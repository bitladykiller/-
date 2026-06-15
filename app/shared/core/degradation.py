"""降级日志约定 — 区分"外部依赖抖动"和"我们自己写错了"。

这个模块负责：
- 定义什么算可预期的外部依赖故障
- 按类别选择日志级别与是否打堆栈

这个模块不负责：
- 决定业务上要降级成什么值（由调用方给兜底值）
- 重试与熔断

WHY 需要这层区分：
记忆、检索这些增强能力都遵循"失败就降级、别让主对话挂掉"的原则，写法上就是
`except Exception: return []`。问题在于它把两类完全不同的事情混为一谈：

- Redis 抖一下、Milvus 超时 —— 预期内，降级就是正确处理，不该刷屏告警
- 我们自己代码写错了（拼错字段名、把 dataclass 当 dict 用）—— 严重缺陷，
  却被同一个 except 吞掉，只留一行没有堆栈的 debug 日志

这个项目真实踩过后者：LTM 的配置是 frozen dataclass，代码却按 dict 下标取值，
每次调用都抛 TypeError，被宽泛 except 吞掉后表现为"长期记忆静默失效"——
没有任何报错，只是永远检索不到、也永远写不进去。

约定：外部故障 → `warning`，不打堆栈；其余一律 `exception`，带完整堆栈，
这样"代码缺陷"在日志里永远是可发现、可告警的。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import redis.exceptions as redis_exceptions

#: 可预期的外部依赖故障。命中这些说明是环境问题，不是代码缺陷。
EXTERNAL_DEPENDENCY_ERRORS: tuple[type[BaseException], ...] = (
    redis_exceptions.RedisError,
    asyncio.TimeoutError,
    TimeoutError,
    ConnectionError,
    OSError,
)


def is_external_dependency_error(exc: BaseException) -> bool:
    """判断异常是否属于可预期的外部依赖故障。"""
    return isinstance(exc, EXTERNAL_DEPENDENCY_ERRORS)


def format_context(**context: Any) -> str:
    """把上下文键值对拼成稳定的日志片段。"""
    return " ".join(f"{key}={value}" for key, value in context.items() if value is not None)


def log_degradation(
    logger: logging.Logger,
    operation: str,
    exc: BaseException,
    **context: Any,
) -> bool:
    """记录一次降级，并按异常类别选择日志级别。

    Args:
        logger: 调用方模块的 logger。
        operation: 出问题的操作名，例如 `"stm.get_recent_messages"`。
        exc: 捕获到的异常。
        **context: 附加排障上下文（tenant / user / session 等）。

    Returns:
        True 表示这是可预期的外部依赖故障；False 表示疑似代码缺陷
        （已按 ERROR 级别带堆栈记录）。
    """
    suffix = format_context(**context)
    detail = f"{operation} 降级 | {suffix} | {exc}" if suffix else f"{operation} 降级 | {exc}"

    if is_external_dependency_error(exc):
        # 外部抖动：记录即可，不需要堆栈，也不该当成缺陷告警
        logger.warning(detail)
        return True

    # 非预期异常几乎都是代码缺陷，必须带堆栈，便于告警与定位
    logger.exception("%s（非预期异常，疑似代码缺陷）", detail)
    return False


__all__ = [
    "EXTERNAL_DEPENDENCY_ERRORS",
    "format_context",
    "is_external_dependency_error",
    "log_degradation",
]
