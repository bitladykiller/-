"""数据库基础设施入口。

这个模块只负责三件事：
1. 创建 SQLAlchemy 异步引擎
2. 暴露统一的异步会话工厂
3. 暴露声明式 Base，供模型定义共享

把这些对象集中在一个模块里，可以避免各业务服务各自拼接数据库配置。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.shared.core.config import settings


class Base(DeclarativeBase):
    """项目统一的声明式 ORM 基类。"""


logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)

__all__ = ["engine", "AsyncSessionLocal", "Base"]
