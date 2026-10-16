from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from app.db.repository import ConversationNotFound, Repository

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(request: Request, user_id: str = Query(min_length=1, max_length=128)):
    try:
        return await Repository(request.app.state.database.sessions).list_conversations(user_id)
    except SQLAlchemyError as exc:
        raise HTTPException(503, "会话列表暂时不可用") from exc


@router.get("/{cid}/messages")
async def messages(cid: int, request: Request):
    repository = Repository(request.app.state.database.sessions)
    try:
        await repository.get_conversation(cid)
        return [
            {"id": m.id, "role": m.role, "content": m.content, "tool_calls": m.tool_calls}
            for m in await repository.list_dialog_messages(cid)
        ]
    except ConversationNotFound as exc:
        raise HTTPException(404, "会话不存在") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "历史记录暂时不可用") from exc
