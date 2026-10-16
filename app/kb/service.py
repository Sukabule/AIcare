import asyncio

from app.core.embeddings import Embeddings
from app.core.rerank import Reranker
from app.core.retrieval import Retrieval
from app.kb.documents import build_chunks
from app.kb.dualwrite import vectorize_pending
from app.kb.milvus_client import Vectors
from app.kb.repository import KnowledgeRepository
from app.kb.sources import materials


class VectorizationFailed(RuntimeError):
    def __init__(self, result):
        self.result = result


class KnowledgeService:
    def __init__(self, settings, sessions):
        self.settings = settings
        self.repository = KnowledgeRepository(sessions)
        self.embeddings = Embeddings(settings)
        self.vectors = Vectors(settings)
        self.retrieval = Retrieval(
            self.embeddings,
            self.vectors,
            settings.retrieval_min_score,
            Reranker(settings),
            "hybrid_rerank",
        )

    async def overview(self):
        try:
            sources = materials(self.settings.kb_source_dir)
            source_error = None
        except (OSError, ValueError):
            sources = []
            source_error = "知识材料目录不可读，请检查 KB_SOURCE_DIR"
        db, count = await asyncio.gather(
            self.repository.overview(), self.vectors.count(), return_exceptions=True
        )
        db_ok, vector_ok = not isinstance(db, Exception), not isinstance(count, Exception)
        return dict(
            mysql=db if db_ok else None,
            milvus=count if vector_ok else None,
            mysql_error=None if db_ok else "MySQL 读不到，请检查数据库服务",
            milvus_error=None if vector_ok else "Milvus 读不到，请检查向量服务",
            consistency=(
                "unavailable"
                if not (db_ok and vector_ok)
                else "consistent"
                if db["done"] == count and not db["pending"]
                else "pending"
            ),
            materials=sources,
            source_dir=str(self.settings.kb_source_dir),
            source_error=source_error,
        )

    async def ingest(self, text, kind, vectorize=False):
        chunks = build_chunks(text, kind)
        async with self.repository.lock():
            result = await self.repository.ingest(chunks)
            if vectorize:
                try:
                    result["vectorized"] = await vectorize_pending(
                        self.repository, self.embeddings, self.vectors
                    )
                except Exception as exc:
                    raise VectorizationFailed(result) from exc
        return result

    async def vectorize(self):
        async with self.repository.lock():
            return await vectorize_pending(self.repository, self.embeddings, self.vectors)

    async def reset(self):
        async with self.repository.lock():
            # If Milvus cannot be reached, leave the authority intact for retry.
            await self.vectors.reset()
            await self.repository.reset()
