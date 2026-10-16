from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from app.core.evaluation import read_report

router = APIRouter(prefix="/api/rag-eval", tags=["rag-evaluation"])


class Disposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["已解决", "无需解决"]
    resolution: str = Field(min_length=1, max_length=4000)

    @field_validator("resolution")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请填写处置说明")
        return value.strip()


@router.get("/report")
async def report(request: Request):
    value = read_report(request.app.state.rag_report_path)
    if value["present"]:
        cases = (value.get("generation") or {}).get("faithfulness_cases", [])
        try:
            value["ledger"] = await request.app.state.rag_repository.manual_summary(
                [case["eval_id"] for case in cases]
            )
            denominator = value["kpi"].get("faithfulness_n", 0)
            value["ledger"]["current_confirmed_rate"] = (
                value["ledger"]["current_confirmed"] / denominator if denominator else None
            )
        except SQLAlchemyError:
            value["ledger"] = {"error": "个案台账读不到，请检查 MySQL"}
    return value


@router.get("/faith-cases")
async def cases(
    request: Request,
    status: Literal["未解决", "已解决", "无需解决"] | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    try:
        return await request.app.state.rag_repository.cases(status, page, page_size)
    except SQLAlchemyError as exc:
        raise HTTPException(503, "个案台账读不到，请检查 MySQL") from exc


@router.post("/faith-cases/{case_id}")
async def resolve(case_id: int, body: Disposition, request: Request):
    try:
        await request.app.state.rag_repository.resolve(case_id, body.status, body.resolution)
        return {"status": body.status}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "处置未完成，请检查 MySQL") from exc
