"""统一日志配置。

职责：
- 提供全局日志初始化入口
- 维持日志初始化幂等状态
- 暴露业务模块共用的 logger 获取与上下文格式化入口

边界：
- 纯日志格式 / handler / context helper 已下沉到 `logger_support.py`
- 这里不承载具体业务模块的日志字段约定
"""

from __future__ import annotations

import logging
from app.core.logger_support import (
    DATE_FORMAT,
    LOG_FORMAT,
    configure_noisy_loggers,
    configure_root_logger,
    format_log_context,
)

_logging_initialized = False

_configure_root_logger = configure_root_logger
_configure_noisy_loggers = configure_noisy_loggers


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
