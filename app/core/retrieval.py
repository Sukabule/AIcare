STRATEGIES = ("vector", "bm25", "hybrid", "hybrid_rerank")


def arrange_evidence(hits):
    ordered = hits[:1] + hits[2:] + hits[1:2]
    return [{**hit, "number": index} for index, hit in enumerate(ordered, 1)]


class Retrieval:
    def __init__(
        self,
        embeddings,
        vectors,
        min_score=0.5,
        reranker=None,
        strategy="vector",
        understanding=None,
    ):
        self.embeddings, self.vectors, self.min_score = embeddings, vectors, min_score
        self.reranker, self.strategy, self.understanding = reranker, strategy, understanding

    async def search_knowledge(
        self,
        query: str,
        top_k: int = 5,
        strategy=None,
        filters=None,
        apply_threshold=True,
        apply_understanding=True,
    ) -> list[dict]:
        strategy = strategy or self.strategy
        if strategy not in STRATEGIES:
            raise ValueError("未知检索策略")
        keyword = query
        if apply_understanding and self.understanding and strategy != "vector":
            keyword = await self.understanding(query)
        if strategy == "bm25":
            return await self.vectors.bm25_search(keyword, top_k, filters)
        vector = (await self.embeddings.embed_texts([query]))[0]
        if strategy == "vector":
            hits = (
                await self.vectors.dense_search(vector, top_k, filters)
                if filters
                else await self.vectors.search(vector, top_k)
            )
            return [h for h in hits if not apply_threshold or h["score"] >= self.min_score]
        hits = await self.vectors.hybrid_search(
            vector, keyword, 50 if strategy == "hybrid_rerank" else top_k, filters
        )
        if strategy == "hybrid_rerank":
            if self.reranker is None:
                raise RuntimeError("重排服务未配置")
            return await self.reranker.rank(query, hits, top_k)
        return hits


async def search_knowledge(query: str, top_k: int = 5) -> list[dict]:
    from app.config import Settings
    from app.core.embeddings import Embeddings
    from app.core.rerank import Reranker
    from app.kb.milvus_client import Vectors

    settings = Settings()
    return await Retrieval(
        Embeddings(settings),
        Vectors(settings),
        settings.retrieval_min_score,
        Reranker(settings),
        "hybrid_rerank",
    ).search_knowledge(query, top_k)
