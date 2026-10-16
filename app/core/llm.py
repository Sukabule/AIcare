from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from app.config import Settings


class ModelConfigurationError(ValueError):
    pass


class UsageSafeChatOpenAI(ChatOpenAI):
    """Emit OpenAI's cumulative usage once, even if upstream repeats it per delta.

    Message chunks add usage, whereas each wire usage object is a request total.
    Keep text/tool chunks streaming immediately and append the last usage at EOF.
    Each stream owns its accumulator; concurrent sessions cannot mix accounting.
    """

    @staticmethod
    def without_usage(chunk):
        return chunk.model_copy(
            update={"message": chunk.message.model_copy(update={"usage_metadata": None})}
        )

    def _stream(self, *args, **kwargs):
        usage = None
        for chunk in super()._stream(*args, **kwargs):
            if chunk.message.usage_metadata is not None:
                usage = chunk.message.usage_metadata
                chunk = self.without_usage(chunk)
            yield chunk
        if usage is not None:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=usage))

    async def _astream(self, *args, **kwargs):
        usage = None
        async for chunk in super()._astream(*args, **kwargs):
            if chunk.message.usage_metadata is not None:
                usage = chunk.message.usage_metadata
                chunk = self.without_usage(chunk)
            yield chunk
        if usage is not None:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=usage))


def create_chat_model(settings: Settings) -> ChatOpenAI:
    if not settings.chat_api_key.get_secret_value() or not settings.chat_model:
        raise ModelConfigurationError("请在 .env 配置 CHAT_BASE_URL、CHAT_MODEL 和 CHAT_API_KEY。")
    return UsageSafeChatOpenAI(
        base_url=settings.chat_base_url,
        model=settings.chat_model,
        api_key=settings.chat_api_key,
        timeout=settings.chat_timeout_seconds,
        max_retries=0,
        # Explicitly use Chat Completions for OpenAI-compatible upstreams.
        use_responses_api=False,
        stream_usage=True,
    )
