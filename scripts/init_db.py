"""Apply authoritative SQL files; never use ORM create_all or erase application data."""

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="Also provision dedicated aicare_test")
    args = parser.parse_args()
    apply("sql/ch02-ddl.sql")
    apply("sql/ch02-seed.sql")
    apply("sql/ch03-ddl.sql")
    apply("sql/ch04-ddl.sql")
    apply("sql/ch06-ticket-type.sql")
    apply("sql/ch07-ddl.sql")
    apply("sql/ch07-layers.sql")
    apply("sql/ch08-ddl.sql")
    apply("sql/ch09-ddl.sql")
    if args.test:
        apply("sql/ch02-test-db.sql", root=True)
        apply("sql/ch02-ddl.sql", database="aicare_test")
        apply("sql/ch02-seed.sql", database="aicare_test")
        apply("sql/ch03-ddl.sql", database="aicare_test")
        apply("sql/ch04-ddl.sql", database="aicare_test")
        apply("sql/ch06-ticket-type.sql", database="aicare_test")
        apply("sql/ch07-ddl.sql", database="aicare_test")
        apply("sql/ch07-layers.sql", database="aicare_test")
        apply("sql/ch08-ddl.sql", database="aicare_test")
        apply("sql/ch09-ddl.sql", database="aicare_test")
    print("Database schema and seed are ready." + (" Test database ready." if args.test else ""))


if __name__ == "__main__":
    main()
