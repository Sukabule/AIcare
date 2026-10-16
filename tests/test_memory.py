import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.memory import ConversationTooLong, SessionMemory


def counter(messages):
    return sum(len(message.content) for message in messages)


def memory(budget=12):
    return SessionMemory(max_tokens=budget, token_counter=counter)


def test_stores_complete_rounds_and_isolates_sessions():
    store = memory()
    store.append_turn("a", "hi", "ok")
    assert store.history("a") == [HumanMessage(content="hi"), AIMessage(content="ok")]
    assert store.history("b") == []
    snapshot = store.history("a")
    snapshot[0].content = "changed"
    snapshot.clear()
    assert store.history("a")[0].content == "hi"


def test_trim_preserves_system_current_and_recent_complete_round():
    store = memory(9)
    store.append_turn("a", "old", "old")
    store.append_turn("a", "new", "yes")
    result = store.messages_for("a", SystemMessage(content="S"), "Q")
    assert [m.content for m in result] == ["S", "new", "yes", "Q"]
    assert counter(result) <= 9


def test_trim_never_keeps_orphan_assistant_or_partial_message():
    store = memory(5)
    store.append_turn("a", "long question", "ok")
    assert [m.content for m in store.messages_for("a", SystemMessage(content="S"), "Q")] == [
        "S",
        "Q",
    ]


def test_exact_boundary_keeps_whole_round():
    store = memory(6)
    store.append_turn("a", "hi", "ok")
    assert len(store.messages_for("a", SystemMessage(content="S"), "Q")) == 4


def test_oversize_current_input_is_rejected_without_mutation():
    store = memory(2)
    with pytest.raises(ConversationTooLong):
        store.messages_for("a", SystemMessage(content="SS"), "Q")
    assert store.history("a") == []


async def test_session_lock_serializes_only_same_session():
    store = memory()
    async with store.session_lock("a"):
        task = asyncio.create_task(store.session_lock("a").acquire())
        await asyncio.sleep(0)
        assert not task.done()
        async with store.session_lock("b"):
            pass
    await asyncio.wait_for(task, timeout=1)
    store.session_lock("a").release()
