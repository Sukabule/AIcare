import asyncio
from collections.abc import Callable
from functools import lru_cache

import tiktoken
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately

TokenCounter = Callable[[list[BaseMessage]], int]


class ConversationTooLong(ValueError):
    pass


def trim_history(history, system, message, *, max_tokens=2000, token_counter=None):
    """Keep the system/current input plus a suffix of complete human/AI turns."""
    counter = token_counter or make_token_counter()
    current = HumanMessage(content=message)
    mandatory = counter([system, current])
    if mandatory > max_tokens:
        raise ConversationTooLong("system prompt 与当前问题超过 token 预算，请缩短问题或调大预算。")
    trimmed = trim_messages(
        [system, *history],
        max_tokens=max_tokens - mandatory + counter([system]),
        token_counter=counter,
        strategy="last",
        include_system=True,
        start_on="human",
        end_on="ai",
        allow_partial=False,
    )
    return [*(trimmed or [system]), current]


def make_token_counter(encoding_name: str = "cl100k_base") -> TokenCounter:
    # Encoding is explicit, so arbitrary upstream model IDs never require an alias.
    # A different provider tokenizer / message envelope can yield different usage.
    @lru_cache(maxsize=1)
    def encoding():
        return tiktoken.get_encoding(encoding_name)

    def count(messages: list[BaseMessage]) -> int:
        tokenizer = encoding()
        return 3 + sum(
            4 + len(tokenizer.encode(str(message.content), disallowed_special=()))
            for message in messages
        )

    return count


def approximate_counter(chars_per_token=1.2):
    def count(messages):
        return count_tokens_approximately(messages, chars_per_token=chars_per_token)

    return count


def db_id(message):
    value = message.id or ""
    return int(value[3:]) if value.startswith("db-") and value[3:].isdigit() else 0


def split_layers(messages, summary_upto_msg_id=0, layer1_from_msg_id=0):
    """Split only at user anchors, keeping each tool call and result in its turn."""
    rows = list(messages)
    anchors = [
        (i, db_id(m)) for i, m in enumerate(rows) if isinstance(m, HumanMessage) and db_id(m)
    ]
    if not anchors:
        return [], rows
    if summary_upto_msg_id:
        start = next((i for i, ident in anchors if ident > summary_upto_msg_id), None)
        if start is None:
            # A fully covered anchored history has no window. Unanchored legacy data
            # instead takes the fallback above.
            return [], []
        rows = rows[start:]
    if not layer1_from_msg_id:
        return [], rows
    index = next(
        (
            i
            for i, m in enumerate(rows)
            if isinstance(m, HumanMessage) and db_id(m) > layer1_from_msg_id
        ),
        None,
    )
    if index is None:
        return rows, []
    return rows[:index], rows[index:]


def compress_reply(text, keep_chars=60):
    return text if len(text) <= keep_chars else text[:keep_chars] + "…(略)"


def compress_tool_result(name, content, *, keep_tokens=200, token_counter=None):
    counter = token_counter or approximate_counter()
    if counter([ToolMessage(content=content, tool_call_id="estimate")]) <= keep_tokens:
        return content
    return f"(已调用 {name},结果从略)"


def to_layer2(messages, *, keep_chars=60, tool_keep_tokens=200, token_counter=None):
    names, result = {}, []
    for message in messages:
        if isinstance(message, AIMessage):
            names.update({c["id"]: c["name"] for c in message.tool_calls})
            result.append(
                message.model_copy(update={"content": compress_reply(message.text, keep_chars)})
            )
        elif isinstance(message, ToolMessage):
            result.append(
                message.model_copy(
                    update={
                        "content": compress_tool_result(
                            message.name or names.get(message.tool_call_id, "工具"),
                            message.content,
                            keep_tokens=tool_keep_tokens,
                            token_counter=token_counter,
                        )
                    }
                )
            )
        else:
            result.append(message)
    return result


