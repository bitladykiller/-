"""长期记忆存储层的纯数据 helper。

这个模块只放 `SimpleLongTermMemory` 中不依赖 Milvus 客户端状态的纯逻辑：
- 过滤表达式构造
- Milvus 记录构造
- 搜索结果转换
- 去重命中判断
- 相似记忆聚类

这样 `simple_long_term_memory.py` 可以更聚焦在“何时查 / 何时写 / 何时合并”的编排。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias

from app.memory.ltm_utils import cosine_similarity, entity_to_memory
from app.memory.schemas import MemorySearchResult

MilvusRecord: TypeAlias = dict[str, Any]
MilvusHit: TypeAlias = Mapping[str, Any]
MilvusSearchGroups: TypeAlias = list[list[MilvusRecord]]


def build_active_memory_filter(
    tenant_id: str,
    user_id: str,
    memory_type: str | None = None,
) -> str:
    """构造长期记忆查询过滤条件。"""
    filters = [
        f'tenant_id == "{tenant_id}"',
        f'user_id == "{user_id}"',
        'is_deleted == false',
    ]
    if memory_type is not None:
        filters.insert(2, f'memory_type == "{memory_type}"')
    return " and ".join(filters)


def build_memory_id_filter(memory_id: str) -> str:
    """构造按 `memory_id` 精确查询的过滤表达式。"""
    return f'memory_id == "{memory_id}"'


def build_memory_record(
    *,
    memory_id: str,
    tenant_id: str,
    user_id: str,
    memory_type: str,
    content: str,
    embedding: list[float],
    now_ts: int,
) -> MilvusRecord:
    """构造一条待写入 Milvus 的长期记忆记录。"""
    return {
        "memory_id": memory_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "memory_type": memory_type,
        "content": content,
        "embedding": embedding,
        "created_at": now_ts,
        "updated_at": now_ts,
        "last_hit_at": 0,
        "hit_count": 0,
        "is_deleted": False,
    }


def build_partial_update_record(
    memory_id: str,
    *,
    updated_at: int,
    **fields: Any,
) -> MilvusRecord:
    """构造 Milvus partial upsert 记录。"""
    record: MilvusRecord = {
        "memory_id": memory_id,
        "updated_at": updated_at,
    }
    record.update(fields)
    return record


def record_embedding(record: MilvusHit) -> list[float]:
    """从 Milvus 记录中安全提取 embedding。"""
    embedding = record.get("embedding", [])
    return embedding if isinstance(embedding, list) else []


def search_results_from_hits(hits: Sequence[MilvusHit]) -> list[MemorySearchResult]:
    """把检索命中统一转换为领域层搜索结果。"""
    search_results: list[MemorySearchResult] = []
    for hit in hits:
        entity = hit.get("entity")
        if not isinstance(entity, dict):
            continue
        search_results.append(
            MemorySearchResult(
                memory=entity_to_memory(entity),
                score=hit.get("score", 0.0),
            )
        )
    return search_results


def has_dedup_match(
    result_groups: MilvusSearchGroups,
    similarity_threshold: float,
) -> bool:
    """判断去重检索结果里是否已有足够相似的记忆。"""
    if not result_groups or not result_groups[0]:
        return False

    max_score = max(item.get("distance", 0) for item in result_groups[0])
    return max_score >= similarity_threshold


def cluster_memory_records(
    records: Sequence[MilvusRecord],
    similarity_threshold: float,
) -> list[list[MilvusRecord]]:
    """按 embedding 相似度将记忆聚成多个簇。"""
    clusters: list[list[MilvusRecord]] = []
    used_indices: set[int] = set()

    for index, record in enumerate(records):
        if index in used_indices:
            continue

        cluster = [record]
        used_indices.add(index)

        for compare_index, candidate in enumerate(records):
            if compare_index in used_indices:
                continue

            embedding1 = record_embedding(record)
            embedding2 = record_embedding(candidate)
            if not embedding1 or not embedding2:
                continue

            similarity = cosine_similarity(embedding1, embedding2)
            if similarity >= similarity_threshold:
                cluster.append(candidate)
                used_indices.add(compare_index)

        if len(cluster) > 1:
            clusters.append(cluster)

    return clusters
