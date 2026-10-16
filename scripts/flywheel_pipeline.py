"""Serial, restartable normalize-and-dedup batch; no in-process scheduler."""

import argparse
import asyncio
import json

from ch09_common import close_model, configure_model

from app.config import Settings
from app.core.flywheel import Flywheel
from app.db.base import Database
from app.db.repository import Repository


async def run(limit):
    settings = Settings()
    db, model = Database(settings.database_url.get_secret_value()), configure_model(settings)
    try:
        report = await Flywheel(Repository(db.sessions), model).process_pending(limit)
        print(json.dumps(report, ensure_ascii=False))
        return 1 if report.get("blocked") or report["skipped"] else 0
    finally:
        await close_model(model)
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("limit 必须在 1-1000")
    raise SystemExit(asyncio.run(run(args.limit)))
