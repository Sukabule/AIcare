from fastapi import APIRouter, Request

from app.api.observability import overview_data
from app.core.evaluation import read_report
from app.db.repository import Repository

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/overview")
async def overview(request: Request):
    kb = await request.app.state.kb.overview()
    modules = [
        dict(
            name="知识库",
            path="/kb",
            status="ready"
            if kb["mysql"] is not None and kb["milvus"] is not None
            else "unavailable",
            summary=f"{kb['mysql']['total']} 块知识，{kb['mysql']['pending']} 块待向量化"
            if kb["mysql"]
            else "数据库读不到；材料预览仍可使用",
            vector_status=kb["consistency"],
        )
    ]
    if kb["mysql"] is not None and kb["milvus"] is None:
        modules[0]["summary"] += "；向量服务读不到"
    try:
        counts = await Repository(request.app.state.database.sessions).review_counts()
        review = dict(
            name="审核队列",
            path="/review",
            status="ready",
            counts=counts,
            summary=(
                f"待审 {counts['待审']} · 通过 {counts['通过']} · 驳回 {counts['驳回']}"
                "；优先补高频缺口"
            ),
        )
    except Exception:
        review = dict(
            name="审核队列",
            path="/review",
            status="unavailable",
            summary="数据库读不到，暂无法读取审核队列",
        )
    modules.append(review)
    modules.extend(
        dict(name=name, path=None, status="not_implemented", summary="本章尚未实现")
        for name in ["问题池"]
    )
    report = read_report(request.app.state.rag_report_path)
    refusal = report.get("kpi", {}).get("refusal_rate")
    refusal_text = f"{refusal:.1%}" if refusal is not None else "尚未完成"
    modules.append(
        dict(
            name="RAG 评估",
            path="/rag-eval",
            status="ready" if report["present"] else "no_report",
            summary=(
                f"{report['meta']['dataset_size']} 题 · "
                f"最佳 MRR {report['kpi'].get('best_mrr', 0):.3f} · 库外拒答 {refusal_text}"
                if report["present"] and report["kpi"].get("best_mrr") is not None
                else "尚无有效评估报告，进入页面运行评估"
            ),
        )
    )
    observation = await overview_data(request.app)
    mismatch = observation["calibration"]["needs_update"]
    modules.append(
        dict(
            name="观测与成本",
            path="/observability",
            status="needs_update" if mismatch else "ready",
            summary="推荐阈值与在用值不同，请回填 app/config.py"
            if mismatch
            else (
                f"评估 {len(observation['trend'].get('runs', []))} 轮；查看意图 token 账与阈值校准"
            ),
        )
    )
    return {"modules": modules}
