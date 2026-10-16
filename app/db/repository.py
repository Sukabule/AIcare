from uuid import uuid4

from sqlalchemy import func, select

from app.db.models import FAQ, Conversation, ConversationSummary, Message, Ticket, ToolAuditLog
from app.db.review_repository import ReviewRepository


class ConversationNotFound(LookupError):
    pass


class Repository(ReviewRepository):
    """Each operation owns a short transaction and its own AsyncSession."""

    def __init__(self, sessions, summary_inject_segments=3):
        self.sessions = sessions
        self.summary_inject_segments = summary_inject_segments

    async def insert_tool_audit(self, **values):
        async with self.sessions.begin() as session:
            row = ToolAuditLog(**values)
            session.add(row)
            await session.flush()
            return row

    async def get_conversation(self, cid):
        async with self.sessions() as session:
            row = await session.get(Conversation, cid)
            if row is None:
                raise ConversationNotFound("会话不存在。")
            return row

    async def count_messages_after(self, cid, after_id):
        async with self.sessions() as session:
            return await session.scalar(
                select(func.count(Message.id)).where(
                    Message.conversation_id == cid, Message.id > after_id
                )
            )

    async def list_dialog_messages(self, cid):
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == cid, Message.role.in_(["user", "assistant"]))
                    .order_by(Message.id)
                )
            )

    async def update_conversation_summary(self, cid, summary, upto_msg_id):
        async with self.sessions.begin() as session:
            row = await session.get(Conversation, cid, with_for_update=True)
            if row is None:
                raise ConversationNotFound("会话不存在。")
            if upto_msg_id >= (row.summary_upto_msg_id or 0):
                row.summary, row.summary_upto_msg_id = summary, upto_msg_id

    async def update_layer1_from(self, cid, msg_id):
        async with self.sessions.begin() as session:
            row = await session.get(Conversation, cid, with_for_update=True)
            if row is None:
                raise ConversationNotFound("会话不存在。")
            row.layer1_from_msg_id = max(row.layer1_from_msg_id or 0, msg_id)

    async def append_summary_segment(self, cid, from_msg_id, upto_msg_id, content):
        if not content.strip() or from_msg_id > upto_msg_id:
            raise ValueError("摘要或区间无效")
        async with self.sessions.begin() as session:
            row = await session.get(Conversation, cid, with_for_update=True)
            if row is None:
                raise ConversationNotFound("会话不存在。")
            if from_msg_id <= (row.summary_upto_msg_id or 0):
                raise ValueError("摘要区间已覆盖，拒绝重复追加")
            seq = (
                await session.scalar(
                    select(func.max(ConversationSummary.seq)).where(
                        ConversationSummary.conversation_id == cid
                    )
                )
                or 0
            ) + 1
            session.add(
                ConversationSummary(
                    conversation_id=cid,
                    seq=seq,
                    from_msg_id=from_msg_id,
                    upto_msg_id=upto_msg_id,
                    content=content,
                )
            )
            await session.flush()
            segments = list(
                await session.scalars(
                    select(ConversationSummary.content)
                    .where(ConversationSummary.conversation_id == cid)
                    .order_by(ConversationSummary.seq.desc())
                    .limit(self.summary_inject_segments)
                )
            )
            row.summary = "\n".join(reversed(segments))
            row.summary_upto_msg_id = upto_msg_id
            return seq

    async def list_summary_segments(self, cid, limit=None):
        async with self.sessions() as session:
            statement = select(ConversationSummary.content).where(
                ConversationSummary.conversation_id == cid
            )
            if limit is None:
                return list(await session.scalars(statement.order_by(ConversationSummary.seq)))
            rows = list(
                await session.scalars(
                    statement.order_by(ConversationSummary.seq.desc()).limit(limit)
                )
            )
            return list(reversed(rows))

    async def list_conversations(self, user_id, limit=50):
        first_question = (
            select(Message.content)
            .where(Message.conversation_id == Conversation.id, Message.role == "user")
            .order_by(Message.id)
            .limit(1)
            .scalar_subquery()
        )
        async with self.sessions() as session:
            rows = await session.execute(
                select(Conversation, first_question.label("preview"))
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.id.desc())
                .limit(limit)
            )
            return [
                {
                    "id": c.id,
                    "preview": (preview or "新对话")[:80],
                    "has_summary": bool(c.summary),
                    "created_at": c.created_at.isoformat(),
                    "status": c.status,
                }
                for c, preview in rows
            ]

    async def get_or_create(self, user_id: str, conversation_id: int | None) -> Conversation:
        async with self.sessions.begin() as session:
            if conversation_id is None:
                conversation = Conversation(user_id=user_id)
                session.add(conversation)
                await session.flush()
                await session.refresh(conversation)
                return conversation
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.user_id != user_id:
                raise ConversationNotFound("会话不存在或不属于当前用户，请新建对话。")
            return conversation

    async def append(self, conversation_id, role, content, *, tool_calls=None, tool_call_id=None):
        async with self.sessions.begin() as session:
            message = Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
            )
            session.add(message)
            await session.flush()
            return message

    async def history(self, conversation_id: int) -> list[Message]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
            return list(rows)

    async def search_faq(self, keyword: str) -> list[dict]:
        async with self.sessions() as session:
            # Literal substring only. Escape '%' / '_' instead of treating them as wildcards.
            rows = await session.scalars(
                select(FAQ)
                .where(FAQ.question.contains(keyword, autoescape=True))
                .order_by(FAQ.id)
                .limit(5)
            )
            return [
                {
                    "id": row.id,
                    "question": row.question,
                    "answer": row.answer,
                    "category": row.category,
                }
                for row in rows
            ]

    async def create_ticket(
        self, conversation_id: int, description: str, ticket_type: str
    ) -> Ticket:
        async with self.sessions.begin() as session:
            conversation = await session.get(Conversation, conversation_id, with_for_update=True)
            if conversation is None:
                raise ConversationNotFound("会话不存在。")
            ticket = Ticket(
                ticket_no="TK" + uuid4().hex.upper(),
                conversation_id=conversation_id,
                description=description,
                ticket_type=ticket_type,
            )
            session.add(ticket)
            conversation.status = "已转人工"
            await session.flush()
            await session.refresh(ticket)
            return ticket
