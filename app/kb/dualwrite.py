async def write_pending(repository, chunks):
    return await repository.ingest(chunks)


async def vectorize_pending(repository, embeddings, vectors, batch_size=16) -> int:
    completed = 0
    while rows := await repository.pending(batch_size):
        texts = ["\n".join((r["category"], r["questions"], r["answer"])) for r in rows]
        values = await embeddings.embed_texts(texts)
        if len(values) != len(rows):
            raise ValueError("嵌入返回数量与知识块数量不符")
        await vectors.upsert(
            [
                dict(
                    id=r["id"],
                    vector=v,
                    question=r["questions"],
                    answer=r["answer"],
                    category=r["category"],
                    section_path=r.get("section_path") or "",
                    content_type=r.get("content_type") or "",
                    is_key_clause=bool(r.get("is_key_clause")),
                    text="\n".join((r["category"], r["questions"], r["answer"])),
                )
                for r, v in zip(rows, values, strict=True)
            ]
        )
        await repository.mark_done([r["id"] for r in rows])
        completed += len(rows)
    return completed
