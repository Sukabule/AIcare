import json

import httpx
from langchain_openai import ChatOpenAI

from app.core.prompts import EXTRACT_PROMPT
from app.schemas import AfterSalesTicket


async def test_chatopenai_sends_chat_completions_and_forwards_deltas():
    def upstream(request):
        assert request.url == "https://upstream.invalid/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "vendor-real-model"
        assert body["stream"] is True
        assert "tools" not in body
        assert body["messages"][-1]["content"] == "你好"
        chunks = [
            {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "vendor-real-model",
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
            }
            for text in ["你", "好"]
        ]
        data = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        return httpx.Response(
            200, text=data + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        llm = ChatOpenAI(
            model="vendor-real-model",
            base_url="https://upstream.invalid/v1",
            api_key="test-only",
            http_async_client=http,
            stream_usage=False,
            use_responses_api=False,
            max_retries=0,
        )
        chunks = [chunk async for chunk in llm.astream([("human", "你好")])]
    assert "".join(chunk.text for chunk in chunks) == "你好"


async def test_structured_output_sends_json_schema_without_tools():
    expected = {"order_id": None, "request_type": "其他", "expected_solution": "未明确"}

    def upstream(request):
        body = json.loads(request.content)
        assert "tools" not in body
        assert body["response_format"]["type"] == "json_schema"
        schema = body["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert set(schema["schema"]["required"]) == set(expected)
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "vendor-real-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(expected)},
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        llm = ChatOpenAI(
            model="vendor-real-model",
            base_url="https://upstream.invalid/v1",
            api_key="test-only",
            http_async_client=http,
            use_responses_api=False,
            max_retries=0,
        )
        model = llm.with_structured_output(AfterSalesTicket, method="json_schema", strict=True)
        result = await model.ainvoke(EXTRACT_PROMPT.format_messages(text="有点问题"))
    assert result.model_dump() == expected
