"""数据库模块共享 helper。

职责：
- 收敛 SQLAlchemy 引擎参数和会话工厂样板
- 收敛 SQLAlchemy 自身日志级别配置

边界：
- 不直接读取全局 settings
- 不暴露项目级 engine / session 单例
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

SQLALCHEMY_LOGGER_NAME = "sqlalchemy.engine"
POOL_SIZE = 5
MAX_OVERFLOW = 10
SESSION_EXPIRE_ON_COMMIT = False


def configure_sqlalchemy_logging(
    *,
    logger_name: str = SQLALCHEMY_LOGGER_NAME,
    level: int = logging.WARNING,
) -> None:
    """压低 SQLAlchemy 自身日志，避免日常开发被 SQL 明细刷屏。"""
    logging.getLogger(logger_name).setLevel(level)


def build_engine_options(
    database_url: str,
    *,
    pool_size: int = POOL_SIZE,
    max_overflow: int = MAX_OVERFLOW,
) -> dict[str, Any]:
    """构造项目统一的异步引擎参数。"""
    return {
        "url": database_url,
        "echo": False,
        "pool_pre_ping": True,
        "pool_size": pool_size,
        "max_overflow": max_overflow,
    }


def create_session_factory(
    engine: AsyncEngine,
    *,
    expire_on_commit: bool = SESSION_EXPIRE_ON_COMMIT,
) -> async_sessionmaker[AsyncSession]:
    """根据项目引擎构造统一异步会话工厂。"""
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=expire_on_commit,
    )


__all__ = [
    "MAX_OVERFLOW",
    "POOL_SIZE",
    "SESSION_EXPIRE_ON_COMMIT",
    "SQLALCHEMY_LOGGER_NAME",
    "build_engine_options",
    "configure_sqlalchemy_logging",
    "create_session_factory",
]
