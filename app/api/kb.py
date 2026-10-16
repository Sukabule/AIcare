from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.kb.documents import build_chunks
from app.kb.service import VectorizationFailed

router = APIRouter(prefix="/api/kb", tags=["knowledge"])


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=200000)
    content_type: Literal["manual", "policy", "faq", "spec"] = "manual"
    vectorize: bool = False


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    route: Literal["dense", "vector", "bm25", "hybrid", "hybrid_rerank"] = "hybrid_rerank"
    filters: dict[Literal["category", "content_type", "section_path"], str] = Field(
        default_factory=dict
    )


@router.get("/overview")
async def overview(request: Request):
    result = await request.app.state.kb.overview()
    result["jobs"] = request.app.state.jobs.status()
    return result


@router.post("/preview")
async def preview(body: DocumentInput):
    return {"chunks": build_chunks(body.text, body.content_type)}


@router.post("/ingest")
async def ingest(body: DocumentInput, request: Request):
    try:
        return await request.app.state.kb.ingest(body.text, body.content_type, body.vectorize)
    except VectorizationFailed as exc:
        raise HTTPException(
            502,
            {
                **exc.result,
                "message": (
                    f"已入库 {exc.result['inserted']} 块；"
                    "向量化未完成，pending 可补跑，无需重新录入。"
                ),
            },
        ) from exc
    except BlockingIOError as exc:
        raise HTTPException(409, str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "MySQL 不可用，录入未完成") from exc


@router.post("/vectorize")
async def vectorize(request: Request):
    try:
        return {"vectorized": await request.app.state.kb.vectorize()}
    except BlockingIOError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "向量化未完成；已完成的块保留，剩余 pending 可重跑") from exc


@router.post("/search")
async def search(body: SearchInput, request: Request):
    try:
        return {
            "hits": await request.app.state.kb.retrieval.search_knowledge(
                body.query,
                body.top_k,
                strategy="vector" if body.route == "dense" else body.route,
                filters=body.filters,
            ),
            "min_score": request.app.state.kb.settings.retrieval_min_score
            if body.route in ("dense", "vector")
            else None,
            "route": body.route,
        }
    except Exception as exc:
        raise HTTPException(502, "检索不可用，请检查嵌入上游和 Milvus 服务") from exc


@router.get("/staging")
async def staging(request: Request):
    try:
        return {"items": await request.app.state.kb.repository.staging()}
    except SQLAlchemyError as exc:
        raise HTTPException(503, "MySQL 读不到") from exc
