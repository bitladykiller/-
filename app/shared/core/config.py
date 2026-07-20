"""应用配置统一入口 — 聚合配置模型和运行时行为配置。

职责：
- 承载 InfrastructureSettings + BusinessSettings 的组合字段代理
- 对 AppConfig（运行时行为配置）提供显式属性访问
- 作为全局 settings 单例的提供者

边界：
- 真实字段模型仍位于 config_models.py
- 运行时行为常量收敛到 app_config.py
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
    """组合配置实现，统一代理基础设施配置、业务配置和运行时行为配置。"""

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

    # ── 运行时行为配置（显式属性，不再用 run_ 前缀魔法） ──

    @property
    def app_config(self) -> AppConfig:
        return self._app_config

    @property
    def react_max_attempts(self) -> int:
        return self._app_config.react.max_attempts

    @property
    def react_recursion_limit(self) -> int:
        return self._app_config.react.recursion_limit

    @property
    def react_transcript_window(self) -> int:
        return self._app_config.react.transcript_window

    @property
    def react_progress_message(self) -> str:
        return self._app_config.react.progress_message

    @property
    def react_fallback_answer(self) -> str:
        return self._app_config.react.fallback_answer

    @property
    def react_retry_prompt(self) -> str:
        return self._app_config.react.retry_prompt

    @property
    def react_step_exhausted_marker(self) -> str:
        return self._app_config.react.step_exhausted_marker

    @property
    def react_step_exhausted_reason(self) -> str:
        return self._app_config.react.step_exhausted_reason

    @property
    def react_default_insufficiency_reason(self) -> str:
        return self._app_config.react.default_insufficiency_reason

    @property
    def react_initial_reason(self) -> str:
        return self._app_config.react.initial_reason

    @property
    def upload_max_upload_size_mb(self) -> int:
        return self._app_config.upload.max_upload_size_mb

    @property
    def upload_max_upload_size_bytes(self) -> int:
        return self._app_config.upload.max_upload_size_bytes

    @property
    def task_key_prefix(self) -> str:
        return self._app_config.task_queue.task_key_prefix

    @property
    def task_ttl_seconds(self) -> int:
        return self._app_config.task_queue.task_ttl_seconds

    @property
    def memory_extractor_temperature(self) -> float:
        return self._app_config.memory.memory_extractor_temperature

    @property
    def user_profile_cache_ttl(self) -> int:
        return self._app_config.memory.user_profile_cache_ttl

    # ── STM/LTM 快捷属性 ──

    @property
    def stm_enabled(self) -> bool:
        return self._app_config.memory.stm.enabled

    @property
    def ltm_enabled(self) -> bool:
        return self._app_config.memory.ltm.enabled

    @property
    def ltm_collection_name(self) -> str:
        return self._app_config.memory.ltm.collection_name

    # ── 连接 URL 计算属性 ──

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
