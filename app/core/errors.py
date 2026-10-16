from openai import APIConnectionError, APIStatusError, APITimeoutError
from sqlalchemy.exc import SQLAlchemyError

from app.core.llm import ModelConfigurationError
from app.core.memory import ConversationTooLong
from app.db.repository import ConversationNotFound


def public_error(exc: Exception) -> dict[str, str]:
    # Never echo upstream response bodies, URLs, keys or user input.
    if isinstance(exc, ConversationNotFound):
        return {"code": "conversation_not_found", "message": str(exc)}
    if isinstance(exc, SQLAlchemyError):
        return {"code": "database_unavailable", "message": "数据库暂时不可用，请稍后重试。"}
    if isinstance(exc, ModelConfigurationError):
        return {"code": "model_not_configured", "message": str(exc)}
    if isinstance(exc, ConversationTooLong):
        return {"code": "context_budget_exceeded", "message": str(exc)}
    if isinstance(exc, APITimeoutError):
        return {"code": "upstream_timeout", "message": "上游模型响应超时，请稍后重试。"}
    if isinstance(exc, APIConnectionError):
        return {
            "code": "upstream_connection_error",
            "message": "无法连接上游模型，请检查地址与网络。",
        }
    if isinstance(exc, APIStatusError):
        if exc.status_code in (401, 403):
            message = "上游鉴权失败，请检查 CHAT_API_KEY 与模型访问权限。"
        elif exc.status_code == 404:
            message = "上游资源不存在，请检查 CHAT_BASE_URL 和真实 CHAT_MODEL。"
        elif exc.status_code == 400:
            message = "上游拒绝请求，请检查模型名及原生 JSON Schema 支持。"
        elif exc.status_code == 402:
            message = "上游账户余额不足，请充值后重试。"
        else:
            message = f"上游模型请求失败（HTTP {exc.status_code}），请稍后重试。"
        return {"code": "upstream_error", "message": message}
    return {"code": "upstream_error", "message": "上游模型调用或响应校验失败，请检查模型配置。"}
