"""应用配置入口。

职责：
- 作为 `app.shared.core.config` 的稳定导入路径
- 聚合设置模型、运行时门面和 URL helper 的公开 API

边界：
- 真实字段模型位于 `config_models.py`
- 这里直接承载运行时组合逻辑和全局 `settings` 单例
"""

from __future__ import annotations

from typing import Any

from app.shared.core.config_models import (
    BusinessSettings,
    InfrastructureSettings,
    ProjectBaseSettings,
)


class _Settings:
    """组合配置实现，统一代理基础设施与业务配置。"""

    def __init__(
        self,
        *,
        infra: InfrastructureSettings | None = None,
        business: BusinessSettings | None = None,
    ) -> None:
        self._infra = infra or InfrastructureSettings()
        self._business = business or BusinessSettings()
        self._sources: tuple[ProjectBaseSettings, ...] = (
            self._infra,
            self._business,
        )

    def __getattr__(self, name: str) -> Any:
        """优先从基础设施配置，再从业务配置中解析字段。"""
        for source in self._sources:
            model_fields = getattr(source.__class__, "model_fields", {})
            if name in model_fields:
                return getattr(source, name)
        raise AttributeError(f"'settings' object has no attribute '{name}'")

    @property
    def DATABASE_URL(self) -> str:
        """构建 MySQL 异步连接 URL。"""
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def REDIS_URL(self) -> str:
        """构建 Redis 连接 URL。"""
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def MILVUS_URL(self) -> str:
        """构建 Milvus 连接地址。"""
        return f"{self.MILVUS_HOST}:{self.MILVUS_PORT}"


settings = _Settings()

__all__ = [
    "settings",
]
