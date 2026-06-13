"""记忆模块包入口。

职责：
- 承载短期记忆、长期记忆、记忆抽取与中间件编排
- 暴露 Agent 运行时会用到的记忆配置、数据模型和核心类

边界：
- 只处理“记什么、怎么取、怎么压缩、怎么回写”
- 不处理 HTTP 协议细节，也不承载 LangGraph 节点编排
"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from app.memory.config import (
    LONG_TERM_MEMORY_CONFIG,
    LONG_TERM_MEMORY_TYPES,
    SHORT_TERM_MEMORY_CONFIG,
)
from app.memory.prompt_builder import (
    build_compression_prompt,
    build_memory_injection_prompt,
    build_summary_injection_prompt,
)
from app.memory.schemas import (
    AgentMemoryState,
    LongTermMemory,
    MemoryExtractorResult,
    MemorySearchResult,
    MessageRecord,
    SessionMeta,
    SessionSummary,
    UserProfileData,
    UserProfileFact,
    UserProfilePayload,
)

if TYPE_CHECKING:
    from app.memory.ltm.store import SimpleLongTermMemory
    from app.memory.orchestration.extractor import MemoryExtractor
    from app.memory.orchestration.middleware import MemoryMiddleware
    from app.memory.stm.store import RedisShortTermMemory

_LAZY_EXPORTS = {
    "RedisShortTermMemory": (
        "app.memory.stm.store",
        "RedisShortTermMemory",
    ),
    "SimpleLongTermMemory": (
        "app.memory.ltm.store",
        "SimpleLongTermMemory",
    ),
    "MemoryExtractor": (
        "app.memory.orchestration.extractor",
        "MemoryExtractor",
    ),
    "MemoryMiddleware": (
        "app.memory.orchestration.middleware",
        "MemoryMiddleware",
    ),
}


def __getattr__(name: str) -> Any:
    """按需导入重依赖对象，避免轻量场景触发完整依赖链。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'app.memory' has no attribute {name!r}")

    module_name, attr_name = target
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value

# `build_agent_prompt` 已移除；Agent Prompt 组装已收敛到
# `lg_agent/lg_memory_prompt.py` 的 `build_memory_context`。

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
    "UserProfileFact",
    "UserProfileData",
    "UserProfilePayload",

    # 核心类
    "RedisShortTermMemory",
    "SimpleLongTermMemory",
    "MemoryExtractor",
    "MemoryMiddleware",
    "build_compression_prompt",
    "build_memory_injection_prompt",
    "build_summary_injection_prompt",
]
