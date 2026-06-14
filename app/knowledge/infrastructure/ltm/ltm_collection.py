"""长期记忆的 Milvus collection 与客户端调用 helper。

负责：
- 定义长期记忆 collection 的 schema / index 常量
- 初始化 collection（若不存在）
- 封装 insert / upsert / search 这类 Milvus 客户端调用样板

不负责：
- 长期记忆业务规则
- 去重、聚类和搜索结果转换
- LLM / embedding 编排
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeAlias

from pymilvus import DataType, Function, FunctionType

MilvusRecord: TypeAlias = dict[str, Any]
MilvusSearchGroups: TypeAlias = list[list[MilvusRecord]]

MEMORY_OUTPUT_FIELDS = [
    "memory_id",
    "tenant_id",
    "user_id",
    "memory_type",
    "content",
    "created_at",
    "updated_at",
    "last_hit_at",
    "hit_count",
    "is_deleted",
]
DEDUP_OUTPUT_FIELDS = ["memory_id", "content"]
_MEMORY_EMBEDDING_DIM = 1024


def ensure_memory_collection(
    milvus_client,
    collection_name: str,
) -> bool:
    """确保长期记忆 collection 存在。

    返回：
    - `True`：本次新建了 collection
    - `False`：collection 已存在，未执行新建
    """
    if milvus_client.has_collection(collection_name):
        return False

    schema = milvus_client.create_schema(
        auto_id=False,
        enable_dynamic_field=True,
    )
    schema.add_field("memory_id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
    schema.add_field("user_id", DataType.VARCHAR, max_length=64)
    schema.add_field("memory_type", DataType.VARCHAR, max_length=32)
    schema.add_field("content", DataType.VARCHAR, max_length=4096)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=_MEMORY_EMBEDDING_DIM)
    schema.add_field("created_at", DataType.INT64)
    schema.add_field("updated_at", DataType.INT64)
    schema.add_field("last_hit_at", DataType.INT64)
    schema.add_field("hit_count", DataType.INT64)
    schema.add_field("is_deleted", DataType.BOOL)
    schema.add_function(
        Function(
            name="bm25",
            function_type=FunctionType.BM25,
            input_field_names=["content"],
            output_field_names=["sparse_vector"],
        )
    )
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

    index_params = milvus_client.prepare_index_params()
    index_params.add_index(
        field_name="embedding",
        index_type="IVF_FLAT",
        metric_type="COSINE",
        params={"nlist": 1024},
    )
    index_params.add_index(
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
    )

    milvus_client.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )
    return True


def insert_records(
    milvus_client,
    collection_name: str,
    records: Sequence[MilvusRecord],
) -> None:
    """统一封装 Milvus insert。"""
    milvus_client.insert(
        collection_name=collection_name,
        data=list(records),
    )


def upsert_records(
    milvus_client,
    collection_name: str,
    records: Sequence[MilvusRecord],
) -> None:
    """统一封装 Milvus upsert。"""
    milvus_client.upsert(
        collection_name=collection_name,
        data=list(records),
    )


def search_records(
    milvus_client,
    collection_name: str,
    embedding: Sequence[float],
    filter_expr: str,
    *,
    limit: int,
    output_fields: list[str],
) -> MilvusSearchGroups:
    """统一封装 Milvus 原生向量 search。"""
    return milvus_client.search(
        collection_name=collection_name,
        data=[list(embedding)],
        filter=filter_expr,
        limit=limit,
        output_fields=output_fields,
    )


__all__ = [
    "MEMORY_OUTPUT_FIELDS",
    "ensure_memory_collection",
    "insert_records",
    "search_records",
    "upsert_records",
]
