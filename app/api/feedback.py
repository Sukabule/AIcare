import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db.rag_repository import RagRepository
from app.db.repository import ConversationNotFound, Repository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: int = Field(gt=0, strict=True)
    rating: Literal["up", "down"]
    question: str = Field(min_length=1, max_length=8000, pattern=r"\S")


@router.post("")
async def feedback(body: Feedback, request: Request):
    repository = Repository(request.app.state.database.sessions)
    try:
        await repository.get_conversation(body.conversation_id)
    except ConversationNotFound as exc:
        raise HTTPException(404, "会话不存在") from exc
    if body.rating == "up":
        logger.info("positive feedback conversation=%s", body.conversation_id)
        return {"received": True}
    snapshot = None
    try:
        runtime = request.app.state.agent_factory()
        state = (
            await (await runtime.ensure()).aget_state(runtime.config(body.conversation_id))
        ).values
        if state.get("question") == body.question:
            snapshot = state.get("retrieved_snapshot") or state.get("citations") or None
    except Exception:
        logger.warning("feedback snapshot unavailable", exc_info=True)
    await RagRepository(request.app.state.database.sessions).low_confidence(
        body.conversation_id, body.question, "user_feedback", "用户反馈未解决", snapshot
    )
    return {"received": True}
