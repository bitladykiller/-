"""应用配置。

v3.16: 拆分为基础设施配置和业务逻辑配置（关注点分离）。
InfrastructureSettings: 连接地址、端口、凭据等部署相关配置。
BusinessSettings: 服务选择、模型名称、embedding 类型等业务相关配置。

分离的好处：
- 不同部署环境（开发/测试/生产）只需替换 InfrastructureSettings
- 业务逻辑配置不随部署变化，可独立管理
"""
from enum import Enum
from pathlib import Path

from pydantic_settings import BaseSettings

# 获取项目根目录
ROOT_DIR = Path(__file__).parent.parent.parent
ENV_FILE = ROOT_DIR / ".env"


class ServiceType(str, Enum):
    """LLM 服务类型。"""
    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"


# ================================================================== #
# 基础设施配置 — 连接地址、端口、凭据
# ================================================================== #

class InfrastructureSettings(BaseSettings):
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

    class Config:
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"


# ================================================================== #
# 业务逻辑配置 — 服务选择、模型、行为参数
# ================================================================== #

class BusinessSettings(BaseSettings):
    """业务逻辑配置。

    这些值反映应用行为，不随部署环境变化。
    """

    # --- LLM 服务商 --- #
    # 注意：API Key 和 Base URL 虽来自外部服务，但它们是业务选型决定的
    DEEPSEEK_API_KEY: str
    DEEPSEEK_BASE_URL: str
    DEEPSEEK_MODEL: str

    # --- Vision Model（独立于 Chat 模型）--- #
    VISION_API_KEY: str
    VISION_BASE_URL: str
    VISION_MODEL: str

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

    class Config:
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"


# ================================================================== #
# 统一 Settings — 组合基础设施和业务配置
# ================================================================== #

class Settings(BaseSettings):
    """组合配置类 — 向后兼容。

    直接访问 settings.DEEPSEEK_API_KEY 等字段，透明委托给子配置。
    """

    def __init__(self):
        super().__init__()
        self._infra = InfrastructureSettings()
        self._business = BusinessSettings()

    # --- 基础设施属性（委托） --- #
    @property
    def DB_HOST(self): return self._infra.DB_HOST
    @property
    def DB_PORT(self): return self._infra.DB_PORT
    @property
    def DB_USER(self): return self._infra.DB_USER
    @property
    def DB_PASSWORD(self): return self._infra.DB_PASSWORD
    @property
    def DB_NAME(self): return self._infra.DB_NAME
    @property
    def NEO4J_URL(self): return self._infra.NEO4J_URL
    @property
    def NEO4J_USERNAME(self): return self._infra.NEO4J_USERNAME
    @property
    def NEO4J_PASSWORD(self): return self._infra.NEO4J_PASSWORD
    @property
    def NEO4J_DATABASE(self): return self._infra.NEO4J_DATABASE
    @property
    def REDIS_HOST(self): return self._infra.REDIS_HOST
    @property
    def REDIS_PORT(self): return self._infra.REDIS_PORT
    @property
    def REDIS_DB(self): return self._infra.REDIS_DB
    @property
    def REDIS_PASSWORD(self): return self._infra.REDIS_PASSWORD
    @property
    def MILVUS_HOST(self): return self._infra.MILVUS_HOST
    @property
    def MILVUS_PORT(self): return self._infra.MILVUS_PORT

    # --- 业务配置属性（委托） --- #
    @property
    def DEEPSEEK_API_KEY(self): return self._business.DEEPSEEK_API_KEY
    @property
    def DEEPSEEK_BASE_URL(self): return self._business.DEEPSEEK_BASE_URL
    @property
    def DEEPSEEK_MODEL(self): return self._business.DEEPSEEK_MODEL
    @property
    def VISION_API_KEY(self): return self._business.VISION_API_KEY
    @property
    def VISION_BASE_URL(self): return self._business.VISION_BASE_URL
    @property
    def VISION_MODEL(self): return self._business.VISION_MODEL
    @property
    def OLLAMA_BASE_URL(self): return self._business.OLLAMA_BASE_URL
    @property
    def OLLAMA_CHAT_MODEL(self): return self._business.OLLAMA_CHAT_MODEL
    @property
    def OLLAMA_REASON_MODEL(self): return self._business.OLLAMA_REASON_MODEL
    @property
    def OLLAMA_EMBEDDING_MODEL(self): return self._business.OLLAMA_EMBEDDING_MODEL
    @property
    def OLLAMA_AGENT_MODEL(self): return self._business.OLLAMA_AGENT_MODEL
    @property
    def EMBEDDING_TYPE(self): return self._business.EMBEDDING_TYPE
    @property
    def EMBEDDING_MODEL(self): return self._business.EMBEDDING_MODEL
    @property
    def CHAT_SERVICE(self): return self._business.CHAT_SERVICE
    @property
    def REASON_SERVICE(self): return self._business.REASON_SERVICE
    @property
    def AGENT_SERVICE(self): return self._business.AGENT_SERVICE
    @property
    def SERPAPI_KEY(self): return self._business.SERPAPI_KEY
    @property
    def SEARCH_RESULT_COUNT(self): return self._business.SEARCH_RESULT_COUNT
    @property
    def REDIS_CACHE_EXPIRE(self): return self._business.REDIS_CACHE_EXPIRE
    @property
    def REDIS_CACHE_THRESHOLD(self): return self._business.REDIS_CACHE_THRESHOLD
    @property
    def MILVUS_COLLECTION_NAME(self): return self._business.MILVUS_COLLECTION_NAME

    # --- 计算属性（组合多个子配置）--- #
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
    def NEO4J_CONN_URL(self) -> str:
        """Neo4j 连接 URL。"""
        return self.NEO4J_URL

    @property
    def MILVUS_URL(self) -> str:
        """Milvus 连接地址。"""
        return f"{self.MILVUS_HOST}:{self.MILVUS_PORT}"


settings = Settings()