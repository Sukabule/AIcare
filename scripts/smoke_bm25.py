"""Ch04 fail-fast: real Chinese BM25 + RRF in an isolated temporary collection."""

import json
from uuid import uuid4

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker


def main():
    client = MilvusClient(uri="http://127.0.0.1:19530", timeout=15)
    name = "smoke_ch04_" + uuid4().hex
    try:
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=1024)
        schema.add_field(
            "text",
            DataType.VARCHAR,
            max_length=4000,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
        )
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(
            Function(
                name="bm25",
                function_type=FunctionType.BM25,
                input_field_names=["text"],
                output_field_names=["sparse"],
            )
        )
        indexes = client.prepare_index_params()
        indexes.add_index("dense", index_type="AUTOINDEX", metric_type="COSINE")
        indexes.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
        client.create_collection(
            name, schema=schema, index_params=indexes, consistency_level="Strong"
        )
        vector = [1.0] + [0.0] * 1023
        client.upsert(
            name,
            [
                {"id": 1, "dense": vector, "text": "MH-W40 饮水机 水箱4L UV紫外杀菌"},
                {"id": 2, "dense": vector, "text": "MH-LP100 猫砂盆 自动清理"},
            ],
        )
        query = "饮水机 水箱"
        bm25 = client.search(
            name,
            data=[query],
            anns_field="sparse",
            limit=2,
            search_params={"metric_type": "BM25"},
            output_fields=["text"],
        )[0]
        hybrid = client.hybrid_search(
            name,
            reqs=[
                AnnSearchRequest([vector], "dense", {"metric_type": "COSINE"}, 2),
                AnnSearchRequest([query], "sparse", {"metric_type": "BM25"}, 2),
            ],
            ranker=RRFRanker(),
            limit=2,
            output_fields=["text"],
        )[0]
        passed = bool(bm25 and hybrid and bm25[0]["id"] == 1 and hybrid[0]["id"] == 1)
        print(
            json.dumps(
                {
                    "passed": passed,
                    "bm25_ids": [h["id"] for h in bm25],
                    "hybrid_ids": [h["id"] for h in hybrid],
                }
            )
        )
        return passed
    except Exception as exc:
        print(
            json.dumps(
                {"passed": False, "error_type": type(exc).__name__, "message": str(exc)[:600]}
            )
        )
        return False
    finally:
        if client.has_collection(name):
            client.drop_collection(name)
        client.close()


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
