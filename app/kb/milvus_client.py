import asyncio

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker

TEXT_FIELDS = ["question", "answer", "section_path", "content_type", "category"]
FIELDS = [*TEXT_FIELDS, "is_key_clause"]


def metadata_filter(filters=None):
    filters = filters or {}
    if set(filters) - {"category", "content_type", "section_path"}:
        raise ValueError("不支持的元数据过滤字段")
    if any(not isinstance(value, str) or len(value) > 512 for value in filters.values()):
        raise ValueError("过滤值必须是长度不超过 512 的字符串")
    return " and ".join(f"{key} == {{{key}}}" for key in filters), dict(filters)


class Vectors:
    """Docker Milvus; each worker call owns and closes its client."""

    def __init__(self, settings):
        self.uri = settings.milvus_uri
        self.collection = settings.milvus_collection

    def _call(self, operation, *args):
        client = MilvusClient(uri=self.uri, timeout=15)
        try:
            return operation(client, *args)
        finally:
            client.close()

    def _ensure(self, client):
        if client.has_collection(self.collection):
            names = {
                field["name"] for field in client.describe_collection(self.collection)["fields"]
            }
            if not {"dense", "sparse", "text"} <= names:
                raise RuntimeError("旧向量集合需要先备份并执行 Ch04 迁移")
            if "is_key_clause" not in names:
                client.add_collection_field(
                    self.collection,
                    "is_key_clause",
                    DataType.BOOL,
                    nullable=True,
                    default_value=False,
                )
            return
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=1024)
        schema.add_field(
            "text",
            DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
        )
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("is_key_clause", DataType.BOOL, nullable=True, default_value=False)
        for field, size in [
            ("question", 8192),
            ("answer", 65535),
            ("section_path", 2048),
            ("category", 1024),
            ("content_type", 128),
        ]:
            schema.add_field(field, DataType.VARCHAR, max_length=size)
        schema.add_function(
            Function(
                name="text_bm25",
                function_type=FunctionType.BM25,
                input_field_names=["text"],
                output_field_names=["sparse"],
            )
        )
        indexes = client.prepare_index_params()
        indexes.add_index(field_name="dense", index_type="AUTOINDEX", metric_type="COSINE")
        indexes.add_index(
            field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25"
        )
        client.create_collection(
            self.collection, schema=schema, index_params=indexes, consistency_level="Strong"
        )

    async def upsert(self, rows):
        values = []
        for row in rows:
            fields = {name: row.get(name) or "" for name in TEXT_FIELDS}
            values.append(
                dict(
                    id=row["id"],
                    dense=row.get("dense", row.get("vector")),
                    text=row.get("text")
                    or "\n".join((fields["category"], fields["question"], fields["answer"])),
                    **fields,
                    is_key_clause=bool(row.get("is_key_clause")),
                )
            )

        def write(client):
            self._ensure(client)
            client.upsert(self.collection, values)

        await asyncio.to_thread(self._call, write)

    async def _search(self, data, field, metric, top_k, filters):
        expression, params = metadata_filter(filters)

        def search(client):
            if not client.has_collection(self.collection):
                return []
            self._ensure(client)
            hits = client.search(
                self.collection,
                data=[data],
                anns_field=field,
                limit=top_k,
                filter=expression,
                filter_params=params,
                output_fields=FIELDS,
                search_params={"metric_type": metric},
                consistency_level="Strong",
            )[0]
            return [dict(id=h["id"], score=h["distance"], **h["entity"]) for h in hits]

        return await asyncio.to_thread(self._call, search)

    async def dense_search(self, vector, top_k, filters=None):
        return await self._search(vector, "dense", "COSINE", top_k, filters)

    async def bm25_search(self, query, top_k, filters=None):
        return await self._search(query, "sparse", "BM25", top_k, filters)

    async def hybrid_search(self, vector, query, top_k, filters=None):
        expression, params = metadata_filter(filters)

        def search(client):
            if not client.has_collection(self.collection):
                return []
            self._ensure(client)
            requests = [
                AnnSearchRequest(
                    [data], field, {"metric_type": metric}, 50, expr=expression, expr_params=params
                )
                for data, field, metric in [(vector, "dense", "COSINE"), (query, "sparse", "BM25")]
            ]
            hits = client.hybrid_search(
                self.collection,
                reqs=requests,
                ranker=RRFRanker(),
                limit=top_k,
                output_fields=FIELDS,
                consistency_level="Strong",
            )[0]
            return [dict(id=h["id"], score=h["distance"], **h["entity"]) for h in hits]

        return await asyncio.to_thread(self._call, search)

    async def search(self, vector, top_k):
        return await self.dense_search(vector, top_k)

    async def count(self):
        def count(client):
            if not client.has_collection(self.collection):
                return 0
            return client.query(
                self.collection, filter="", output_fields=["count(*)"], consistency_level="Strong"
            )[0]["count(*)"]

        return await asyncio.to_thread(self._call, count)

    async def reset(self):
        await asyncio.to_thread(self._call, lambda c: c.drop_collection(self.collection))

    async def remove(self, ids):
        if not ids:
            return

        def remove(client):
            if client.has_collection(self.collection):
                client.delete(self.collection, ids=list(ids))

        await asyncio.to_thread(self._call, remove)

    async def backfill_key_flags(self, rows):
        def migrate(client):
            self._ensure(client)
            if rows:
                client.upsert(
                    self.collection,
                    [dict(id=row["id"], is_key_clause=bool(row["is_key_clause"])) for row in rows],
                    partial_update=True,
                )
            return len(rows)

        return await asyncio.to_thread(self._call, migrate)
