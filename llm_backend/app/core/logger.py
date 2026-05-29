"""
统一日志配置。

用法：
    from app.core.logger import get_logger
    logger = get_logger(__name__)
    logger.info("something happened", extra={"user_id": 123})
"""

from __future__ import annotations

import logging
import sys
from typing import Optional


LOG_FORMAT = (
    "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_logging_initialized = False


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

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(format_str, datefmt=date_format))

    root = logging.getLogger()
    root.setLevel(level)
    # 避免重复添加（uvicorn reload 场景）
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)

    # 抑制过于啰嗦的第三方库日志
    for noisy in (
        "sqlalchemy.engine",
        "pymilvus.client",
        "pymilvus.milvus_client",
        "httpx",
        "httpcore",
        "urllib3",
        "asyncio",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _logging_initialized = True


def get_logger(name: str) -> logging.Logger:
    """获取一个遵循全局格式的 logger。

    Args:
        name: 通常传 __name__ 即可。
    """
    return logging.getLogger(name)
