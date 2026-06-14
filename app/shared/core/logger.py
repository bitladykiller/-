"""统一日志配置。

职责：
- 提供全局日志初始化入口
- 维持日志初始化幂等状态
- 暴露业务模块共用的 logger 获取与上下文格式化入口

边界：
- 纯日志格式、handler 策略和上下文拼装 helper 已收口到当前模块内部
- 这里不承载具体业务模块的日志字段约定
"""

from __future__ import annotations

from collections.abc import Iterator
import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_NOISY_LOGGERS = (
    "sqlalchemy.engine",
    "pymilvus.client",
    "pymilvus.milvus_client",
    "httpx",
    "httpcore",
    "urllib3",
    "asyncio",
)

_logging_initialized = False


def _has_log_value(value: object) -> bool:
    """判断上下文字段是否值得写入日志。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _iter_log_context_parts(**context: object) -> Iterator[str]:
    """遍历值得写入日志的上下文字段片段。"""
    for key, value in context.items():
        if _has_log_value(value):
            yield f"{key}={value}"


def format_log_context(**context: object) -> str:
    """把上下文字段拼成稳定日志片段，避免业务模块重复手写。"""
    return " ".join(_iter_log_context_parts(**context))


def _build_stream_handler(
    format_str: str,
    date_format: str,
) -> logging.StreamHandler:
    """构造统一格式的 stdout handler。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(format_str, datefmt=date_format))
    return handler


def _has_stream_handler(logger: logging.Logger) -> bool:
    """判断 logger 是否已经挂载了控制台输出 handler。"""
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler,
            logging.FileHandler,
        ):
            return True
    return False


def configure_noisy_loggers(level: int = logging.WARNING) -> None:
    """抑制第三方库的冗余日志输出。"""
    for logger_name in _NOISY_LOGGERS:
        logging.getLogger(logger_name).setLevel(level)


def configure_root_logger(
    root: logging.Logger,
    *,
    level: int,
    format_str: str,
    date_format: str,
) -> None:
    """对目标 root logger 应用统一级别和 handler 策略。"""
    root.setLevel(level)
    if not _has_stream_handler(root):
        root.addHandler(_build_stream_handler(format_str, date_format))


def setup_logging(
    level: int = logging.INFO,
    format_str: str = LOG_FORMAT,
    date_format: str = DATE_FORMAT,
) -> None:
    """初始化全局日志配置（幂等，多次调用不会重复添加 handler）。

    Args:
        level: 根 logger 的日志级别，默认 INFO。
        format_str: 日志格式字符串。
        date_format: 时间格式字符串。
    """
    global _logging_initialized
    if _logging_initialized:
        return

    root = logging.getLogger()
    configure_root_logger(
        root,
        level=level,
        format_str=format_str,
        date_format=date_format,
    )
    configure_noisy_loggers()

    _logging_initialized = True


def get_logger(name: str) -> logging.Logger:
    """获取一个遵循全局格式的 logger。

    Args:
        name: 通常传 __name__ 即可。
    """
    return logging.getLogger(name)


__all__ = ["setup_logging", "get_logger", "format_log_context"]
