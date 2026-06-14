"""配置模型定义。

职责：
- 定义基础设施与业务设置的数据模型
- 集中管理 `.env` 路径与通用 `BaseSettings` 配置

边界：
- 不负责拼接连接 URL
- 不负责全局 `settings` 单例
- 不负责多来源字段代理
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).parent.parent.parent
ENV_FILE = ROOT_DIR / ".env"
PROJECT_SETTINGS_CONFIG = SettingsConfigDict(
    env_file=str(ENV_FILE),
    env_file_encoding="utf-8",
    case_sensitive=True,
    extra="ignore",
)


class ServiceType(str, Enum):
    """LLM 服务类型。"""

    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"


class ProjectBaseSettings(BaseSettings):
    """项目统一的 BaseSettings 基类。"""

    model_config = PROJECT_SETTINGS_CONFIG


class InfrastructureSettings(ProjectBaseSettings):
    """基础设施连接配置。"""

    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    NEO4J_URL: str = "bolt://localhost:7687"
    NEO4J_USERNAME: str = "neo4j"
    NEO4J_PASSWORD: str = "password"
    NEO4J_DATABASE: str = "neo4j"

    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530


class BusinessSettings(ProjectBaseSettings):
    """业务行为配置。"""

    DEEPSEEK_API_KEY: str
    DEEPSEEK_BASE_URL: str
    DEEPSEEK_MODEL: str

    OLLAMA_BASE_URL: str
    OLLAMA_CHAT_MODEL: str
    OLLAMA_REASON_MODEL: str
    OLLAMA_EMBEDDING_MODEL: str = "bge-m3"
    OLLAMA_AGENT_MODEL: str

    EMBEDDING_TYPE: str = "ollama"
    EMBEDDING_MODEL: str = "bge-m3"

    CHAT_SERVICE: ServiceType = ServiceType.DEEPSEEK
    REASON_SERVICE: ServiceType = ServiceType.OLLAMA
    AGENT_SERVICE: ServiceType = ServiceType.DEEPSEEK

    SERPAPI_KEY: str
    SEARCH_RESULT_COUNT: int = 3

    REDIS_CACHE_EXPIRE: int = 3600
    REDIS_CACHE_THRESHOLD: float = 0.8

    MILVUS_COLLECTION_NAME: str = "customer_agent_long_memory"


__all__ = [
    "BusinessSettings",
    "InfrastructureSettings",
    "ServiceType",
]
