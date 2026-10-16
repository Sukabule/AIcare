import httpx
import pytest

from app.config import Settings
from app.core.rerank import Reranker
from app.core.retrieval import arrange_evidence
from app.kb.milvus_client import metadata_filter


async def test_rerank_orders_truncates_and_maps_originals():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.3},
                    {"index": 1, "relevance_score": 0.9},
                ]
            },
        )

    ranker = Reranker(Settings(), transport=httpx.MockTransport(handler))
    hits = [
        {"id": 1, "question": "W20", "answer": "2L"},
        {"id": 2, "question": "W40", "answer": "4L"},
    ]
    result = await ranker.rank("W40", hits, 1)
    assert [h["id"] for h in result] == [2]
    assert result[0]["score"] == 0.9
    assert await ranker.rank("anything", [], 10) == []


async def test_rerank_invalid_index_is_not_silently_used():
    ranker = Reranker(
        Settings(),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"results": [{"index": 99, "relevance_score": 0.9}]})
        ),
    )
    with pytest.raises(ValueError):
        await ranker.rank("x", [{"id": 1, "question": "q", "answer": "a"}], 10)


def test_top_two_evidence_at_ends_and_references_are_stable():
    rows = [{"id": i, "question": str(i), "answer": "a", "section_path": "p"} for i in range(4)]
    citations = arrange_evidence(rows)
    assert [r["id"] for r in citations] == [0, 2, 3, 1]
    assert [r["number"] for r in citations] == [1, 2, 3, 4]


def test_filter_parameterization_and_whitelist():
    expression, params = metadata_filter({"category": 'x" or id > 0', "content_type": "spec"})
    assert 'x"' not in expression
    assert params["category"] == 'x" or id > 0'
    with pytest.raises(ValueError):
        metadata_filter({"id > 0": "bad"})


async def test_strategy_branches_expand_only_bm25_and_rerank_after_fusion():
    from app.core.retrieval import Retrieval

    calls = []

    class Embeddings:
        async def embed_texts(self, texts):
            calls.append(("embed", texts))
            return [[1.0]]

    class Vectors:
        async def search(self, vector, top_k):
            calls.append(("vector", top_k))
            return [{"id": 1, "score": 0.9}]

        async def bm25_search(self, text, top_k, filters):
            calls.append(("bm25", text))
            return [{"id": 2, "score": 5}]

        async def hybrid_search(self, vector, text, top_k, filters):
            calls.append(("hybrid", text, top_k))
            return [{"id": 3, "score": 0.02}]

    class Ranker:
        async def rank(self, query, hits, top_k):
            calls.append(("rerank", query, hits[0]["id"]))
            return [{"id": 3, "score": 0.95}]

    async def rewrite(query):
        return "shipping synonyms"

    retrieval = Retrieval(Embeddings(), Vectors(), reranker=Ranker(), understanding=rewrite)
    for strategy, expected in [("vector", 1), ("bm25", 2), ("hybrid", 3), ("hybrid_rerank", 3)]:
        result = await retrieval.search_knowledge("original", 5, strategy=strategy)
        assert result[0]["id"] == expected
    assert all(texts == ["original"] for name, texts, *rest in calls if name == "embed")
    assert ("bm25", "shipping synonyms") in calls
    assert ("hybrid", "shipping synonyms", 50) in calls
    assert calls[-1] == ("rerank", "original", 3)
