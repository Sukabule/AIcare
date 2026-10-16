from pydantic import BaseModel, ConfigDict, Field

from app.core.intent import history_text
from app.core.prompts import COREF_REWRITE_PROMPT


class ResolvedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)


async def resolve_reference(model, query, history):
    value = await model.with_structured_output(
        ResolvedQuery, method="json_schema", strict=True
    ).ainvoke(COREF_REWRITE_PROMPT.format_messages(question=query, history=history_text(history)))
    return ResolvedQuery.model_validate(value).query.strip()
