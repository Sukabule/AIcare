"""Apply authoritative SQL files via host Docker CLI or a direct container connection."""

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_FILES = (
    "sql/ch02-ddl.sql",
    "sql/ch02-seed.sql",
    "sql/ch03-ddl.sql",
    "sql/ch04-ddl.sql",
    "sql/ch06-ticket-type.sql",
    "sql/ch07-ddl.sql",
    "sql/ch07-layers.sql",
    "sql/ch08-ddl.sql",
    "sql/ch09-ddl.sql",
)


def apply(path: str, *, database="aicare", root=False):
    user = "root" if root else "aicare"
    variable = "MYSQL_ROOT_PASSWORD" if root else "MYSQL_PASSWORD"
    command = f'MYSQL_PWD="${variable}" mysql -u {user} --default-character-set=utf8mb4 {database}'
    subprocess.run(
        ["docker", "compose", "exec", "-T", "mysql", "sh", "-c", command],
        input=(ROOT / path).read_bytes(),
        cwd=ROOT,
        check=True,
    )


async def apply_direct():
    # Direct file invocation has scripts/, rather than the repository, on sys.path.
    sys.path.insert(0, str(ROOT))
    from app.config import Settings
    from app.db.base import Database

    database = Database(Settings().database_url.get_secret_value())
    try:
        async with database.engine.begin() as connection:
            for path in SQL_FILES:
                # These authoritative files contain no delimiter blocks. Keep a
                # single session so SET/PREPARE/EXECUTE migrations retain state.
                for statement in (ROOT / path).read_text("utf-8").split(";"):
                    if statement.strip():
                        await connection.exec_driver_sql(statement)
                print(f"Applied {path}", flush=True)
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="Also provision dedicated aicare_test")
    parser.add_argument(
        "--direct", action="store_true", help="Use DATABASE_URL directly, without the Docker CLI"
    )
    args = parser.parse_args()
    if args.direct:
        if args.test:
            parser.error("--direct cannot provision the privileged test database")
        asyncio.run(apply_direct())
    else:
        for path in SQL_FILES:
            apply(path)
        if args.test:
            apply("sql/ch02-test-db.sql", root=True)
            for path in SQL_FILES:
                apply(path, database="aicare_test")
    print("Database schema and seed are ready." + (" Test database ready." if args.test else ""))


if __name__ == "__main__":
    main()
