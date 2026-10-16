import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("app.context")


def configure_context_logging():
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        Path("log").mkdir(exist_ok=True)
        handler = RotatingFileHandler(
            "log/app.log", maxBytes=10_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def log_context(kind, cid, summary, messages, counter, *, consumer="main_agent", question=""):
    logger.info(
        "%s %s",
        kind,
        json.dumps(
            {
                "conversation_id": cid,
                "consumer": consumer,
                "question": question,
                "summary": summary,
                "window_count": len(messages),
                "estimated_tokens": counter(messages),
                "messages": [
                    {
                        "role": m.type,
                        "head": m.text[:40],
                        "content": m.content,
                        "tool_calls": getattr(m, "tool_calls", None),
                        "tool_call_id": getattr(m, "tool_call_id", None),
                    }
                    for m in messages
                ],
            },
            ensure_ascii=False,
        ),
    )
