"""统一日志配置。

职责：
- 提供全局日志初始化入口
- 维持日志初始化幂等状态
- 暴露业务模块共用的 logger 获取与上下文格式化入口

边界：
- 纯日志格式、handler 策略和上下文拼装 helper 已收口到当前模块内部
- 这里不承载具体业务模块的日志字段约定
"""

import logging
import sys

_logging_initialized = False


def format_log_context(**context: object) -> str:
    """把上下文字段拼成稳定日志片段，避免业务模块重复手写。"""
    parts: list[str] = []
    for key, value in context.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, set, dict)) and not value:
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def setup_logging() -> None:
    """初始化全局日志配置（幂等，多次调用不会重复添加 handler）。"""
    global _logging_initialized
    if _logging_initialized:
        return

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    has_stream_handler = any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in root.handlers
    )
    if not has_stream_handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(handler)

    for logger_name in (
        "sqlalchemy.engine",
        "pymilvus.client",
        "pymilvus.milvus_client",
        "httpx",
        "httpcore",
        "urllib3",
        "asyncio",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    _logging_initialized = True


def get_logger(name: str) -> logging.Logger:
    """获取一个遵循全局格式的 logger。

    Args:
        name: 通常传 __name__ 即可。
    """
    return logging.getLogger(name)
