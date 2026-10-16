import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import public_error
from app.core.memory import ConversationTooLong
from app.db.repository import ConversationNotFound
from app.schemas.agent import AgentRequest, AgentResult

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/agent", response_model=AgentResult)
async def agent(body: AgentRequest, request: Request):
    try:
        return await request.app.state.agent_factory().run_agent_turn(body)
    except Exception as exc:
        logger.warning("Graph failed: %s %s", type(exc).__name__, getattr(exc, "__notes__", []))
        status = 502
        if isinstance(exc, ConversationNotFound):
            status = 404
        elif isinstance(exc, SQLAlchemyError):
            status = 503
        elif isinstance(exc, ConversationTooLong):
            status = 422
        raise HTTPException(status_code=status, detail=public_error(exc)) from exc
