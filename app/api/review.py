from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db.repository import Repository
from app.db.review_repository import ReviewConflict
from app.kb.review_writeback import ReviewWriteback, WritebackFailed

router = APIRouter(prefix="/api/review", tags=["review"])


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved_answer: str = Field(min_length=1, max_length=10000, pattern=r"\S")


@router.get("/queue")
async def queue(request: Request, status: Literal["待审", "通过", "驳回", "全部"] = "待审"):
    return {
        "items": await Repository(request.app.state.database.sessions).list_review_queue(
            None if status == "全部" else status
        )
    }


@router.get("/{rid}")
async def detail(rid: int, request: Request):
    try:
        return await Repository(request.app.state.database.sessions).get_review_detail(rid)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{rid}/approve")
async def approve(rid: int, body: Approval, request: Request):
    try:
        return await ReviewWriteback(request.app.state.kb).approve(
            rid, body.approved_answer.strip()
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ReviewConflict, BlockingIOError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except WritebackFailed as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/{rid}/reject")
async def reject(rid: int, request: Request):
    try:
        await Repository(request.app.state.database.sessions).update_review_status(
            rid, "驳回", None
        )
        return {"status": "驳回"}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
