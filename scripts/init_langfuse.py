"""Initialize the local v3 stack with generated secrets, preserving existing credentials."""

import re
import secrets
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / ".env"
    content = path.read_text("utf-8") if path.exists() else ""
    values = dotenv_values(path) if path.exists() else {}

    def put(name, value):
        nonlocal content
        if values.get(name):
            return
        line = f"{name}={value}"
        pattern = rf"^{re.escape(name)}=.*$"
        content = (
            re.sub(pattern, lambda match: line, content, flags=re.M)
            if re.search(pattern, content, flags=re.M)
            else content.rstrip() + "\n" + line + "\n"
        )
        values[name] = value

    for name in [
        "LF_POSTGRES_PASSWORD",
        "LF_CLICKHOUSE_PASSWORD",
        "LF_REDIS_PASSWORD",
        "LF_MINIO_PASSWORD",
        "LF_SALT",
        "LF_ENCRYPTION_KEY",
        "LF_NEXTAUTH_SECRET",
        "LANGFUSE_INIT_USER_PASSWORD",
    ]:
        put(name, secrets.token_hex(32))
    put("LANGFUSE_BASE_URL", "http://127.0.0.1:3000")
    put("LANGFUSE_PUBLIC_KEY", "pk-lf-" + secrets.token_hex(16))
    put("LANGFUSE_SECRET_KEY", "sk-lf-" + secrets.token_hex(32))
    put("LANGFUSE_INIT_USER_EMAIL", "admin@aicare.local")
    path.write_text(content, encoding="utf-8")
    print("Langfuse local configuration initialized in ignored .env; secrets are not printed.")


if __name__ == "__main__":
    main()
