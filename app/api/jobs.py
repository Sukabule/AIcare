from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.core.jobs import HEAVY, TARGETS

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = False


@router.get("")
async def listing(request: Request):
    return request.app.state.jobs.status()


@router.get("/{name}")
async def status(name: str, request: Request):
    if name not in TARGETS:
        raise HTTPException(404, "未知作业")
    jobs = request.app.state.jobs
    return {**next(j for j in jobs.status() if j["name"] == name), "log": jobs.tail(name)}


@router.post("/{name}")
async def start(name: str, body: JobInput, request: Request):
    if name not in TARGETS:
        raise HTTPException(404, "未知作业")
    if name in HEAVY and not body.confirmed:
        raise HTTPException(422, "此作业需要明确确认")
    try:
        return await request.app.state.jobs.start(name)
    except BlockingIOError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "作业无法启动，请检查 make 和 uv 是否已安装") from exc


@router.post("/{name}/stop")
async def stop(name: str, request: Request):
    if name not in TARGETS:
        raise HTTPException(404, "未知作业")
    await request.app.state.jobs.stop(name)
    return {"status": "stopped"}
