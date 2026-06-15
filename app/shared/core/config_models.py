"""配置模型定义。

职责：
- 定义基础设施与业务设置的数据模型
- 集中管理 `.env` 路径与通用 `BaseSettings` 配置

边界：
- 不负责拼接连接 URL
- 不负责全局 `settings` 单例
- 不负责多来源字段代理
"""

from enum import Enum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_SETTINGS_CONFIG = SettingsConfigDict(
    env_file=str(Path(__file__).parent.parent.parent / ".env"),
    env_file_encoding="utf-8",
    case_sensitive=True,
    extra="ignore",
)


class ServiceType(str, Enum):
    """LLM 服务类型。"""

    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"


class InfrastructureSettings(BaseSettings):
    """基础设施连接配置。"""

    model_config = PROJECT_SETTINGS_CONFIG

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


class BusinessSettings(BaseSettings):
    """业务行为配置。"""

    model_config = PROJECT_SETTINGS_CONFIG

    DEEPSEEK_API_KEY: str
    DEEPSEEK_MODEL: str

    OLLAMA_BASE_URL: str
    OLLAMA_EMBEDDING_MODEL: str = "bge-m3"
    OLLAMA_AGENT_MODEL: str

    EMBEDDING_TYPE: str = "ollama"
    EMBEDDING_MODEL: str = "bge-m3"

    AGENT_SERVICE: ServiceType = ServiceType.DEEPSEEK
