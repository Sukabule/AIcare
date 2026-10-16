from uuid import uuid4

from app.config import Settings
from app.kb.milvus_client import Vectors


async def test_real_chinese_bm25_hybrid_and_filter():
    vectors = Vectors(Settings(milvus_collection="test_ch04_" + uuid4().hex))
    dense = [1.0] + [0.0] * 1023
    rows = [
        dict(
            id=1,
            vector=dense,
            question="MH-W40",
            answer="4L饮水机 UV杀菌",
            category="硬件",
            content_type="spec",
            section_path="规格 / MH-W40",
        ),
        dict(
            id=2,
            vector=dense,
            question="MH-LP100",
            answer="自动猫砂盆",
            category="硬件",
            content_type="spec",
            section_path="规格 / MH-LP100",
        ),
    ]
    try:
        await vectors.upsert(rows)
        assert (await vectors.bm25_search("饮水机", 5))[0]["id"] == 1
        assert (await vectors.hybrid_search(dense, "饮水机", 5))[0]["id"] == 1
        assert len(await vectors.dense_search(dense, 5, {"category": "硬件"})) == 2
        assert await vectors.hybrid_search(dense, "饮水机", 5, {"category": "其他"}) == []
    finally:
        await vectors.reset()
