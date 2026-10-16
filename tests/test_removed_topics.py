import httpx

from app.core.intent import IntentResult
from app.core.jobs import TARGETS
from app.main import create_app


async def test_removed_topic_feature_is_not_reachable():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in (
            "/topics",
            "/topics/questions",
            "/acceptance",
            "/acceptance/data",
            "/acceptance/eval",
            "/acceptance/errors",
            "/api/topics/distribution",
            "/api/acceptance/overview",
            "/static/topics.html",
            "/static/acceptance.html",
        ):
            assert (await client.get(path)).status_code == 404, path
        for name in ("ch10-train", "ch10-corpus", "classify-pool", "classifier-up"):
            assert (
                await client.post(f"/api/jobs/{name}", json={"confirmed": True})
            ).status_code == 404
        for path in ("/", "/admin", "/kb", "/review", "/observability", "/rag-eval"):
            assert (await client.get(path)).status_code == 200, path
        nav = (await client.get("/static/admin.js")).text
        assert "/topics" not in nav and "/acceptance" not in nav
    await app.state.database.close()


def test_chat_intent_schema_and_existing_jobs_remain():
    intents = IntentResult.model_json_schema()["properties"]["intent"]["enum"]
    assert intents == [
        "物流",
        "订单",
        "商品咨询",
        "退款退货",
        "售后",
        "投诉",
        "闲聊",
        "其他",
        "人工",
    ]
    assert {"kb-build", "flywheel", "eval-rag"} <= TARGETS.keys()
    assert not any(n.startswith("ch10-") or n.startswith("classif") for n in TARGETS)
