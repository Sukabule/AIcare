import math

from langchain_openai import OpenAIEmbeddings

from app.config import Settings


class Embeddings:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = OpenAIEmbeddings(
            model=self.settings.embed_model,
            base_url=self.settings.embed_base_url,
            api_key=self.settings.embed_api_key,
            check_embedding_ctx_length=False,
            request_timeout=45,
            max_retries=1,
        )
        try:
            result = await model.aembed_documents(texts)
            if len(result) != len(texts) or any(
                len(v) != 1024 or not all(math.isfinite(x) for x in v) for v in result
            ):
                raise ValueError("BGE-M3 必须返回 1024 维有效向量")
            return result
        finally:
            await model.async_client._client.close()
            model.client._client.close()


async def embed_texts(texts: list[str]) -> list[list[float]]:
    return await Embeddings(Settings()).embed_texts(texts)
