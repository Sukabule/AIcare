import asyncio

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.prompts import (
    DIRECT_ANSWER_CHECK_PROMPT,
    EXPAND_QUERIES_PROMPT,
    POLICY_CHECK_PROMPT,
    QUERY_UNDERSTANDING_PROMPT,
    SELF_CHECK_PROMPT,
)


class ExpandedQueries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(min_length=3, max_length=3)

    @field_validator("queries")
    @classmethod
    def distinct_nonempty(cls, values):
        values = [v.strip() for v in values]
        if any(not v or len(v) > 512 for v in values) or len(set(values)) != 3:
            raise ValueError("扩写必须是三条不同、非空且简短的查询")
        return values


async def expand_queries(model, query, order_data):
    import json

    value = await model.with_structured_output(
        ExpandedQueries, method="json_schema", strict=True
    ).ainvoke(
        EXPAND_QUERIES_PROMPT.format_messages(
            query=query, order=json.dumps(order_data, ensure_ascii=False)
        )
    )
    return ExpandedQueries.model_validate(value).queries


def merge_policy_hits(groups):
    by_id = {}
    for group in groups:
        for hit in group:
            previous = by_id.get(hit["id"])
            if previous is None or hit["score"] > previous["score"]:
                by_id[hit["id"]] = dict(hit)
    return sorted(by_id.values(), key=lambda h: h["score"], reverse=True)


class QueryRewrite(BaseModel):
    standard_question: str = Field(description="保留原意、型号、数字的标准问法")
    synonyms: str = Field(description="与原问题同义的检索词，以空格分隔，不补充新诉求")


class EvidenceCheck(BaseModel):
    useful: bool
    reason: str


class DirectAnswerCheck(BaseModel):
    requires_knowledge: bool
    reason: str


class DirectAnswerGuard:
    def __init__(self, model_factory, repository):
        self.model_factory, self.repository = model_factory, repository

    async def __call__(self, conversation_id, query, answer):
        from app.core.rag import REFUSAL

        async with asyncio.timeout(45):
            result = (
                await self.model_factory()
                .with_structured_output(DirectAnswerCheck)
                .ainvoke(DIRECT_ANSWER_CHECK_PROMPT.format_messages(query=query, answer=answer))
            )
        if result.requires_knowledge:
            await self.repository.low_confidence(
                conversation_id, query, "self_check", result.reason
            )
            return REFUSAL
        return None


class QueryUnderstanding:
    def __init__(self, model_factory):
        self.model_factory = model_factory
        self.cache = {}
        self.fallbacks = 0

    async def __call__(self, query):
        if query in self.cache:
            return self.cache[query]
        try:
            async with asyncio.timeout(20):
                result = (
                    await self.model_factory()
                    .with_structured_output(QueryRewrite)
                    .ainvoke(QUERY_UNDERSTANDING_PROMPT.format_messages(query=query))
                )
            result = " ".join((query, result.standard_question, result.synonyms))
        except Exception:
            # Retrieval still works without the optional LLM rewrite.
            self.fallbacks += 1
            result = query
        self.cache[query] = result
        return result


class SelfCheck:
    def __init__(self, model_factory):
        self.model_factory = model_factory

    async def __call__(self, query, hits):
        return await self.check(query, hits, SELF_CHECK_PROMPT)

    async def check_policy(self, query, hits):
        return await self.check(query, hits, POLICY_CHECK_PROMPT)

    async def check(self, query, hits, prompt):
        import json

        async with asyncio.timeout(45):
            result = (
                await self.model_factory()
                .with_structured_output(EvidenceCheck)
                .ainvoke(
                    prompt.format_messages(
                        query=query, evidence=json.dumps(hits, ensure_ascii=False)
                    )
                )
            )
        return result.useful, result.reason
