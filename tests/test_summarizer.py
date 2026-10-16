import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.config import Settings
from app.core.summarizer import Summarizer, should_summarize


def state(cid=1):
    return {
        "conversation_id": cid,
        "summary": "",
        "summary_upto_msg_id": 0,
        "layer1_from_msg_id": 2,
        "messages": [
            HumanMessage(content="订单1001退货", id="db-1"),
            AIMessage(content="请提供原因"),
            HumanMessage(content="质量问题", id="db-3"),
        ],
    }


def test_summary_trigger_is_layer2_tokens_only():
    assert should_summarize(state(), 1, len)
    assert not should_summarize(state(), 2, len)
    assert not should_summarize({**state(), "layer1_from_msg_id": 0}, 0, len)


async def test_summary_tasks_debounce_and_commit_only_after_success():
    gate = asyncio.Event()
    commits = []
    row = SimpleNamespace(id=1, role="user", content="订单1001退货", tool_calls=None)

    class Repo:
        async def list_dialog_messages(self, cid):
            return [row]

        async def append_summary_segment(self, *args):
            commits.append(args)
            return 1

    async def summarize(old_summary, dialog):
        await gate.wait()
        return "用户要求订单1001退货。"

    service = Summarizer(
        Repo(), None, Settings(_env_file=None), len, SimpleNamespace(layer2=1), summarize=summarize
    )
    service.maybe_schedule_summary(state())
    service.maybe_schedule_summary(state())
    assert len(service.tasks) == 1 and not commits
    gate.set()
    await asyncio.gather(*service.tasks.values())
    await asyncio.sleep(0)
    assert len(commits) == 1 and not service.tasks
    assert commits[0][1:3] == (1, 2)
    await service.close()


async def test_summary_failure_no_advance_and_next_turn_retries(caplog):
    class Repo:
        async def list_dialog_messages(self, cid):
            return [SimpleNamespace(id=1, role="user", content="订单", tool_calls=None)]

        async def append_summary_segment(self, *args):
            pytest.fail("失败不得写摘要边界")

    async def fail(*args):
        raise ValueError("invalid JSON")

    service = Summarizer(
        Repo(), None, Settings(_env_file=None), len, SimpleNamespace(layer2=1), summarize=fail
    )
    for _ in range(2):
        service.maybe_schedule_summary(state())
        await asyncio.gather(*service.tasks.values())
        await asyncio.sleep(0)
        assert not service.tasks
    assert "summary_failed" in caplog.text
    await service.close()
