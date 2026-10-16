from langchain_core.messages import AIMessage

from app.core.agent import AgentService
from app.schemas import ChatRequest
from app.tools.registry import ToolRegistry


class Evidence:
    async def answer_evidence(self, query, conversation_id, raw_question):
        assert raw_question == "完整原始问题"
        return {
            "refuse": False,
            "hits": [],
            "citations": [
                {"id": 8, "number": 1, "section_path": "原文", "answer": "正文", "question": "q"}
            ],
        }


async def test_multiple_tool_citations_numbered_globally_before_stream(repo, model):
    model.first = AIMessage(
        content="",
        tool_calls=[
            {"name": "query_faq", "args": {"keyword": q}, "id": q, "type": "tool_call"}
            for q in ["first", "second"]
        ],
    )
    registry = ToolRegistry(repo, Evidence())
    agent = AgentService(repo, model, registry, token_counter=len)
    events = [
        event
        async for event in agent.stream_agent_turn(ChatRequest(user_id="u", message="完整原始问题"))
    ]
    citation = next(e for e in events if e.get("event") == "citations")
    assert [c["number"] for c in citation["citations"]] == [1, 2]
    assert events.index(citation) < next(i for i, e in enumerate(events) if "delta" in e)


async def test_refusal_bypasses_final_generation(repo, model):
    class Refuse:
        async def answer_evidence(self, *args):
            return {"refuse": True, "message": "没有依据", "citations": [], "hits": []}

    model.first = AIMessage(
        content="",
        tool_calls=[
            {"name": "query_faq", "args": {"keyword": "q"}, "id": "q", "type": "tool_call"}
        ],
    )
    agent = AgentService(repo, model, ToolRegistry(repo, Refuse()), token_counter=len)
    result = await agent.run_agent_turn(ChatRequest(user_id="u", message="q"))
    assert result.answer == "没有依据"
    assert [kind for kind, _ in model.calls] == ["bound"]


async def test_direct_store_fact_is_replaced_before_memory_and_pooled(repo, model, db):
    from sqlalchemy import select

    from app.core.rag import REFUSAL
    from app.db.models import LowConfidenceQuestion
    from app.db.rag_repository import RagRepository

    model.first = AIMessage(content="我们不销售活体宠物")

    async def guard(cid, query, reply):
        await RagRepository(db.sessions).low_confidence(
            cid, query, "self_check", "未经知识核验的店铺事实"
        )
        return REFUSAL

    agent = AgentService(
        repo, model, ToolRegistry(repo), token_counter=len, direct_answer_guard=guard
    )
    result = await agent.run_agent_turn(ChatRequest(user_id="u", message="能在这买猫吗"))
    assert result.answer == REFUSAL
    history = await repo.history(result.conversation_id)
    assert history[-1].content == REFUSAL
    assert all("不销售活体" not in (row.content or "") for row in history)
    async with db.sessions() as session:
        row = await session.scalar(select(LowConfidenceQuestion))
        assert row.source == "self_check" and row.raw_question == "能在这买猫吗"
