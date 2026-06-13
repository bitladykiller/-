"""Core 包入口。

职责：
- 聚合配置、数据库和日志三个基础设施子模块
- 作为上层模块稳定导入常用基础设施对象的入口

边界：
- 这里只暴露基础设施对象，不直接承载业务实现
"""

from app.core.config import (
    BusinessSettings,
    InfrastructureSettings,
    ServiceType,
    Settings,
    settings,
)
from app.core.database import AsyncSessionLocal, Base, engine
from app.core.logger import format_log_context, get_logger, setup_logging

__all__ = [
    "ServiceType",
    "BusinessSettings",
    "InfrastructureSettings",
    "Settings",
    "settings",
    "AsyncSessionLocal",
    "Base",
    "engine",
    "setup_logging",
    "get_logger",
    "format_log_context",
]
