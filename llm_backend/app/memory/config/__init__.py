"""记忆模块静态配置。

这个模块负责：
- 集中声明 STM（Short-Term Memory，短期记忆）和
  LTM（Long-Term Memory，长期记忆）的默认参数
- 给 Redis 窗口、压缩策略、Milvus 检索策略提供唯一配置源

这个模块不负责：
- 运行时读取环境变量
- Redis / Milvus 客户端初始化
- 具体的记忆读写逻辑
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TypedDict, cast

from app.memory.memory_config_defaults import (
    LONG_TERM_MEMORY_CONFIG as _LONG_TERM_MEMORY_CONFIG,
    LONG_TERM_MEMORY_TYPES as _LONG_TERM_MEMORY_TYPES,
    SENSITIVE_PATTERNS,
    SHORT_TERM_MEMORY_CONFIG as _SHORT_TERM_MEMORY_CONFIG,
)


class ShortTermRedisConfig(TypedDict):
    """短期记忆的 Redis 相关配置。"""

    key_prefix: str
    ttl_seconds: int
    lock_ttl_seconds: int


class ShortTermWindowConfig(TypedDict):
    """短期记忆的消息窗口配置。"""

    max_messages: int


class ShortTermCompressionConfig(TypedDict):
    """短期记忆压缩阈值配置。"""

    enabled: bool
    trigger_rounds: int
    trigger_messages: int
    keep_recent_rounds: int


class ShortTermMemoryConfig(TypedDict):
    """短期记忆总配置。"""

    enabled: bool
    redis: ShortTermRedisConfig
    window: ShortTermWindowConfig
    compression: ShortTermCompressionConfig
    time_window_seconds: int


class LongTermSearchConfig(TypedDict):
    """长期记忆检索配置。"""

    top_k: int
    score_threshold: float


class LongTermDeduplicationConfig(TypedDict):
    """长期记忆去重配置。"""

    top_k: int
    similarity_threshold: float


class LongTermUpdateOnHitConfig(TypedDict):
    """长期记忆命中后更新策略。"""

    enabled: bool
    update_last_hit_at: bool
    increase_hit_count: bool


class LongTermMemoryConfig(TypedDict):
    """长期记忆总配置。"""

    enabled: bool
    collection_name: str
    search: LongTermSearchConfig
    deduplication: LongTermDeduplicationConfig
    update_on_hit: LongTermUpdateOnHitConfig


class LongTermMemoryTypes(TypedDict):
    """长期记忆类型映射。"""

    ISSUE_HISTORY: str
    SOLUTION_NOTE: str


SHORT_TERM_MEMORY_CONFIG: ShortTermMemoryConfig = cast(
    ShortTermMemoryConfig,
    _SHORT_TERM_MEMORY_CONFIG,
)
LONG_TERM_MEMORY_CONFIG: LongTermMemoryConfig = cast(
    LongTermMemoryConfig,
    _LONG_TERM_MEMORY_CONFIG,
)
LONG_TERM_MEMORY_TYPES: LongTermMemoryTypes = cast(
    LongTermMemoryTypes,
    _LONG_TERM_MEMORY_TYPES,
)


def short_term_config() -> ShortTermMemoryConfig:
    """返回短期记忆总配置。"""
    return SHORT_TERM_MEMORY_CONFIG


def short_term_redis_config() -> ShortTermRedisConfig:
    """返回短期记忆的 Redis 子配置。"""
    return SHORT_TERM_MEMORY_CONFIG["redis"]


def short_term_window_config() -> ShortTermWindowConfig:
    """返回短期记忆的窗口子配置。"""
    return SHORT_TERM_MEMORY_CONFIG["window"]


def short_term_compression_config() -> ShortTermCompressionConfig:
    """返回短期记忆的压缩子配置。"""
    return SHORT_TERM_MEMORY_CONFIG["compression"]


def long_term_config() -> LongTermMemoryConfig:
    """返回长期记忆总配置。"""
    return LONG_TERM_MEMORY_CONFIG


def long_term_collection_name() -> str:
    """返回长期记忆 collection 名称。"""
    return LONG_TERM_MEMORY_CONFIG["collection_name"]


def long_term_search_config() -> LongTermSearchConfig:
    """返回长期记忆检索子配置。"""
    return LONG_TERM_MEMORY_CONFIG["search"]


def long_term_deduplication_config() -> LongTermDeduplicationConfig:
    """返回长期记忆去重子配置。"""
    return LONG_TERM_MEMORY_CONFIG["deduplication"]


def long_term_update_on_hit_config() -> LongTermUpdateOnHitConfig:
    """返回长期记忆命中更新子配置。"""
    return LONG_TERM_MEMORY_CONFIG["update_on_hit"]


@lru_cache(maxsize=1)
def long_term_memory_type_values() -> frozenset[str]:
    """返回允许落入向量长期记忆的类型集合。"""
    return frozenset(LONG_TERM_MEMORY_TYPES.values())


@lru_cache(maxsize=1)
def compiled_sensitive_patterns() -> tuple[re.Pattern[str], ...]:
    """返回长期记忆敏感词规则的编译结果。"""
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in SENSITIVE_PATTERNS)

__all__ = [
    "SHORT_TERM_MEMORY_CONFIG",
    "LONG_TERM_MEMORY_CONFIG",
    "LONG_TERM_MEMORY_TYPES",
    "SENSITIVE_PATTERNS",
    "ShortTermRedisConfig",
    "ShortTermWindowConfig",
    "ShortTermCompressionConfig",
    "ShortTermMemoryConfig",
    "LongTermSearchConfig",
    "LongTermDeduplicationConfig",
    "LongTermUpdateOnHitConfig",
    "LongTermMemoryConfig",
    "LongTermMemoryTypes",
    "short_term_config",
    "short_term_redis_config",
    "short_term_window_config",
    "short_term_compression_config",
    "long_term_config",
    "long_term_collection_name",
    "long_term_search_config",
    "long_term_deduplication_config",
    "long_term_update_on_hit_config",
    "long_term_memory_type_values",
    "compiled_sensitive_patterns",
]
