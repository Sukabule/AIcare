import json
from pathlib import Path

from fastapi import APIRouter, Request

from app.db.repository import Repository

REPORT_DIR = Path(__file__).resolve().parents[2] / "data/ch09/reports"
router = APIRouter(prefix="/api/observability", tags=["observability"])


def artifact(path, job, message):
    try:
        value = json.loads(path.read_text("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("report malformed")
        return {"present": True, "status": "ready", "job": job, "report": value}
    except (OSError, ValueError, TypeError):
        return {"present": False, "status": "empty", "job": job, "message": message}


async def overview_data(app):
    directory = getattr(app.state, "ch09_report_dir", REPORT_DIR)
    cost = artifact(
        directory / "cost_by_intent.json", "cost-report", "先聊几句积累 trace，再重跑成本账。"
    )
    calibration = artifact(
        directory / "confidence_calibration.json",
        "calibrate-confidence",
        "尚未校准，请运行真实检索校准。",
    )
    settings = app.state.settings
    calibration["active_threshold"] = settings.evidence_confidence_threshold
    recommended = calibration.get("report", {}).get("recommended_threshold")
    calibration["needs_update"] = (
        recommended is not None and recommended != settings.evidence_confidence_threshold
    )
    try:
        rows = await Repository(app.state.database.sessions).list_eval_runs(10)
        trend = dict(
            present=bool(rows),
            status="ready" if rows else "empty",
            job="eval-flywheel",
            runs=list(reversed(rows)),
            message="按一次评估就是趋势的第一个点。",
        )
    except Exception:
        trend = dict(
            present=False,
            status="unavailable",
            job="eval-flywheel",
            message="数据库读不到，暂无法读取评估趋势。",
        )
    return dict(
        cost=cost,
        trend=trend,
        calibration=calibration,
        langfuse_url=settings.langfuse_base_url or "http://127.0.0.1:3000",
        jobs=[
            job
            for job in app.state.jobs.status()
            if job["name"] in {"cost-report", "eval-flywheel", "calibrate-confidence", "flywheel"}
        ],
    )


@router.get("/overview")
async def overview(request: Request):
    return await overview_data(request.app)
