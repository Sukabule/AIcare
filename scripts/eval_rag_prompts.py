"""Labeled real-model validation for query rewriting and evidence gates."""

# ruff: noqa: E402
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import Settings
from app.core.evaluation import write_report
from app.core.llm import create_chat_model
from app.core.prompts import (
    DIRECT_ANSWER_CHECK_PROMPT,
    QUERY_UNDERSTANDING_PROMPT,
    SELF_CHECK_PROMPT,
)
from app.core.query_understanding import DirectAnswerCheck, EvidenceCheck, QueryRewrite


async def main():
    settings = Settings()
    model = create_chat_model(settings)
    model.temperature = 0
    model.max_tokens = 2048
    model.extra_body = {"enable_thinking": False}
    samples = json.loads((ROOT / "tests/data/rag_prompt_samples.json").read_text("utf-8"))
    results = []
    semaphore = asyncio.Semaphore(2)

    async def check(kind, sample):
        async with semaphore:
            try:
                async with asyncio.timeout(45):
                    if kind == "rewrite":
                        result = await model.with_structured_output(QueryRewrite).ainvoke(
                            QUERY_UNDERSTANDING_PROMPT.format_messages(query=sample["query"])
                        )
                        text = result.standard_question + " " + result.synonyms
                        passed = all(
                            term.lower() in text.lower() for term in sample["expect_terms"]
                        )
                    elif kind == "self_check":
                        result = await model.with_structured_output(EvidenceCheck).ainvoke(
                            SELF_CHECK_PROMPT.format_messages(
                                query=sample["query"],
                                evidence=json.dumps(sample["evidence"], ensure_ascii=False),
                            )
                        )
                        passed = result.useful == sample["useful"]
                    else:
                        result = await model.with_structured_output(DirectAnswerCheck).ainvoke(
                            DIRECT_ANSWER_CHECK_PROMPT.format_messages(
                                query=sample["query"], answer=sample["answer"]
                            )
                        )
                        passed = result.requires_knowledge == sample["requires_knowledge"]
                item = dict(
                    kind=kind, query=sample["query"], passed=passed, result=result.model_dump()
                )
            except Exception as exc:
                item = dict(
                    kind=kind, query=sample["query"], passed=False, error=type(exc).__name__
                )
            results.append(item)
            print(f"{kind}: {'PASS' if item['passed'] else 'FAIL'}", flush=True)

    try:
        await asyncio.gather(
            *(check(kind, sample) for kind, rows in samples.items() for sample in rows)
        )
        write_report(
            ROOT / "data/ch04/reports/prompt_eval.json",
            dict(total=len(results), passed=sum(r["passed"] for r in results), results=results),
        )
        print(f"Prompt samples: {sum(r['passed'] for r in results)}/{len(results)}", flush=True)
    finally:
        await model.root_async_client.close()
        model.root_client.close()


if __name__ == "__main__":
    asyncio.run(main())
