from contextlib import aclosing
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import StreamingResponse

from app.api.chat import sse_data
from app.core.errors import public_error
from app.db.repository import ConversationNotFound, Repository
from app.graph.runtime import ResumeConflict
from app.schemas.actions import CreateRefundRequest, CreateRefundResponse, ResumeRequest
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/api/actions", tags=["actions"])


@router.post("/resume", response_class=StreamingResponse)
async def resume(body: ResumeRequest, request: Request):
    async def events():
        try:
            async with aclosing(
                request.app.state.agent_factory().stream_resume_turn(
                    body.conversation_id, body.order_id, body.interrupt_id, confirmed=body.confirmed
                )
            ) as stream:
                async for event in stream:
                    yield sse_data(
                        event, event="interrupt" if event.get("event") == "interrupt" else None
                    )
            yield "data: [DONE]\n\n"
        except ResumeConflict as exc:
            yield sse_data({"code": "invalid_resume", "message": str(exc)}, event="error")
        except Exception as exc:
            yield sse_data(public_error(exc), event="error")

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/create-refund", response_model=CreateRefundResponse)
async def create_refund(body: CreateRefundRequest, request: Request):
    try:
        return await request.app.state.agent_factory().create_refund(
            body.conversation_id, body.order_id, body.reason
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ConversationNotFound as exc:
        raise HTTPException(404, "会话不存在，请新建对话。") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "退款工单提交未确认成功，请联系人工核实，不要重复提交。") from exc


class TicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: int = Field(gt=0, strict=True)
    ticket_type: Literal["售后", "投诉", "咨询"]
    description: str = Field(min_length=1, max_length=4000)

    @field_validator("description")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("请填写反馈描述")
        return value.strip()


@router.post("/create-ticket")
async def create_ticket(body: TicketInput, request: Request):
    repository = Repository(request.app.state.database.sessions)
    tool = ToolRegistry(repository).tools["create_ticket"]
    try:
        return await tool.ainvoke(body.model_dump())
    except ConversationNotFound as exc:
        raise HTTPException(404, "会话不存在，请新建对话。") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "工单提交未确认成功，请联系人工核实，不要重复提交。") from exc
