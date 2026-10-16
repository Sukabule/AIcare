from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.chat import ChatRequest as ChatRequest


class ExtractRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    text: str = Field(min_length=1, max_length=32000)


class AfterSalesTicket(BaseModel):
    """仅从用户售后描述中提取的工单字段。"""

    model_config = ConfigDict(extra="forbid")
    order_id: str | None = Field(description="原文明确提供的订单号；未提供时必须为 null")
    request_type: Literal["退款", "换货", "维修", "投诉", "其他"] = Field(
        description="用户当前主要诉求；没有明确诉求时为其他"
    )
    expected_solution: str = Field(
        description="概括原文问题及用户期望方案；没有明确期望方案时填写未明确"
    )
