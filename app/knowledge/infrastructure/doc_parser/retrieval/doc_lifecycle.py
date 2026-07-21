"""RAG 文档生命周期工具：软删过滤、doc_id 校验、版本号计算。

策略 2（软删除 + version）：
- 检索默认只看 is_deleted == false
- 更新文档时先软删旧 version，再插入新 version
- 过期软删记录由 hard_purge 物理删除
"""

from __future__ import annotations

import re
import time
from typing import Any

# 业务 doc_id：限制字符集，避免拼进 filter 表达式时被注入
_DOC_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")

# 活跃文档过滤（与 LTM 一致的语义）
ACTIVE_FILTER = "is_deleted == false"
SOFT_DELETED_FILTER = "is_deleted == true"

# 单次 query 上限（与 LTM soft_delete 对齐）
DEFAULT_QUERY_LIMIT = 16384


def now_ts() -> int:
    """Unix 秒级时间戳。"""
    return int(time.time())


def validate_doc_id(doc_id: str) -> str:
    """校验并返回规范化 doc_id。

    Raises:
        ValueError: 为空或字符集非法。
    """
    value = (doc_id or "").strip()
    if not value:
        raise ValueError("doc_id 不能为空")
    if not _DOC_ID_PATTERN.match(value):
        raise ValueError(
            "doc_id 仅允许字母、数字、下划线、点、冒号、连字符，且长度 1-64"
        )
    return value


def escape_milvus_string(value: str) -> str:
    """转义写入 Milvus boolean expression 的字符串字面量。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def doc_id_filter(doc_id: str, *, active_only: bool = False) -> str:
    """构造 doc_id 过滤表达式。"""
    safe = escape_milvus_string(validate_doc_id(doc_id))
    base = f'doc_id == "{safe}"'
    if active_only:
        return f"({base}) and ({ACTIVE_FILTER})"
    return base


def merge_active_filter(user_filter: str | None) -> str:
    """在用户 filter 上叠加「未软删」条件。"""
    if not user_filter or not str(user_filter).strip():
        return ACTIVE_FILTER
    return f"({ACTIVE_FILTER}) and ({str(user_filter).strip()})"


def next_version(max_version: int | None) -> int:
    """计算下一版本文档版本号（从 1 起）。"""
    current = int(max_version or 0)
    if current < 0:
        current = 0
    return current + 1


def build_soft_delete_record(chunk_id: str, *, updated_at: int | None = None) -> dict[str, Any]:
    """构造 chunk 软删 partial upsert 记录。"""
    return {
        "chunk_id": chunk_id,
        "is_deleted": True,
        "updated_at": int(updated_at if updated_at is not None else now_ts()),
    }


def hard_purge_filter(*, cutoff_ts: int) -> str:
    """软删且 updated_at 早于 cutoff 的物理删除候选。"""
    return f"{SOFT_DELETED_FILTER} and updated_at < {int(cutoff_ts)}"


__all__ = [
    "ACTIVE_FILTER",
    "DEFAULT_QUERY_LIMIT",
    "SOFT_DELETED_FILTER",
    "build_soft_delete_record",
    "doc_id_filter",
    "escape_milvus_string",
    "hard_purge_filter",
    "merge_active_filter",
    "next_version",
    "now_ts",
    "validate_doc_id",
]
