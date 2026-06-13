"""日志模块共享 helper。

职责：
- 收敛日志格式常量和第三方噪声 logger 名单
- 承接 root logger 配置、handler 判定和上下文拼装等纯 helper

边界：
- 不持有全局初始化状态
- 不直接暴露 `setup_logging()` 这类应用级入口
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator


LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
NOISY_LOGGERS = (
    "sqlalchemy.engine",
    "pymilvus.client",
    "pymilvus.milvus_client",
    "httpx",
    "httpcore",
    "urllib3",
    "asyncio",
)


def build_stream_handler(
    format_str: str,
    date_format: str,
) -> logging.StreamHandler:
    """构造统一格式的 stdout handler。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(format_str, datefmt=date_format))
    return handler


def has_stream_handler(logger: logging.Logger) -> bool:
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
    for logger_name in NOISY_LOGGERS:
        logging.getLogger(logger_name).setLevel(level)


def has_log_value(value: object) -> bool:
    """判断上下文字段是否值得写入日志。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def iter_log_context_parts(**context: object) -> Iterator[str]:
    """遍历值得写入日志的上下文字段片段。"""
    for key, value in context.items():
        if has_log_value(value):
            yield f"{key}={value}"


def configure_root_logger(
    root: logging.Logger,
    *,
    level: int,
    format_str: str,
    date_format: str,
) -> None:
    """对目标 root logger 应用统一级别和 handler 策略。"""
    root.setLevel(level)
    if not has_stream_handler(root):
        root.addHandler(build_stream_handler(format_str, date_format))


def format_log_context(**context: object) -> str:
    """把上下文字段拼成稳定日志片段，避免业务模块重复手写。"""
    return " ".join(iter_log_context_parts(**context))


__all__ = [
    "DATE_FORMAT",
    "LOG_FORMAT",
    "NOISY_LOGGERS",
    "build_stream_handler",
    "configure_noisy_loggers",
    "configure_root_logger",
    "format_log_context",
    "has_log_value",
    "has_stream_handler",
    "iter_log_context_parts",
]
