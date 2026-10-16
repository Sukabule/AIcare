from langchain_core.language_models.fake_chat_models import FakeListChatModel
from test_ch05_graph import runtime

from app.schemas import ChatRequest


async def test_callback_stream_and_checkpoint_message_emit_text_once(repo, db, tmp_path):
    class Streaming(FakeListChatModel):
        def bind_tools(self, tools):
            return self

    model = Streaming(responses=["请描述遇到的问题"])
    graph = await runtime(repo, db, tmp_path, "人工", model)
    try:
        events = [
            e async for e in graph.stream_agent_turn(ChatRequest(user_id="u", message="帮我建工单"))
        ]
        assert "".join(e.get("delta", "") for e in events) == "请描述遇到的问题"
    finally:
        await graph.close()
