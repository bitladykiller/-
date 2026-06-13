"""长期记忆工具函数。

这个模块只放 `SimpleLongTermMemory` 仍在使用的纯函数：
- `cosine_similarity`：用于相似记忆聚类
- `merge_contents`：合并多条记忆内容
- `entity_to_memory`：把 Milvus 记录转换为领域模型

这里不放 Milvus 连接、日志或检索编排，避免工具层重新膨胀成第二个存储类。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from app.memory.schemas import LongTermMemory

_FieldValue = TypeVar("_FieldValue")


def _entity_value(
    entity: Mapping[str, Any],
    key: str,
    default: _FieldValue,
) -> Any | _FieldValue:
    """读取 Milvus 记录字段；缺失或为 None 时回退默认值。"""
    value = entity.get(key)
    return default if value is None else value


def cosine_similarity(vec1: Sequence[float], vec2: Sequence[float]) -> float:
    """计算两个向量的余弦相似度。"""
    import numpy as np

    vec1_arr = np.array(vec1)
    vec2_arr = np.array(vec2)

    dot_product = np.dot(vec1_arr, vec2_arr)
    norm1 = np.linalg.norm(vec1_arr)
    norm2 = np.linalg.norm(vec2_arr)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return float(dot_product / (norm1 * norm2))


def merge_contents(contents: Sequence[str]) -> str:
    """合并多个内容为一个。

    使用 `dict.fromkeys` 保留原始顺序，同时去除空字符串和重复项，
    避免 `set()` 带来的随机顺序影响人工排查与后续 embedding 稳定性。
    """
    unique_contents = list(dict.fromkeys(content for content in contents if content))
    return "；".join(unique_contents)


def entity_to_memory(entity: Mapping[str, Any]) -> LongTermMemory:
    """将 Milvus entity 字典转换为 LongTermMemory 对象。"""
    return LongTermMemory(
        memory_id=_entity_value(entity, "memory_id", ""),
        tenant_id=_entity_value(entity, "tenant_id", ""),
        user_id=_entity_value(entity, "user_id", ""),
        memory_type=_entity_value(entity, "memory_type", ""),
        content=_entity_value(entity, "content", ""),
        created_at=_entity_value(entity, "created_at", 0),
        updated_at=_entity_value(entity, "updated_at", 0),
        last_hit_at=_entity_value(entity, "last_hit_at", 0),
        hit_count=_entity_value(entity, "hit_count", 0),
        is_deleted=_entity_value(entity, "is_deleted", False),
    )
