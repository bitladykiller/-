"""数据库基础设施入口。

这个模块只负责三件事：
1. 创建 SQLAlchemy 异步引擎
2. 暴露统一的异步会话工厂
3. 暴露声明式 Base，供模型定义共享

把这些对象集中在一个模块里，可以避免各业务服务各自拼接数据库配置。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.core.database_support import (
    build_engine_options,
    configure_sqlalchemy_logging,
    create_session_factory,
)


class Base(DeclarativeBase):
    """项目统一的声明式 ORM 基类。"""


configure_sqlalchemy_logging()

engine = create_async_engine(
    **build_engine_options(settings.DATABASE_URL),
)
AsyncSessionLocal = create_session_factory(engine)

__all__ = ["engine", "AsyncSessionLocal", "Base"]
