from langchain_text_splitters import MarkdownHeaderTextSplitter

from app.kb.chunking import split_body


def build_chunks(
    markdown: str, content_type: str = "manual", chunk_size: int = 700, chunk_overlap: int = 100
) -> list[dict]:
    if chunk_size <= chunk_overlap or chunk_size < 100 or chunk_overlap < 0:
        raise ValueError("切块长度至少 100 且必须大于重叠长度")
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#" * i, f"h{i}") for i in range(1, 7)], strip_headers=True
    )
    chunks = []
    for section in splitter.split_text(markdown):
        titles = [section.metadata[f"h{i}"] for i in range(1, 7) if f"h{i}" in section.metadata]
        key = any("[关键]" in t for t in titles)
        titles = [t.replace("[关键]", "").strip() for t in titles] or ["手工录入"]
        for body, feature in split_body(section.page_content, chunk_size, chunk_overlap):
            chunks.append(
                dict(
                    category=" / ".join(titles[:-1]) or titles[0],
                    questions=titles[-1],
                    answer=body,
                    section_path=" / ".join(titles),
                    content_type=content_type,
                    is_key_clause=key,
                    feature=feature,
                )
            )
    return chunks
