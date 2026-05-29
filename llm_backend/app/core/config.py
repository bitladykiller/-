from pydantic_settings import BaseSettings
from enum import Enum
from pathlib import Path

# 获取项目根目录
ROOT_DIR = Path(__file__).parent.parent.parent
ENV_FILE = ROOT_DIR / ".env"

class ServiceType(str, Enum):
    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"

class Settings(BaseSettings):
    # Deepseek settings
    DEEPSEEK_API_KEY: str
    DEEPSEEK_BASE_URL: str
    DEEPSEEK_MODEL: str
    
    # Vision Model settings (独立配置)
    VISION_API_KEY: str
    VISION_BASE_URL: str
    VISION_MODEL: str
    
    # Ollama settings
    OLLAMA_BASE_URL: str
    OLLAMA_CHAT_MODEL: str
    OLLAMA_REASON_MODEL: str
    OLLAMA_EMBEDDING_MODEL: str = "bge-m3"
    OLLAMA_AGENT_MODEL: str

    # Embedding 配置
    EMBEDDING_TYPE: str = "ollama"
    EMBEDDING_MODEL: str = "bge-m3"

    # Service selection
    CHAT_SERVICE: ServiceType = ServiceType.DEEPSEEK
    REASON_SERVICE: ServiceType = ServiceType.OLLAMA
    AGENT_SERVICE: ServiceType = ServiceType.DEEPSEEK
    
    # Search settings
    SERPAPI_KEY: str
    SEARCH_RESULT_COUNT: int = 3
    
    # Database settings
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    
    # Neo4j settings
    NEO4J_URL: str = "bolt://localhost:7687"
    NEO4J_USERNAME: str = "neo4j"
    NEO4J_PASSWORD: str = "password"
    NEO4J_DATABASE: str = "neo4j"
    
    # Redis settings
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    REDIS_CACHE_EXPIRE: int = 3600
    REDIS_CACHE_THRESHOLD: float = 0.8
    
    # Milvus settings
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_COLLECTION_NAME: str = "customer_agent_long_memory"
    
    # RAG 文档检索 settings（替代原 GraphRAG）
    
    @property
    def DATABASE_URL(self) -> str:
        return f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
    
    @property
    def REDIS_URL(self) -> str:
        """构建Redis URL"""
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    @property
    def NEO4J_CONN_URL(self) -> str:
        """构建Neo4j连接URL"""
        return f"{self.NEO4J_URL}"
    
    @property
    def MILVUS_URL(self) -> str:
        """构建Milvus连接URL"""
        return f"{self.MILVUS_HOST}:{self.MILVUS_PORT}"
    
    class Config:
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"

settings = Settings() 