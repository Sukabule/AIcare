from typing import Literal

from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.prompts import INTENT_CLASSIFY_PROMPT


class IntentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊", "其他", "人工"]
    confidence: float = Field(ge=0, le=1, strict=True)


def history_text(history):
    return "\n".join(f"{m.type}: {m.text}" for m in history)


async def classify(model, query, history):
    try:
        value = await model.with_structured_output(
            IntentResult, method="json_schema", strict=True
        ).ainvoke(
            INTENT_CLASSIFY_PROMPT.format_messages(question=query, history=history_text(history))
        )
        return IntentResult.model_validate(value)
    except (ValidationError, OutputParserException, ValueError, TypeError):
        # Only response parsing/validation falls back. Transport/billing errors must surface.
        return IntentResult(intent="其他", confidence=0.0)
