from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    chat_base_url: str = "https://api.openai.com/v1"
    chat_model: str = ""
    judge_model: str = "zai-org/GLM-5.2"
    chat_api_key: SecretStr = SecretStr("")
    chat_memory_token_budget: int = Field(default=2000, ge=1)
    chat_token_encoding: str = "cl100k_base"
    chat_timeout_seconds: float = Field(default=60, gt=0)
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_base_url: str = ""
    # 240 real labeled retrieval samples, max Youden J=0.9056 (see ch09 report).
    evidence_confidence_threshold: float = Field(default=0.100168, ge=0, le=1)

    embed_base_url: str = "https://api.siliconflow.cn/v1"
    embed_model: str = "BAAI/bge-m3"
    embed_api_key: SecretStr = SecretStr("")
    siliconflow_api_key: SecretStr = SecretStr("")
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "knowledge"
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    retrieval_min_score: float = Field(default=0.5, ge=-1, le=1)
    rerank_min_score: float = Field(default=0.5, ge=0, le=1)
    kb_source_dir: Path = Path("data/kb")
    rerank_base_url: str = ""
    rerank_model: str = ""
    rerank_api_key: SecretStr = SecretStr("")

    database_url: SecretStr = SecretStr(
        "mysql+asyncmy://aicare:configure-in-env@127.0.0.1:3306/aicare?charset=utf8mb4"
    )
    test_database_url: SecretStr = SecretStr(
        "mysql+asyncmy://aicare:configure-in-env@127.0.0.1:3306/aicare_test?charset=utf8mb4"
    )
    # Compose overrides only the endpoint, retaining encoded URL credentials.
    database_host: str = ""
    database_port: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode="after")
    def override_database_endpoint(self):
        if self.database_host or self.database_port is not None:
            for field in ("database_url", "test_database_url"):
                url = make_url(getattr(self, field).get_secret_value())
                url = url.set(
                    host=self.database_host or url.host,
                    port=self.database_port if self.database_port is not None else url.port,
                )
                setattr(self, field, SecretStr(url.render_as_string(hide_password=False)))
        return self

    graph_checkpoint_path: Path = Path("data/checkpoints/conversations.sqlite")
    max_agent_steps: int = Field(default=6, ge=1, le=20)
    intent_model: str = ""
    intent_small_model: str = ""
    intent_mode: str = Field(default="accuracy", pattern="^(accuracy|cost)$")
    intent_conf_threshold: float = Field(default=0.8, ge=0, le=1)
    reference_history_turns: int = Field(default=4, ge=1, le=10)

    model_context_window: int = Field(default=0, ge=0)
    context_budget_turns: int = Field(default=20, ge=1)
    max_user_input_tokens: int = Field(default=2000, ge=1)
    max_output_tokens: int = Field(default=10000, ge=1)
    system_prompt_tokens: int = Field(default=700, ge=0)
    evidence_tokens_per_chunk: int = Field(default=160, ge=0)
    layer1_ratio: float = Field(default=0.7, gt=0, lt=1)
    layer2_reply_keep_chars: int = Field(default=60, ge=1)
    layer2_tool_keep_tokens: int = Field(default=200, ge=1)
    tool_result_max_tokens: int = Field(default=3000, ge=1)
    agent_step_ai_tokens: int = Field(default=500, ge=0)
    summary_inject_segments: int = Field(default=3, ge=1)
    summary_tokens_per_segment: int = Field(default=250, ge=1)
    budget_safety_margin: int = Field(default=1000, ge=0)
    zh_chars_per_token: float = Field(default=1.2, gt=0)
    context_window_max_tokens: int = Field(default=0, ge=0)
    summary_model: str = ""
    summary_base_url: str = ""
    summary_api_key: SecretStr = SecretStr("")

    mcp_logistics_url: str = "http://127.0.0.1:8101/mcp"
    mcp_aftersales_url: str = "http://127.0.0.1:8102/mcp"
    tool_default_timeout: float = Field(default=5, gt=0)
    mcp_tool_timeout: float = Field(default=10, gt=0)
    mcp_discovery_ttl_seconds: float = Field(default=60, ge=0)
    mcp_discovery_failure_ttl_seconds: float = Field(default=5, ge=0)
    tool_max_retries: int = Field(default=2, ge=0, le=5)
    demo_ticket_delay_seconds: float = Field(default=0, ge=0)

    tool_timeout_seconds: float = Field(default=10, gt=0)
    tool_read_retries: int = Field(default=1, ge=0, le=3)
