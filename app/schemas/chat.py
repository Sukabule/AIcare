from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    user_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=32000)
    conversation_id: int | None = Field(default=None, gt=0, strict=True)
