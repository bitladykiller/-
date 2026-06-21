"""应用配置统一入口 — 聚合配置模型和运行时行为配置。

职责：
- 继续承载 InfrastructureSettings + BusinessSettings 的组合字段代理
- 新增对 AppConfig（运行时行为配置）的统一引用
- 作为全局 settings 单例的提供者

边界：
- 真实字段模型仍位于 config_models.py
- 运行时行为常量收敛到 app_config.py
"""

from __future__ import annotations

from typing import Any

from app.shared.core.config_models import (
    BusinessSettings,
    InfrastructureSettings,
    ProjectBaseSettings,
)
from app.platform.config.app_config import AppConfig, app_config as _app_config


class _Settings:
    """组合配置实现，统一代理基础设施配置、业务配置和运行时行为配置。"""

    def __init__(
        self,
        *,
        infra: InfrastructureSettings | None = None,
        business: BusinessSettings | None = None,
        app_config: AppConfig | None = None,
    ) -> None:
        self._infra = infra or InfrastructureSettings()
        self._business = business or BusinessSettings()
        self._app_config = app_config or _app_config
        self._sources: tuple[ProjectBaseSettings, ...] = (
            self._infra,
            self._business,
        )

    def __getattr__(self, name: str) -> Any:
        """优先从基础设施配置，再从业务配置中解析字段。

        运行时行为配置字段以 'run_' 前缀探测：
        - 如果 name 以 'run_' 开头，且 app_config 中有对应字段，返回 app_config 的值
        - 否则走 infra → business 链
        """
        # 检查 app_config
        app_attr = name[len("run_") :] if name.startswith("run_") else None
        if app_attr is not None and hasattr(self._app_config, app_attr):
            return getattr(self._app_config, app_attr)

        for source in self._sources:
            model_fields = getattr(source.__class__, "model_fields", {})
            if name in model_fields:
                return getattr(source, name)
        raise AttributeError(f"'settings' object has no attribute '{name}'")

    @property
    def app_config(self) -> AppConfig:
        """返回运行时行为配置（React、上传、任务队列等）。"""
        return self._app_config

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
