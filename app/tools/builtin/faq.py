from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool

from app.tools.business import ShortText
from app.tools.registry import ToolSpec


def register(registry):
    retrieval, top_k = registry.retrieval, registry.top_k

    @tool
    async def query_faq(
        keyword: ShortText,
        conversation_id: Annotated[int, InjectedToolArg] = 0,
        raw_question: Annotated[str, InjectedToolArg] = "",
    ) -> dict:
        """语义检索商店知识库中的政策、配送费用和商品常见问题。传入用户的实际问题。"""
        if retrieval is not None and hasattr(retrieval, "answer_evidence"):
            return await retrieval.answer_evidence(keyword, conversation_id, raw_question)
        if retrieval is None:
            from app.core.retrieval import search_knowledge

            matches = await search_knowledge(keyword, top_k)
        else:
            matches = await retrieval.search_knowledge(keyword, top_k)
        hits = [{"question": m["question"], "answer": m["answer"]} for m in matches]
        return {
            "hits": hits,
            "source": "faq",
            "keyword": keyword,
            "found": bool(matches),
            "matches": hits,
            "message": "已找到" if matches else "未找到",
        }

    registry.register(ToolSpec.from_tool(query_faq, timeout=30, inject_conversation=True))
