from app.knowledge.infrastructure.ltm.ltm_collection import (
    MEMORY_OUTPUT_FIELDS,
    ensure_memory_collection,
    insert_records,
    search_records,
    upsert_records,
)


class FakeSchema:
    def __init__(self) -> None:
        self.fields: list[tuple[str, object, dict]] = []
        self.functions: list[object] = []

    def add_field(self, name: str, dtype: object, **kwargs) -> None:
        self.fields.append((name, dtype, kwargs))

    def add_function(self, func: object) -> None:
        self.functions.append(func)


class FakeIndexParams:
    def __init__(self) -> None:
        self.indices: list[dict] = []

    def add_index(self, **kwargs) -> None:
        self.indices.append(kwargs)


class FakeMilvusClient:
    def __init__(self, *, has_collection_result: bool = False) -> None:
        self.has_collection_result = has_collection_result
        self.created_schema = FakeSchema()
        self.created_index_params = FakeIndexParams()
        self.create_collection_calls: list[dict] = []
        self.insert_calls: list[dict] = []
        self.upsert_calls: list[dict] = []
        self.search_calls: list[dict] = []

    def create_schema(self, **kwargs) -> FakeSchema:
        self.create_schema_kwargs = kwargs
        return self.created_schema

    def prepare_index_params(self) -> FakeIndexParams:
        return self.created_index_params

    def has_collection(self, collection_name: str) -> bool:
        self.has_collection_name = collection_name
        return self.has_collection_result

    def create_collection(self, **kwargs) -> None:
        self.create_collection_calls.append(kwargs)

    def insert(self, **kwargs) -> None:
        self.insert_calls.append(kwargs)

    def upsert(self, **kwargs) -> None:
        self.upsert_calls.append(kwargs)

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return [[{"distance": 0.91}]]


def test_ensure_memory_collection_creates_collection_when_missing() -> None:
    client = FakeMilvusClient(has_collection_result=False)

    created = ensure_memory_collection(client, "customer_agent_long_memory")

    assert created is True
    assert client.has_collection_name == "customer_agent_long_memory"
    assert len(client.create_collection_calls) == 1
    assert client.create_collection_calls[0]["collection_name"] == "customer_agent_long_memory"
    assert client.create_collection_calls[0]["schema"] is client.created_schema
    assert client.create_collection_calls[0]["index_params"] is client.created_index_params
    assert client.create_schema_kwargs == {
        "auto_id": False,
        "enable_dynamic_field": True,
    }
    assert [name for name, _, _ in client.created_schema.fields] == [
        "memory_id",
        "tenant_id",
        "user_id",
        "memory_type",
        "content",
        "embedding",
        "created_at",
        "updated_at",
        "last_hit_at",
        "hit_count",
        "is_deleted",
        "sparse_vector",
    ]
    assert len(client.created_schema.functions) == 1
    assert client.created_index_params.indices == [
        {
            "field_name": "embedding",
            "index_type": "IVF_FLAT",
            "metric_type": "COSINE",
            "params": {"nlist": 1024},
        },
        {
            "field_name": "sparse_vector",
            "index_type": "SPARSE_INVERTED_INDEX",
            "metric_type": "BM25",
        },
    ]


def test_ensure_memory_collection_skips_existing_collection() -> None:
    client = FakeMilvusClient(has_collection_result=True)

    created = ensure_memory_collection(client, "customer_agent_long_memory")

    assert created is False
    assert client.create_collection_calls == []


def test_query_insert_upsert_and_search_delegate_to_client() -> None:
    client = FakeMilvusClient()
    records = [{"memory_id": "mem-1"}]

    insert_records(client, "memory_coll", records)
    upsert_records(client, "memory_coll", records)
    search_result = search_records(
        client,
        "memory_coll",
        [0.1, 0.2],
        'user_id == "1"',
        limit=3,
        output_fields=MEMORY_OUTPUT_FIELDS,
    )

    assert client.insert_calls == [
        {"collection_name": "memory_coll", "data": records}
    ]
    assert client.upsert_calls == [
        {"collection_name": "memory_coll", "data": records}
    ]
    assert search_result == [[{"distance": 0.91}]]
    assert client.search_calls == [
        {
            "collection_name": "memory_coll",
            "data": [[0.1, 0.2]],
            "filter": 'user_id == "1"',
            "limit": 3,
            "output_fields": MEMORY_OUTPUT_FIELDS,
        }
    ]
