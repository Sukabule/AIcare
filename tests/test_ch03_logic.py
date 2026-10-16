import pytest

from app.core.retrieval import Retrieval
from app.kb.dedup import fingerprint, normalize
from app.kb.documents import build_chunks
from app.kb.dualwrite import vectorize_pending


def test_headings_metadata_and_key_clause():
    chunks = build_chunks("# 商店\n## 运费 [关键]\n满99元包邮。", "policy")
    assert len(chunks) == 1
    assert chunks[0]["questions"] == "运费"
    assert chunks[0]["category"] == "商店"
    assert chunks[0]["is_key_clause"]
    assert chunks[0]["section_path"] == "商店 / 运费"


def test_tables_repeat_headers_and_preserve_rows():
    rows = [f"| 商品{i} | 规格说明{i} |" for i in range(30)]
    chunks = build_chunks(
        "# FAQ\n## 规格\n| 名称 | 说明 |\n| --- | --- |\n" + "\n".join(rows), "faq", 180, 30
    )
    assert len(chunks) > 1
    for chunk in chunks:
        assert "| 名称 | 说明 |\n| --- | --- |" in chunk["answer"]
    for row in rows:
        assert sum(row in c["answer"].splitlines() for c in chunks) == 1


def test_long_text_overlap_has_complete_sentences():
    text = "".join(f"第{i}项条款需要保留完整句子。" for i in range(35))
    chunks = build_chunks("# 政策\n## 条款\n" + text, "policy", 140, 40)
    assert len(chunks) > 1
    assert all(len(c["answer"]) <= 140 for c in chunks)
    assert all(c["answer"].startswith("第") for c in chunks)
    assert all(c["answer"].endswith("。") for c in chunks)
    assert any(chunks[0]["answer"].split("。")[-2] in c["answer"] for c in chunks[1:2])
    assert all(
        f"第{i}项条款需要保留完整句子。" in "".join(c["answer"] for c in chunks) for i in range(35)
    )


def test_text_dedup_does_not_discard_different_bodies():
    assert normalize(" ＡＢＣ，邮费？！ ") == normalize("abc邮费")
    assert fingerprint("标题", "正文1") != fingerprint("标题", "正文2")
    assert fingerprint("标题！", "正文。") == fingerprint("标题", "正文")


class FakeRepo:
    def __init__(self):
        self.rows = [
            dict(id=i, category="政策", questions="运费", answer=str(i)) for i in (1, 2, 3)
        ]
        self.done = set()
        self.fail = True

    async def pending(self, limit):
        return [r for r in self.rows if r["id"] not in self.done][:limit]

    async def mark_done(self, ids):
        if self.fail and 2 in ids:
            self.fail = False
            raise RuntimeError("crash after upsert")
        self.done.update(ids)


class FakeEmbedding:
    async def embed_texts(self, texts):
        return [[1.0, 0.0] for _ in texts]


class FakeVectors:
    def __init__(self):
        self.rows = {}

    async def upsert(self, rows):
        self.rows.update({r["id"]: r for r in rows})

    async def search(self, vector, top_k):
        return [
            dict(id=1, question="运费", answer="包邮", score=0.8),
            dict(id=2, question="无关", answer="", score=0.1),
        ][:top_k]


async def test_resume_after_vector_write_before_mysql_done():
    repo, vectors = FakeRepo(), FakeVectors()
    with pytest.raises(RuntimeError):
        await vectorize_pending(repo, FakeEmbedding(), vectors, batch_size=1)
    assert repo.done == {1}
    assert set(vectors.rows) == {1, 2}
    assert await vectorize_pending(repo, FakeEmbedding(), vectors, batch_size=1) == 2
    assert repo.done == {1, 2, 3}
    assert len(vectors.rows) == 3
    assert await vectorize_pending(repo, FakeEmbedding(), vectors) == 0


async def test_dense_threshold_and_empty_hits():
    retrieval = Retrieval(FakeEmbedding(), FakeVectors(), min_score=0.5)
    assert [h["id"] for h in await retrieval.search_knowledge("邮费", 5)] == [1]
    retrieval.min_score = 0.9
    assert await retrieval.search_knowledge("星际旅行", 5) == []
