import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.core.llm import create_chat_model  # noqa: E402
from app.kb.mining import extract_pairs  # noqa: E402
from app.kb.sources import ROOT  # noqa: E402


async def main():
    model = create_chat_model(Settings())
    results = []
    try:
        samples = json.loads((ROOT / "tests/data/mining_samples.json").read_text("utf-8"))
        for sample in samples:
            pairs = await extract_pairs(model, sample["messages"])
            text = " ".join(p["answer"] for p in pairs)
            passed = not pairs if sample["empty"] else all(v in text for v in sample["expected"])
            results.append({"input": sample["messages"], "pairs": pairs, "passed": passed})
        report_path = ROOT / "data/ch03/reports/mining_eval.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps({"passed": sum(r["passed"] for r in results), "total": len(results)}))
        return all(r["passed"] for r in results)
    finally:
        await model.root_async_client.close()
        model.root_client.close()


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
    except Exception as exc:
        print("Evaluation failed:", type(exc).__name__)
        success = False
    raise SystemExit(0 if success else 1)
