from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ToolAuditLog(Base):
    __tablename__ = "tool_audit_logs"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger)
    tool_call_id: Mapped[str] = mapped_column(String(255))
    tool_name: Mapped[str] = mapped_column(String(128))
    tool_source: Mapped[str] = mapped_column(String(32))
    mcp_server: Mapped[str | None] = mapped_column(String(128))
    arguments: Mapped[dict] = mapped_column(JSON)
    result_summary: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(Enum("成功", "失败", "超时", "校验拦下", "权限拒绝"))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)")
    )


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_upto_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    layer1_from_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        Enum("进行中", "已转人工", "已结束"), server_default="进行中"
    )
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)")
    )


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (UniqueConstraint("conversation_id", "seq"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column()
    from_msg_id: Mapped[int] = mapped_column(BigInteger)
    upto_msg_id: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)")
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    role: Mapped[str] = mapped_column(Enum("user", "assistant", "tool"))
    content: Mapped[str | None] = mapped_column(LONGTEXT)
    tool_calls: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    tool_call_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)")
    )


class FAQ(Base):
    __tablename__ = "faq"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(String(255))
    answer: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64))


class Ticket(Base):
    __tablename__ = "tickets"
    ticket_no: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    description: Mapped[str] = mapped_column(Text)
    ticket_type: Mapped[str] = mapped_column(
        Enum("售后", "投诉", "咨询", "其他", "退款", validate_strings=True)
    )
    status: Mapped[str] = mapped_column(String(32), server_default="待处理")
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)")
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(255))
    questions: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    section_path: Mapped[str | None] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(32))
    is_key_clause: Mapped[bool] = mapped_column(Boolean, default=False)
    prev_chunk_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_chunks.id"))
    next_chunk_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_chunks.id"))
    vector_id: Mapped[str | None] = mapped_column(String(64))
    vectorize_status: Mapped[str] = mapped_column(Enum("pending", "done"), default="pending")
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))


class QaExtractionStaging(Base):
    __tablename__ = "qa_extraction_staging"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(64))
    source_ref: Mapped[str | None] = mapped_column(String(255))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Enum("extracted", "kept", "discarded"), default="extracted")
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))


class LowConfidenceQuestion(Base):
    __tablename__ = "low_confidence_questions"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    raw_question: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Enum("retrieval_low_conf", "self_check", "user_feedback"))
    reason: Mapped[str] = mapped_column(Text)
    retrieved_chunks: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    matched_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_queue.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))


class FaithCase(Base):
    __tablename__ = "faith_cases"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    eval_id: Mapped[str] = mapped_column(String(64), unique=True)
    bucket: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    strategy: Mapped[str] = mapped_column(String(32))
    answer: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON)
    judge_model: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(Enum("未解决", "已解决", "无需解决"), default="未解决")
    resolution: Mapped[str | None] = mapped_column(Text)
    seen_count: Mapped[int] = mapped_column(default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DATETIME, server_default=text("CURRENT_TIMESTAMP")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DATETIME, server_default=text("CURRENT_TIMESTAMP")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DATETIME)


class ReviewQueue(Base):
    __tablename__ = "review_queue"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    normalized_question: Mapped[str] = mapped_column(String(512))
    ai_suggested_answer: Mapped[str | None] = mapped_column(Text)
    occurrence_count: Mapped[int] = mapped_column(default=1)
    review_status: Mapped[str] = mapped_column(Enum("待审", "通过", "驳回"), default="待审")
    approved_answer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    triggered_by: Mapped[str] = mapped_column(Enum("定时", "手动"), default="手动")
    dataset_size: Mapped[int] = mapped_column()
    metrics: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME, server_default=text("CURRENT_TIMESTAMP"))
