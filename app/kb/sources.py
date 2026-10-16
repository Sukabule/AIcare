from pathlib import Path

from app.config import Settings
from app.kb.documents import build_chunks

ROOT = Path(__file__).resolve().parents[2]
SOURCE_TYPES = {
    "returns.md": "policy",
    "manual.md": "manual",
    "faq.md": "faq",
    "returns-policy.md": "policy",
    "after-sales-manual.md": "manual",
    "product-faq.md": "faq",
    "product-specs.md": "spec",
    "member-benefits.md": "policy",
    "billing-shipping.md": "policy",
}


def materials(directory=None):
    directory = Path(directory if directory is not None else Settings().kb_source_dir)
    if not directory.is_absolute():
        directory = ROOT / directory
    if not directory.is_dir():
        raise FileNotFoundError("配置的知识材料目录不存在")
    result = []
    for path in sorted(directory.glob("*.md")):
        filename = path.name
        kind = SOURCE_TYPES.get(filename, "manual")
        text = path.read_text("utf-8")
        chunks = build_chunks(text, kind)
        result.append(
            dict(
                name=filename,
                content_type=kind,
                text=text,
                chunks=chunks,
                count=len(chunks),
                key_clauses=sum(c["is_key_clause"] for c in chunks),
                features=sorted({c["feature"] for c in chunks}),
            )
        )
    return result
