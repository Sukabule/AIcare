from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import tool
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db.base import Database
from app.db.repository import Repository
from app.main import create_app
from app.tools.mcp_client import MCPDiscovery, format_logistics, normalize_result
from app.tools.registry import ToolSpec


@pytest.fixture(autouse=True)
def isolated_mcp_discovery(monkeypatch, request):
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        monkeypatch.setenv(name, "")
    if request.node.get_closest_marker("mcp_integration"):
        return

    @tool
    async def query_logistics(tracking_no: str) -> dict:
        """Fixture MCP logistics; real protocol has a separate integration test."""
        return {
            "source": "mock",
            "tracking_no": tracking_no,
            "status_code": "TRANSIT",
            "location": "上海配送站",
            "timeline": [],
        }

    @tool
    async def query_warranty(order_id: str) -> dict:
        """Fixture warranty."""
        return {"source": "mock", "order_id": order_id, "in_warranty": True}

    @tool
    async def query_return_status(order_id: str) -> dict:
        """Fixture returns."""
        return {"source": "mock", "order_id": order_id, "status": "仓库验收中"}

    async def discover(self):
        return [
            ToolSpec.from_tool(
                t,
                source="mcp",
                mcp_server=server,
                format_result=format_logistics if t.name == "query_logistics" else normalize_result,
            )
            for t, server in [
                (query_logistics, "logistics"),
                (query_warranty, "aftersales"),
                (query_return_status, "aftersales"),
            ]
        ]

    # Tests of discovery itself retain the real discovery implementation, with fake HTTP tools.
    if request.path.name != "test_ch08_registry.py":
        monkeypatch.setattr(MCPDiscovery, "fetch_mcp_specs", discover)


SQL_STATEMENTS = [
    statement
    for path in [
        "sql/ch02-ddl.sql",
        "sql/ch02-seed.sql",
        "sql/ch03-ddl.sql",
        "sql/ch04-ddl.sql",
        "sql/ch06-ticket-type.sql",
        "sql/ch07-ddl.sql",
        "sql/ch07-layers.sql",
        "sql/ch08-ddl.sql",
        "sql/ch09-ddl.sql",
    ]
    for statement in Path(path).read_text("utf-8").split(";")
    if statement.strip()
]


class FakeModel:
    def __init__(self):
        self.first = AIMessage(content="你好，请问需要什么帮助？")
        self.final = AIMessage(content="工具核验后的答复")
        self.calls = []
        self.bindings = 0
        self.error = None

    def bind_tools(self, tools):
        self.bindings += 1
        parent = self

        class Bound:
            async def ainvoke(self, messages, config=None):
                parent.calls.append(("bound", messages))
                if parent.error:
                    raise parent.error
                return parent.final if messages[-1].type == "tool" else parent.first

        return Bound()

    async def ainvoke(self, messages, config=None):
        self.calls.append(("final", messages))
        return self.final

    async def astream(self, messages):
        self.calls.append(("stream", messages))
        for value in ["已查到", "物流\n结果"]:
            yield AIMessageChunk(content=value)


@pytest.fixture
async def db():
    settings = Settings()
    url = settings.test_database_url.get_secret_value()
    if make_url(url).database != "aicare_test":
        raise RuntimeError("Tests require the dedicated aicare_test database")
    database = Database(url)
    async with database.engine.begin() as connection:
        for statement in SQL_STATEMENTS:
            await connection.execute(text(statement))
        # Old test checkouts may retain the retired child table; clear only this test DB.
        if (await connection.execute(text("SHOW TABLES LIKE 'topic_classifications'"))).first():
            await connection.execute(text("DELETE FROM topic_classifications"))
        await connection.execute(
            text("UPDATE knowledge_chunks SET prev_chunk_id=NULL, next_chunk_id=NULL")
        )
        for table in [
            "low_confidence_questions",
            "review_queue",
            "eval_runs",
            "faith_cases",
            "knowledge_chunks",
            "qa_extraction_staging",
            "tool_audit_logs",
            "messages",
            "tickets",
            "conversations",
        ]:
            await connection.execute(text(f"DELETE FROM {table}"))
    yield database
    await database.close()


@pytest.fixture
def repo(db):
    return Repository(db.sessions)


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def application(db, model, tmp_path):
    async def classify(query):
        return "物流" if "物流" in query else "订单"

    async def resolve(query, history):
        return query

    app = create_app(
        settings=Settings(kb_source_dir="data/kb", graph_checkpoint_path=tmp_path / "graph.sqlite"),
        llm=model,
        database=db,
        token_counter=len,
        classifier=classify,
        reference_resolver=resolve,
    )

    async def count():
        return 0

    app.state.kb.vectors.count = count
    return app


@pytest.fixture
async def client(application):
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as http:
        yield http
    if (
        hasattr(application.state.agent_factory, "cache_info")
        and application.state.agent_factory.cache_info().currsize
    ):
        await application.state.agent_factory().close()
