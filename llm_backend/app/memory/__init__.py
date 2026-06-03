"""
记忆模块。

STM = Short-Term Memory，短期记忆。
LTM = Long-Term Memory，长期记忆。

本模块提供智能客服 Agent 的记忆能力，包括：
1. 短期记忆：基于 Redis List 实现会话级滑动窗口
2. 长期记忆：基于 Milvus 存储用户画像、历史问题和有效解决方案
"""

from app.memory.config import (
    SHORT_TERM_MEMORY_CONFIG,
    LONG_TERM_MEMORY_CONFIG,
    LONG_TERM_MEMORY_TYPES,
)

from app.memory.schemas import (
    MessageRecord,
    SessionMeta,
    SessionSummary,
    LongTermMemory,
    MemorySearchResult,
    MemoryExtractorResult,
    AgentMemoryState,
)

from app.memory.redis_short_term_memory import RedisShortTermMemory
from app.memory.simple_long_term_memory import SimpleLongTermMemory
from app.memory.memory_extractor import MemoryExtractor
from app.memory.memory_middleware import MemoryMiddleware
__all__ = [
    # 配置
    "SHORT_TERM_MEMORY_CONFIG",
    "LONG_TERM_MEMORY_CONFIG",
    "LONG_TERM_MEMORY_TYPES",

    # 数据模型
    "MessageRecord",
    "SessionMeta",
    "SessionSummary",
    "LongTermMemory",
    "MemorySearchResult",
    "MemoryExtractorResult",
    "AgentMemoryState",

    # 核心类
    "RedisShortTermMemory",
    "SimpleLongTermMemory",
    "MemoryExtractor",
    "MemoryMiddleware",

    # v3.17 移除 "build_agent_prompt" — 未被任何生产代码调用，
    # Agent Prompt 构建已由 lg_context.py 的 build_memory_context 替代。
]