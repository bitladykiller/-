"""长期记忆操作层的纯 helper。

这个模块只放 `SimpleLongTermMemory` 中不直接依赖 Milvus 客户端状态的
“操作规划”逻辑：
- 检索参数解析
- 写入 / 命中更新 / 软删除 payload 构造
- 相似记忆聚类后的合并计划
- 日志文本预览

这样 `simple_long_term_memory.py` 可以更聚焦在：
- 何时生成 embedding
- 何时调用 Milvus client
- 何时执行 upsert / delete
"""

from __future__ import annotations

import uuid
from typing import TypedDict

from app.memory.config import LongTermSearchConfig, LongTermUpdateOnHitConfig
from app.memory.ltm_store_utils import (
    MilvusRecord,
    build_active_memory_filter,
    build_memory_record,
    build_partial_update_record,
)
from app.memory.ltm_utils import merge_contents
from app.memory.schemas import LongTermMemory


class HitUpdatePlan(TypedDict):
    """长期记忆命中后的更新计划。"""

    hit_count: int
    last_hit_at: int
    update_record: MilvusRecord


class ClusterMergePlan(TypedDict):
    """相似记忆聚类后的合并计划。"""

    merged_record: MilvusRecord
    merged_content: str
    deleted_memory_ids: list[str]


def preview_text(text: str, limit: int) -> str:
    """为日志截断长文本，避免低价值噪音。"""
    return text[:limit]


def resolve_search_params(
    search_config: LongTermSearchConfig,
    top_k: int | None,
    score_threshold: float | None,
) -> tuple[int, float]:
    """统一补齐检索入口的默认 top_k 和阈值。"""
    resolved_top_k = top_k if top_k is not None else search_config["top_k"]
    resolved_score_threshold = (
        score_threshold
        if score_threshold is not None
        else search_config["score_threshold"]
    )
    return resolved_top_k, resolved_score_threshold


def resolve_active_search_request(
    search_config: LongTermSearchConfig,
    tenant_id: str,
    user_id: str,
    top_k: int | None,
    score_threshold: float | None,
) -> tuple[str, int, float]:
    """统一补齐活跃记忆过滤条件与检索参数。"""
    resolved_top_k, resolved_score_threshold = resolve_search_params(
        search_config,
        top_k,
        score_threshold,
    )
    filter_expr = build_active_memory_filter(tenant_id, user_id)
    return filter_expr, resolved_top_k, resolved_score_threshold


def build_new_memory_insert_record(
    *,
    tenant_id: str,
    user_id: str,
    memory_type: str,
    content: str,
    embedding: list[float],
    now_ts: int,
    memory_id: str | None = None,
) -> tuple[str, MilvusRecord]:
    """构造一条新长期记忆的写入计划。"""
    resolved_memory_id = memory_id or str(uuid.uuid4())
    record = build_memory_record(
        memory_id=resolved_memory_id,
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type=memory_type,
        content=content,
        embedding=embedding,
        now_ts=now_ts,
    )
    return resolved_memory_id, record


def build_hit_update_plan(
    memory: LongTermMemory,
    update_config: LongTermUpdateOnHitConfig,
    now_ts: int,
) -> HitUpdatePlan:
    """根据命中更新策略生成 partial upsert payload。"""
    last_hit_at = now_ts if update_config["update_last_hit_at"] else memory.last_hit_at
    hit_count = (
        (memory.hit_count or 0) + 1
        if update_config["increase_hit_count"]
        else memory.hit_count
    )
    update_record = build_partial_update_record(
        memory.memory_id,
        updated_at=now_ts,
        hit_count=hit_count,
        last_hit_at=last_hit_at,
    )
    return {
        "hit_count": hit_count,
        "last_hit_at": last_hit_at,
        "update_record": update_record,
    }


def build_soft_delete_record(memory_id: str, now_ts: int) -> MilvusRecord:
    """构造长期记忆软删除的 partial upsert payload。"""
    return build_partial_update_record(
        memory_id,
        updated_at=now_ts,
        is_deleted=True,
    )


def build_cluster_merge_plan(
    cluster: list[MilvusRecord],
    now_ts: int,
) -> ClusterMergePlan:
    """根据一个相似记忆簇生成合并计划。"""
    main_memory = max(cluster, key=lambda record: record.get("updated_at", 0))
    merged_content = merge_contents([record.get("content", "") for record in cluster])

    merged_record = dict(main_memory)
    merged_record["content"] = merged_content
    merged_record["updated_at"] = now_ts
    merged_record["hit_count"] = sum(record.get("hit_count", 0) or 0 for record in cluster)
    merged_record["last_hit_at"] = max(
        record.get("last_hit_at", 0) or 0 for record in cluster
    )

    return {
        "merged_record": merged_record,
        "merged_content": merged_content,
        "deleted_memory_ids": [
            str(record.get("memory_id"))
            for record in cluster
            if record.get("memory_id") != main_memory.get("memory_id")
        ],
    }