def close_unfinished_history(messages):
    """Make abandoned/failed turns protocol-valid without altering audit history."""
    result, pending = [], {}

    def close_pending():
        for call_id in pending:
            result.append(
                ToolMessage(
                    content="上一轮工具调用未完成，未取得可用结果。",
                    tool_call_id=call_id,
                    name=pending[call_id],
                )
            )
        pending.clear()

    for message in messages:
        if isinstance(message, ToolMessage):
            if message.tool_call_id in pending:
                result.append(message)
                pending.pop(message.tool_call_id)
            continue
        close_pending()
        result.append(message)
        if isinstance(message, AIMessage):
            pending.update({call["id"]: call["name"] for call in message.tool_calls})
    close_pending()
    return result


def build_window(
    messages,
    summary_upto_msg_id=0,
    layer1_from_msg_id=0,
    max_tokens=None,
    *,
    token_counter=None,
    keep_chars=60,
    tool_keep_tokens=200,
):
    counter = token_counter or approximate_counter()
    middle, recent = split_layers(messages, summary_upto_msg_id, layer1_from_msg_id)
    rows = [
        *to_layer2(
            middle, keep_chars=keep_chars, tool_keep_tokens=tool_keep_tokens, token_counter=counter
        ),
        *recent,
    ]
    rows = close_unfinished_history(rows)
    if max_tokens is None:
        return rows
    return trim_messages(
        rows,
        max_tokens=max_tokens,
        token_counter=counter,
        strategy="last",
        start_on="human",
        allow_partial=False,
    )


def layer_tokens(
    messages,
    summary_upto_msg_id=0,
    layer1_from_msg_id=0,
    *,
    token_counter=None,
    keep_chars=60,
    tool_keep_tokens=200,
):
    counter = token_counter or approximate_counter()
    middle, recent = split_layers(messages, summary_upto_msg_id, layer1_from_msg_id)
    compressed = to_layer2(
        middle, keep_chars=keep_chars, tool_keep_tokens=tool_keep_tokens, token_counter=counter
    )
    return counter(compressed) if compressed else 0, counter(recent) if recent else 0


def next_layer1_from(messages, summary_upto_msg_id, layer1_budget, *, token_counter=None):
    counter = token_counter or approximate_counter()
    _, rows = split_layers(messages, summary_upto_msg_id)
    indices = [i for i, m in enumerate(rows) if isinstance(m, HumanMessage) and db_id(m)]
    if not indices:
        return 0
    first = indices[-1]  # Always keep the newest entire turn, even if it alone is large.
    for index in reversed(indices[:-1]):
        if counter(rows[index:]) > layer1_budget:
            break
        first = index
    # Closed upper boundary immediately before the first retained USER. This also
    # includes the previous turn's final assistant in the DB summary range.
    return max(summary_upto_msg_id, db_id(rows[first]) - 1) if first else summary_upto_msg_id


def summary_line(summary):
    return f"(早前对话摘要:{summary})" if summary else ""


def summary_system(summary):
    return SystemMessage(content=summary_line(summary)) if summary else None


class SessionMemory:
    """Process-local full turns; callers hold session_lock across generation + commit."""

    def __init__(self, max_tokens: int = 2000, token_counter: TokenCounter | None = None):
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = max_tokens
        self.token_counter = token_counter or make_token_counter()
        self._sessions: dict[str, list[BaseMessage]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def session_lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    def history(self, session_id: str) -> list[BaseMessage]:
        return [message.model_copy(deep=True) for message in self._sessions.get(session_id, [])]

    def append_turn(self, session_id: str, human: str, assistant: str) -> None:
        self._sessions.setdefault(session_id, []).extend(
            [HumanMessage(content=human), AIMessage(content=assistant)]
        )

    def messages_for(
        self, session_id: str, system: SystemMessage, message: str
    ) -> list[BaseMessage]:
        current = HumanMessage(content=message)
        mandatory_cost = self.token_counter([system, current])
        if mandatory_cost > self.max_tokens:
            raise ConversationTooLong(
                "system prompt 与当前问题超过 token 预算，请缩短问题或调大预算。"
            )
        current_cost = mandatory_cost - self.token_counter([system])
        trimmed = trim_messages(
            [system, *self.history(session_id)],
            max_tokens=self.max_tokens - current_cost,
            token_counter=self.token_counter,
            strategy="last",
            include_system=True,
            start_on="human",
            end_on="ai",
            allow_partial=False,
        )
        # With no completed turns, end_on='ai' may produce an empty result.
        return [*(trimmed or [system]), current]
