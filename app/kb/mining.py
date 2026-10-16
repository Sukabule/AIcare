import hashlib
import json

from pydantic import BaseModel, Field

from app.core.prompts import MINING_PROMPT


class Pair(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=12000)


class ExtractedKnowledge(BaseModel):
    pairs: list[Pair] = Field(default_factory=list, max_length=100)


async def extract_pairs(model, conversations):
    structured = model.with_structured_output(ExtractedKnowledge, method="json_schema")
    prompt = MINING_PROMPT.invoke({"conversations": json.dumps(conversations, ensure_ascii=False)})
    result = await structured.ainvoke(prompt)
    return [p.model_dump() for p in result.pairs]


async def mine(repository, model, batch_size=20):
    conversations = await repository.conversations()
    batches, current, length = [], [], 0
    for conversation in conversations:
        size = len(json.dumps(conversation, ensure_ascii=False))
        if current and (len(current) >= batch_size or length + size > 24000):
            batches.append(current)
            current, length = [], 0
        # Bound each source too; avoid silently mining incomplete source dialogue.
        if size > 24000:
            continue
        current.append(conversation)
        length += size
    if current:
        batches.append(current)
    extracted = 0
    for batch in batches:
        canonical = json.dumps(batch, ensure_ascii=False, sort_keys=True)
        batch_no = hashlib.sha256(canonical.encode()).hexdigest()
        if await repository.batch_exists(batch_no):
            continue
        pairs = await extract_pairs(model, batch)
        await repository.stage(
            batch_no, "conversations:" + ",".join(str(c["id"]) for c in batch), pairs
        )
        extracted += len(pairs)
    return {"extracted": extracted, "promoted": await repository.promote(), "batches": len(batches)}
