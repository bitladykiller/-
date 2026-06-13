"""应用配置。

职责：
- 承载基础设施连接配置，例如 MySQL / Redis / Neo4j / Milvus
- 承载业务行为配置，例如模型选择、检索数量和缓存阈值
- 通过统一 `settings` 对象向外暴露稳定访问入口

边界：
- 基础设施配置描述“连到哪里”
- 业务配置描述“应用怎么跑”
- 组合层只负责向后兼容，不承载业务逻辑
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.config_support import (
    build_database_url,
    build_milvus_url,
    build_redis_url,
    resolve_setting_from_sources,
)

# 获取项目根目录
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
    """项目统一的 BaseSettings 基类。

    把 `.env` 路径、大小写敏感和额外字段策略放在一处，
    避免 Infrastructure / Business 两个 settings 类各自重复一份配置。
    """
    model_config = PROJECT_SETTINGS_CONFIG


# ================================================================== #
# 基础设施配置 — 连接地址、端口、凭据
# ================================================================== #

class InfrastructureSettings(ProjectBaseSettings):
    """基础设施连接配置。

    这些值随部署环境（开发/测试/生产）变化，与环境绑定。
    """

    # --- 数据库 --- #
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    # --- Neo4j --- #
    NEO4J_URL: str = "bolt://localhost:7687"
    NEO4J_USERNAME: str = "neo4j"
    NEO4J_PASSWORD: str = "password"
    NEO4J_DATABASE: str = "neo4j"

    # --- Redis --- #
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # --- Milvus --- #
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530


# ================================================================== #
# 业务逻辑配置 — 服务选择、模型、行为参数
# ================================================================== #

class BusinessSettings(ProjectBaseSettings):
    """业务逻辑配置。

    这些值反映应用行为，不随部署环境变化。
    """

    # --- LLM 服务商 --- #
    # 注意：API Key 和 Base URL 虽来自外部服务，但它们是业务选型决定的
    DEEPSEEK_API_KEY: str
    DEEPSEEK_BASE_URL: str
    DEEPSEEK_MODEL: str

    # --- Ollama 本地模型 --- #
    OLLAMA_BASE_URL: str
    OLLAMA_CHAT_MODEL: str
    OLLAMA_REASON_MODEL: str
    OLLAMA_EMBEDDING_MODEL: str = "bge-m3"
    OLLAMA_AGENT_MODEL: str

    # --- Embedding --- #
    EMBEDDING_TYPE: str = "ollama"     # "ollama" | "huggingface"
    EMBEDDING_MODEL: str = "bge-m3"

    # --- 服务选择 --- #
    # 每个功能槽位可独立选择 DeepSeek 或 Ollama
    CHAT_SERVICE: ServiceType = ServiceType.DEEPSEEK      # 回复生成
    REASON_SERVICE: ServiceType = ServiceType.OLLAMA       # 深度推理
    AGENT_SERVICE: ServiceType = ServiceType.DEEPSEEK      # Agent 调用

    # --- 搜索 --- #
    SERPAPI_KEY: str
    SEARCH_RESULT_COUNT: int = 3

    # --- 缓存 --- #
    REDIS_CACHE_EXPIRE: int = 3600         # Redis 缓存 TTL（秒）
    REDIS_CACHE_THRESHOLD: float = 0.8     # 相似度缓存命中阈值

    # --- Milvus Collection --- #
    MILVUS_COLLECTION_NAME: str = "customer_agent_long_memory"

# ================================================================== #
# 统一 Settings — 组合基础设施和业务配置
# ================================================================== #

class Settings:
    """组合配置类 — 向后兼容。

    WHY：
    对外继续暴露单一 `settings` 入口，内部再把基础设施配置和业务配置拆开，
    这样既保留原有调用方式，也避免为每个字段重复写代理属性。

    属性查找顺序：InfrastructureSettings → BusinessSettings → 计算属性。
    """

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
        """代理到子配置，优先 Infrastructure → Business。"""
        return resolve_setting_from_sources(name, self._sources)

    # ---------------------------------------------------------------- #
    # 计算属性 — 组合多个子配置字段的值
    # ---------------------------------------------------------------- #

    @property
    def DATABASE_URL(self) -> str:
        """构建 MySQL 异步连接 URL。"""
        return build_database_url(
            host=self.DB_HOST,
            port=self.DB_PORT,
            user=self.DB_USER,
            password=self.DB_PASSWORD,
            database=self.DB_NAME,
        )

    @property
    def REDIS_URL(self) -> str:
        """构建 Redis 连接 URL。"""
        return build_redis_url(
            host=self.REDIS_HOST,
            port=self.REDIS_PORT,
            db=self.REDIS_DB,
            password=self.REDIS_PASSWORD,
        )

    @property
    def MILVUS_URL(self) -> str:
        """Milvus 连接地址。"""
        return build_milvus_url(
            host=self.MILVUS_HOST,
            port=self.MILVUS_PORT,
        )


settings = Settings()
