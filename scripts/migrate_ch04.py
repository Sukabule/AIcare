"""Back up Ch03 knowledge before replacing demo content with configured Ch04 sources."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymilvus import MilvusClient
from sqlalchemy import select

from app.config import Settings
from app.db.base import Database
from app.db.models import KnowledgeChunk, QaExtractionStaging
from app.kb.repository import record
from app.kb.service import KnowledgeService
from app.kb.sources import materials


async def main():
    settings = Settings()
    sources = materials(settings.kb_source_dir)
    if not sources:
        raise RuntimeError("没有源文档，禁止迁移")
    db = Database(settings.database_url.get_secret_value())
    service = KnowledgeService(settings, db.sessions)
    client = MilvusClient(uri=settings.milvus_uri)
    try:
        fields = {
            f["name"] for f in client.describe_collection(settings.milvus_collection)["fields"]
        }
        if "dense" in fields:
            print("Ch04 schema already active; only resume pending vectors.", flush=True)
        else:
            async with service.repository.lock():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                target = Path(".cache/backups") / ("ch03_knowledge_" + timestamp + ".json")
                target.parent.mkdir(parents=True, exist_ok=True)
                async with db.sessions() as session:
                    backup = {
                        cls.__tablename__: [
                            record(row) for row in await session.scalars(select(cls))
                        ]
                        for cls in (KnowledgeChunk, QaExtractionStaging)
                    }
                target.write_text(
                    json.dumps(backup, ensure_ascii=False, indent=2, default=str), "utf-8"
                )
                if json.loads(target.read_text("utf-8")) != json.loads(
                    json.dumps(backup, default=str)
                ):
                    raise RuntimeError("备份校验失败")
                archive = settings.milvus_collection + "_ch03_backup_" + timestamp
                client.rename_collection(settings.milvus_collection, archive)
                print(f"Backup: {target}; old vector collection: {archive}", flush=True)
                await service.repository.reset()
                for source in sources:
                    result = await service.repository.ingest(source["chunks"])
                    print(f"Imported {source['name']}: {result['inserted']}", flush=True)
        print(f"Vectorized: {await service.vectorize()}", flush=True)
        print(
            f"Overview: {(await service.repository.overview())['total']} chunks; "
            f"Milvus {await service.vectors.count()}",
            flush=True,
        )
    finally:
        client.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
