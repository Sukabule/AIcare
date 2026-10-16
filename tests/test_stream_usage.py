import json

import httpx
import pytest
from langchain_core.callbacks import BaseCallbackHandler

from app.core.llm import UsageSafeChatOpenAI


def wire(mode):
    chunks = []
    for index, text in enumerate(["", "你", "好", None]):
        data = dict(
            id="usage-test",
            object="chat.completion.chunk",
            created=0,
            model="test",
            choices=[
                dict(index=0, delta={"content": text}, finish_reason="stop" if index == 3 else None)
            ],
        )
        if mode == "cumulative" or mode == "finish_only" and index == 3:
            out = min(index, 2)
            data["usage"] = dict(
                prompt_tokens=16,
                completion_tokens=out,
                total_tokens=16 + out,
                prompt_tokens_details={"cached_tokens": 7},
            )
        chunks.append(data)
    if mode != "finish_only":
        chunks.append(
            dict(
                id="usage-test",
                object="chat.completion.chunk",
                created=0,
                model="test",
                choices=[],
                usage=dict(
                    prompt_tokens=16,
                    completion_tokens=2,
                    total_tokens=18,
                    prompt_tokens_details={"cached_tokens": 7},
                ),
            )
        )
    return "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"


def upstream(mode):
    def handler(request):
        body = json.loads(request.content)
        assert body["stream_options"]["include_usage"] is True
        return httpx.Response(200, text=wire(mode), headers={"Content-Type": "text/event-stream"})

    return handler


@pytest.mark.parametrize("mode", ["cumulative", "terminal_only", "finish_only"])
async def test_async_stream_usage_is_final_total_once_without_losing_text(mode):
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream(mode))) as http:
        model = UsageSafeChatOpenAI(
            model="test",
            api_key="test-only",
            base_url="https://upstream.invalid/v1",
            http_async_client=http,
            stream_usage=True,
            use_responses_api=False,
            max_retries=0,
        )
        chunks = [chunk async for chunk in model.astream("你好")]
        combined = chunks[0]
        for chunk in chunks[1:]:
            combined += chunk
        assert combined.text == "你好"
        assert combined.usage_metadata["input_tokens"] == 16
        assert combined.usage_metadata["output_tokens"] == 2
        assert combined.usage_metadata["total_tokens"] == 18
        assert combined.usage_metadata["input_token_details"]["cache_read"] == 7


@pytest.mark.parametrize("mode", ["cumulative", "terminal_only", "finish_only"])
def test_sync_stream_uses_same_final_total(mode):
    with httpx.Client(transport=httpx.MockTransport(upstream(mode))) as http:
        model = UsageSafeChatOpenAI(
            model="test",
            api_key="test-only",
            base_url="https://upstream.invalid/v1",
            http_client=http,
            stream_usage=True,
            use_responses_api=False,
            max_retries=0,
        )
        chunks = list(model.stream("你好"))
        combined = chunks[0]
        for chunk in chunks[1:]:
            combined += chunk
        assert combined.text == "你好" and combined.usage_metadata["total_tokens"] == 18


async def test_tool_chunks_and_callback_accounting_survive_usage_normalization():
    captured = []

    class Capture(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            captured.append(response.generations[0][0].message)

    def handler(request):
        assert json.loads(request.content)["tools"][0]["function"]["name"] == "query_order"
        packets = [
            json.loads(part.removeprefix("data: "))
            for part in wire("cumulative").split("\n\n")
            if part.startswith("data: {")
        ]
        packets[1]["choices"][0]["delta"] = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "query_order", "arguments": '{"order_'},
                }
            ]
        }
        packets[2]["choices"][0]["delta"] = {
            "tool_calls": [{"index": 0, "function": {"arguments": 'id":"1001"}'}}]
        }
        body = "".join("data: " + json.dumps(p) + "\n\n" for p in packets) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    schema = {
        "type": "function",
        "function": {
            "name": "query_order",
            "description": "查订单",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        model = UsageSafeChatOpenAI(
            model="test",
            api_key="test-only",
            base_url="https://upstream.invalid/v1",
            http_async_client=http,
            stream_usage=True,
            use_responses_api=False,
            max_retries=0,
        )
        async for _ in model.bind_tools([schema]).astream(
            "查订单", config={"callbacks": [Capture()]}
        ):
            pass
    assert captured[0].tool_calls[0]["args"] == {"order_id": "1001"}
    assert captured[0].usage_metadata["total_tokens"] == 18
