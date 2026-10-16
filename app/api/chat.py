import json
import logging
from contextlib import aclosing

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from app.core.errors import public_error
from app.schemas import ChatRequest

router = APIRouter()
logger = logging.getLogger(__name__)


def sse_data(payload: dict, *, event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def stream_chat(body, agent_factory):
    try:
        async with aclosing(agent_factory().stream_agent_turn(body)) as stream:
            async for event in stream:
                yield sse_data(
                    event, event="interrupt" if event.get("event") == "interrupt" else None
                )
        yield "data: [DONE]\n\n"
    except Exception as exc:
        logger.warning("Chat failed: %s %s", type(exc).__name__, getattr(exc, "__notes__", []))
        yield sse_data(public_error(exc), event="error")


@router.post("/chat", response_class=StreamingResponse)
async def chat(body: ChatRequest, request: Request):
    return StreamingResponse(
        stream_chat(body, request.app.state.agent_factory),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
