"""记忆模块 facade。

对外暴露按能力分包后的统一入口，同时保留旧平铺模块的兼容路径。
"""

from app.memory.config import *  # noqa: F403
from app.memory.prompt_builder import (
    build_compression_prompt,
    build_memory_injection_prompt,
    build_summary_injection_prompt,
)
from app.memory.schemas import *  # noqa: F403
from app.memory.orchestration import MemoryExtractor, MemoryMiddleware
from app.memory.stm import RedisShortTermMemory
from app.memory.ltm import SimpleLongTermMemory

__all__ = [
    "RedisShortTermMemory",
    "SimpleLongTermMemory",
    "MemoryExtractor",
    "MemoryMiddleware",
    "build_compression_prompt",
    "build_memory_injection_prompt",
    "build_summary_injection_prompt",
]
