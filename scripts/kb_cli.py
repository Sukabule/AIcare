"""Shared implementations used by make and browser jobs."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import Settings  # noqa: E402
from app.core.llm import create_chat_model  # noqa: E402
from app.db.base import Database  # noqa: E402
from app.db.models import Conversation  # noqa: E402
from app.db.repository import Repository  # noqa: E402
from app.kb.mining import mine  # noqa: E402
from app.kb.service import KnowledgeService  # noqa: E402
from app.kb.sources import ROOT, materials  # noqa: E402


async def execute(command):
    if command == "preview":
        return [
            {"name": m["name"], "count": m.get("count"), "chunks": m["chunks"]} for m in materials()
        ]
    settings = Settings()
    db = Database(settings.database_url.get_secret_value())
    kb = KnowledgeService(settings, db.sessions)
    try:
        if command == "build":
            result = []
            async with kb.repository.lock():
                for material in materials():
                    if material.get("error"):
                        raise ValueError(material["error"])
                    result.append(
                        {"name": material["name"], **await kb.repository.ingest(material["chunks"])}
                    )
            return result
        if command == "vectorize":
            return {"vectorized": await kb.vectorize()}
        if command == "reset":
            await kb.reset()
            return {"reset": True}
        if command == "mine":
            model = create_chat_model(settings)
            try:
                async with kb.repository.lock():
                    return await mine(kb.repository, model)
            finally:
                await model.root_async_client.close()
                model.root_client.close()
        if command == "seed":
            repo = Repository(db.sessions)
            samples = json.loads((ROOT / "tests/data/mining_samples.json").read_text("utf-8"))
            inserted = 0
            async with kb.repository.lock():
                for index, sample in enumerate(samples):
                    user = f"ch03-seed-{index}"
                    async with db.sessions() as session:
                        if await session.scalar(
                            select(Conversation.id).where(Conversation.user_id == user).limit(1)
                        ):
                            continue
                    conv = await repo.get_or_create(user, None)
                    for message in sample["messages"]:
                        await repo.append(conv.id, message["role"], message["content"])
                    inserted += 1
            return {"seeded": inserted}
        if command == "eval-retrieval":
            samples = json.loads((ROOT / "tests/data/retrieval_samples.json").read_text("utf-8"))
            results = []
            for sample in samples:
                hits = await kb.retrieval.search_knowledge(sample["query"], 5)
                passed = (
                    any(sample["expected"] in h["question"] for h in hits)
                    if sample["expected"]
                    else not hits
                )
                results.append({**sample, "passed": passed, "hits": hits})
            report_path = ROOT / "data/ch03/reports/retrieval_eval.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
            print(json.dumps(results, ensure_ascii=False, indent=2))
            if not all(r["passed"] for r in results):
                raise ValueError("Retrieval evaluation failed")
            return {"passed": len(results), "total": len(results)}
    finally:
        await db.close()


def main(default=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        nargs="?" if default else None,
        default=default,
        choices=["preview", "build", "vectorize", "reset", "mine", "seed", "eval-retrieval"],
    )
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(execute(args.command)), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"Job failed: {type(exc).__name__}. Pending records remain available for retry.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
