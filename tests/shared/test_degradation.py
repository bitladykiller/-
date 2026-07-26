"""降级日志分类单测。

这层存在的唯一理由：让"代码缺陷"不再伪装成"外部依赖抖动"。
所以断言重点是**级别与堆栈**，而不是文案。
"""

from __future__ import annotations

import asyncio

import redis.exceptions as redis_exceptions
from app.shared.core.degradation import (
    format_context,
    is_external_dependency_error,
    log_degradation,
)


class RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.exceptions: list[tuple[str, tuple[object, ...]]] = []

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message % args if args else message)

    def exception(self, message: str, *args: object) -> None:
        self.exceptions.append((message, args))


def test_external_dependency_errors_are_recognized() -> None:
    for exc in (
        redis_exceptions.ConnectionError("down"),
        redis_exceptions.TimeoutError("slow"),
        asyncio.TimeoutError(),
        TimeoutError(),
        ConnectionError(),
        OSError("socket"),
    ):
        assert is_external_dependency_error(exc) is True


def test_programming_errors_are_not_external() -> None:
    """这些正是历史上被 except Exception 吞掉的类型。"""
    for exc in (
        TypeError("'LTMSearchConfig' object is not subscriptable"),
        AttributeError("no attribute"),
        KeyError("top_k"),
        ValueError("bad"),
    ):
        assert is_external_dependency_error(exc) is False


def test_external_failure_logs_warning_without_stack() -> None:
    logger = RecordingLogger()

    handled = log_degradation(
        logger,  # type: ignore[arg-type]
        "stm.get_summary",
        redis_exceptions.ConnectionError("redis down"),
        session="s1",
    )

    assert handled is True
    assert logger.exceptions == []
    assert len(logger.warnings) == 1
    assert "stm.get_summary" in logger.warnings[0]
    assert "session=s1" in logger.warnings[0]


def test_unexpected_failure_logs_exception_with_stack() -> None:
    """非预期异常必须走 logger.exception —— 否则缺陷又会隐身。"""
    logger = RecordingLogger()

    handled = log_degradation(
        logger,  # type: ignore[arg-type]
        "ltm.hybrid_search",
        TypeError("'LTMSearchConfig' object is not subscriptable"),
        tenant="t1",
    )

    assert handled is False
    assert logger.warnings == []
    assert len(logger.exceptions) == 1
    template, args = logger.exceptions[0]
    rendered = template % args
    assert "ltm.hybrid_search" in rendered
    assert "疑似代码缺陷" in rendered
    assert "not subscriptable" in rendered


def test_format_context_skips_none_values() -> None:
    assert format_context(tenant="t1", user=None, session="s1") == "tenant=t1 session=s1"


def test_format_context_is_empty_without_context() -> None:
    assert format_context() == ""


def test_log_degradation_without_context_still_logs() -> None:
    logger = RecordingLogger()

    log_degradation(logger, "op", ConnectionError("x"))  # type: ignore[arg-type]

    assert logger.warnings == ["op 降级 | x"]
