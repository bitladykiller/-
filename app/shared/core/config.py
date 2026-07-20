"""应用配置统一入口 — 聚合配置模型和运行时行为配置。

职责：
- 承载 InfrastructureSettings + BusinessSettings 的组合字段代理
- 暴露 AppConfig 与连接 URL
- 作为全局 settings 单例的提供者

边界：
- 真实字段模型仍位于 config_models.py
- 运行时行为常量收敛到 app_config.py（请用 settings.app_config.xxx，勿再加一层无用的转发属性）
"""

from __future__ import annotations

from typing import Any

from app.shared.core.app_config import AppConfig
from app.shared.core.app_config import app_config as _app_config
from app.shared.core.config_models import (
    BusinessSettings,
    InfrastructureSettings,
    ProjectBaseSettings,
)


class _Settings:
    """组合配置：infra/business 经 __getattr__ 代理，行为配置走 app_config。"""

    def __init__(
        self,
        *,
        infra: InfrastructureSettings | None = None,
        business: BusinessSettings | None = None,
        app_config: AppConfig | None = None,
    ) -> None:
        # pydantic-settings 从 .env 注入必填字段；静态检查器无法感知该运行时行为
        self._infra = infra or InfrastructureSettings()  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
        self._business = business or BusinessSettings()  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
        self._app_config = app_config or _app_config
        self._sources: tuple[ProjectBaseSettings, ...] = (
            self._infra,
            self._business,
        )

    def __getattr__(self, name: str) -> Any:
        """从 infra → business 链中解析字段。"""
        for source in self._sources:
            model_fields = getattr(source.__class__, "model_fields", {})
            if name in model_fields:
                return getattr(source, name)
        raise AttributeError(f"'settings' object has no attribute '{name}'")

    @property
    def app_config(self) -> AppConfig:
        return self._app_config

    # ── 连接 URL（由 env 字段拼出，业务侧常用） ──

    @property
    def DATABASE_URL(self) -> str:  # noqa: N802
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def REDIS_URL(self) -> str:  # noqa: N802
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def MILVUS_URL(self) -> str:  # noqa: N802
        return f"{self.MILVUS_HOST}:{self.MILVUS_PORT}"


settings = _Settings()

__all__ = [
    "settings",
]
