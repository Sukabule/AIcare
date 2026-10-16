"""Append-only background summaries; never mutate the graph's message channel."""

import asyncio
import json
from contextlib import nullcontext

from pydantic import BaseModel, ConfigDict, Field

from app.core.context_log import logger
from app.core.memory import layer_tokens
from app.core.prompts import SUMMARY_PROMPT


class SummarySegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=200)


async def summarize_dialog(model, old_summary, dialog, config=None):
    chain = model.with_structured_output(SummarySegment, method="json_schema", strict=True)
    if config:
        chain = chain.with_config(config)
    value = await chain.ainvoke(
        SUMMARY_PROMPT.format_messages(
            old_summary=old_summary or "无", dialog=json.dumps(dialog, ensure_ascii=False)
        )
    )
    return SummarySegment.model_validate(value).summary.strip()


def should_summarize(state, budget, counter, *, keep_chars=60, tool_keep_tokens=200):
    middle, _ = layer_tokens(
        state["messages"],
        state.get("summary_upto_msg_id", 0),
        state.get("layer1_from_msg_id", 0),
        token_counter=counter,
        keep_chars=keep_chars,
        tool_keep_tokens=tool_keep_tokens,
    )
    return middle > budget


class Summarizer:
    def __init__(self, repository, model_factory, settings, counter, budget, *, summarize=None):
        self.repository, self.model_factory = repository, model_factory
        self.settings, self.counter, self.budget = settings, counter, budget
        self.summarize = summarize
        self.tasks = {}
        self.observability = None

    def maybe_schedule_summary(self, state):
        try:
            cid = state["conversation_id"]
            if cid in self.tasks or not should_summarize(
                state,
                self.budget.layer2,
                self.counter,
                keep_chars=self.settings.layer2_reply_keep_chars,
                tool_keep_tokens=self.settings.layer2_tool_keep_tokens,
            ):
                return
            upto, previous = state.get("layer1_from_msg_id", 0), state.get("summary_upto_msg_id", 0)
            if upto <= previous:
                return
            task = asyncio.create_task(
                self._run(
                    cid,
                    previous,
                    upto,
                    state.get("summary", ""),
                    state.get("trace", {}).get("langfuse_trace_id"),
                ),
                name=f"summary-{cid}",
            )
            self.tasks[cid] = task
            task.add_done_callback(
                lambda done: self.tasks.pop(cid, None) if self.tasks.get(cid) is done else None
            )
        except Exception:
            logger.exception("summary_schedule_failed")

    async def _run(self, cid, previous, upto, old_summary, trace_id=None):
        try:
            rows = await self.repository.list_dialog_messages(cid)
            dialog = [
                {"role": r.role, "content": r.content or ""}
                for r in rows
                if previous < r.id <= upto and r.content and not r.tool_calls
            ]
            if not dialog:
                return
            logger.info("summary_start cid=%s from=%s upto=%s", cid, previous + 1, upto)
            scope = (
                self.observability.summary_scope(cid, trace_id)
                if self.observability
                else nullcontext(None)
            )
            with scope as config:
                content = (
                    await self.summarize(old_summary, dialog)
                    if self.summarize
                    else await summarize_dialog(self.model_factory(), old_summary, dialog, config)
                )
            content = SummarySegment(summary=content).summary
            seq = await self.repository.append_summary_segment(cid, previous + 1, upto, content)
            logger.info("summary_done cid=%s seq=%s upto=%s summary=%s", cid, seq, upto, content)
        except Exception:
            logger.exception("summary_failed cid=%s; retry only after a later turn", cid)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
