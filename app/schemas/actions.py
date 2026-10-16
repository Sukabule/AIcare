from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: int = Field(gt=0, strict=True)
    order_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$"
    )
    confirmed: bool | None = Field(default=None, strict=True)
    interrupt_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def one_choice(self):
        if (self.order_id is not None) == (self.confirmed is not None):
            raise ValueError("请选择订单或确认/取消工单，两者只允许提供一个")
        return self


class CreateRefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: int = Field(gt=0, strict=True)
    order_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$")
    reason: Literal["七天无理由", "质量问题", "发错货", "不想要了", "其他"]


class CreateRefundResponse(BaseModel):
    ticket_no: str
    ticket_type: Literal["退款"]
    status: str
