import math

import httpx


class Reranker:
    def __init__(self, settings, transport=None):
        self.url = (settings.rerank_base_url or "https://api.siliconflow.cn/v1").rstrip("/")
        self.model = settings.rerank_model or "BAAI/bge-reranker-v2-m3"
        self.key = (settings.rerank_api_key or settings.siliconflow_api_key).get_secret_value()
        self.transport = transport

    async def rank(self, query, hits, top_k=10):
        if not hits:
            return []
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            response = await client.post(
                self.url + "/rerank",
                headers={"Authorization": "Bearer " + self.key},
                json={
                    "model": self.model,
                    "query": query,
                    "documents": ["\n".join((h.get("question", ""), h["answer"])) for h in hits],
                    "top_n": min(top_k, len(hits)),
                    "return_documents": False,
                },
            )
            response.raise_for_status()
            results = response.json()["results"]
        ranked, seen = [], set()
        for item in results:
            index, score = item["index"], float(item["relevance_score"])
            if (
                type(index) is not int
                or not 0 <= index < len(hits)
                or index in seen
                or not math.isfinite(score)
            ):
                raise ValueError("重排响应包含无效索引或分数")
            seen.add(index)
            ranked.append({**hits[index], "score": score})
        return sorted(ranked, key=lambda h: h["score"], reverse=True)[:top_k]
