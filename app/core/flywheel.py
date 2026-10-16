import json
import logging

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.prompts import FLYWHEEL_NORMALIZE_PROMPT

logger = logging.getLogger(__name__)


class NormalizedGap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalized_question: str = Field(min_length=4, max_length=512)
    matched_question_id: int | None = Field(default=None, gt=0, strict=True)
    ai_suggested_answer: str = Field(max_length=10000)

    @field_validator("normalized_question")
    @classmethod
    def complete_question(cls, value):
        value = value.strip()
        if len(value) < 4:
            raise ValueError("标准问题须为完整问句")
        return value


class Flywheel:
    def __init__(self, repository, model):
        self.repository, self.model = repository, model

    async def normalize(self, question, candidates):
        value = await self.model.with_structured_output(
            NormalizedGap, method="json_schema", strict=True
        ).ainvoke(
            FLYWHEEL_NORMALIZE_PROMPT.format_messages(
                question=question, candidates=json.dumps(candidates, ensure_ascii=False)
            )
        )
        return NormalizedGap.model_validate(value)

    async def process_pending(self, limit=100):
        report = {"processed": 0, "created": 0, "merged": 0, "skipped": 0}
        async with self.repository.flywheel_lock():
            rows = await self.repository.fetch_unmatched_low_conf(limit)
            for row in rows:
                candidates = await self.repository.list_review_candidates(200)
                try:
                    value = await self.normalize(row["raw_question"], candidates)
                    result = await self.repository.apply_normalization(
                        row["id"], value, {item["id"] for item in candidates}
                    )
                    for key, count in result.items():
                        report[key] += count
                except Exception as exc:
                    report["skipped"] += 1
                    logger.warning("flywheel item skipped: %s (%s)", row["id"], type(exc).__name__)
                    if getattr(exc, "status_code", None) in {400, 401, 402, 403, 404}:
                        report["blocked"] = "上游模型不可用，未处理的原话保留供重跑"
                        break
        return report
