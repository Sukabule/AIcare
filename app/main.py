from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    actions,
    admin,
    agent,
    chat,
    conversations,
    extract,
    feedback,
    jobs,
    kb,
    observability,
    rag_eval,
    review,
)
from app.config import Settings
from app.core.budget import compute
from app.core.context_log import configure_context_logging, logger
from app.core.evaluation import REPORT_PATH
from app.core.jobs import Jobs
from app.core.llm import create_chat_model
from app.core.memory import approximate_counter
from app.core.query_understanding import QueryUnderstanding, SelfCheck
from app.core.rag import RagPipeline
from app.db.base import Database
from app.db.rag_repository import RagRepository
from app.db.repository import Repository
from app.graph.runtime import GraphRuntime
from app.kb.service import KnowledgeService
from app.tools.registry import ToolRegistry


def create_app(
    *,
    settings=None,
    llm=None,
    database=None,
    token_counter=None,
    classifier=None,
    reference_resolver=None,
    expander=None,
    intent_llm=None,
) -> FastAPI:
    settings = settings or Settings()
    configure_context_logging()
    budget = compute(settings)
    logger.info("context_budget %s", budget)
    if not budget.healthy:
        logger.warning(
            "context_budget unhealthy: 窗口扣除固定开销与当前峰值后不足一轮历史，请调整配置"
        )
    db = database if database is not None else Database(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(app):
        # Instantiate the runtime and scan builtin modules at service startup.
        agent_factory()
        yield
        await application.state.jobs.close()
        if agent_factory.cache_info().currsize:
            await agent_factory().close()
        if database is None:
            await db.close()
        if (
            intent_factory.cache_info().currsize
            and settings.intent_model
            and intent_llm is None
            and llm is None
        ):
            model = intent_factory()
            await model.root_async_client.close()
            model.root_client.close()
        if model_factory.cache_info().currsize and llm is None:
            model = model_factory()
            await model.root_async_client.close()
            model.root_client.close()
        if summary_factory.cache_info().currsize and llm is None:
            model = summary_factory()
            await model.root_async_client.close()
            model.root_client.close()

    application = FastAPI(title="AIcare", version="0.10.1", lifespan=lifespan)
    application.state.settings = settings
    application.state.kb = KnowledgeService(settings, db.sessions)
    application.state.jobs = Jobs(Path(__file__).resolve().parents[1])
    application.state.llm = llm
    application.state.database = db
    application.state.context_budget = budget

    @lru_cache(maxsize=1)
    def model_factory():
        return create_chat_model(settings)

    @lru_cache(maxsize=1)
    def rag_model_factory():
        model = llm if llm is not None else model_factory()
        if llm is None:
            updates = {"temperature": 0, "max_tokens": min(2048, settings.max_output_tokens)}
            if "siliconflow" in settings.chat_base_url:
                updates["extra_body"] = {"enable_thinking": False}
            model = model.model_copy(update=updates)
        return model

    application.state.kb.retrieval.understanding = QueryUnderstanding(rag_model_factory)
    application.state.rag_repository = RagRepository(db.sessions)
    application.state.rag_report_path = REPORT_PATH
    application.state.rag = RagPipeline(
        application.state.kb.retrieval,
        application.state.rag_repository,
        SelfCheck(rag_model_factory),
        settings.rerank_min_score,
        settings.evidence_confidence_threshold,
    )

    @lru_cache(maxsize=1)
    def intent_factory():
        # Cost routing fields are seeds; runtime remains accuracy mode.
        if intent_llm is not None:
            return intent_llm
        if llm is not None or not settings.intent_model:
            return rag_model_factory()
        updates = {"temperature": 0, "max_tokens": 1024}
        if "siliconflow" in settings.chat_base_url:
            updates["extra_body"] = {"enable_thinking": False}
        return create_chat_model(
            settings.model_copy(update={"chat_model": settings.intent_model})
        ).model_copy(update=updates)

    @lru_cache(maxsize=1)
    def summary_factory():
        if llm is not None:
            return llm
        configured = settings.model_copy(
            update={
                "chat_model": settings.summary_model or settings.chat_model,
                "chat_base_url": settings.summary_base_url or settings.chat_base_url,
                "chat_api_key": settings.summary_api_key
                if settings.summary_api_key.get_secret_value()
                else settings.chat_api_key,
            }
        )
        updates = {"temperature": 0, "max_tokens": 512}
        if "siliconflow" in configured.chat_base_url:
            updates["extra_body"] = {"enable_thinking": False}
        return create_chat_model(configured).model_copy(update=updates)

    @lru_cache(maxsize=1)
    def agent_factory():
        repository = Repository(db.sessions, settings.summary_inject_segments)
        return GraphRuntime(
            repository,
            rag_model_factory(),
            application.state.rag,
            ToolRegistry(
                repository, application.state.rag, settings.retrieval_top_k, settings=settings
            ),
            settings,
            token_counter or approximate_counter(settings.zh_chars_per_token),
            classifier,
            reference_resolver,
            expander,
            intent_factory(),
            summary_factory,
        )

    application.state.model_factory = model_factory
    application.state.agent_factory = agent_factory
    application.include_router(chat.router, prefix="/api")
    application.include_router(agent.router, prefix="/api")
    application.include_router(extract.router, prefix="/api")
    application.include_router(kb.router)
    application.include_router(jobs.router)
    application.include_router(admin.router)
    application.include_router(rag_eval.router)
    application.include_router(feedback.router)
    application.include_router(review.router)
    application.include_router(observability.router)
    application.include_router(actions.router)
    application.include_router(conversations.router)
    application.mount(
        "/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static"
    )

    def page_endpoint(filename):
        async def page():
            return FileResponse(Path(__file__).parent / "static" / filename)

        return page

    for path, filename, name in (
        ("/", "index.html", "index"),
        ("/admin", "admin.html", "admin_page"),
        ("/kb", "kb.html", "knowledge_page"),
        ("/review", "review.html", "review_page"),
        ("/observability", "observability.html", "observability_page"),
        ("/rag-eval", "rag-eval.html", "rag_evaluation_page"),
    ):
        application.add_api_route(
            path, page_endpoint(filename), methods=["GET"], name=name, include_in_schema=False
        )

    @application.get("/health")
    async def health():
        return {"status": "ok"}

    return application


app = create_app()
