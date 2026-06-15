import app.shared.core.config as config_module
import pytest
from app.shared.core.config_models import (
    BusinessSettings,
    InfrastructureSettings,
    ServiceType,
)


def _build_infrastructure_settings() -> InfrastructureSettings:
    return InfrastructureSettings(
        DB_HOST="mysql",
        DB_PORT=3306,
        DB_USER="root",
        DB_PASSWORD="1234",
        DB_NAME="kefu_agent",
        REDIS_HOST="redis",
        REDIS_PORT=6379,
        REDIS_DB=1,
        REDIS_PASSWORD="secret",
        MILVUS_HOST="milvus",
        MILVUS_PORT=19530,
    )


def _build_business_settings() -> BusinessSettings:
    return BusinessSettings(
        DEEPSEEK_API_KEY="key",
        DEEPSEEK_MODEL="deepseek-chat",
        OLLAMA_BASE_URL="http://ollama:11434",
        OLLAMA_AGENT_MODEL="qwen2.5:32b",
        AGENT_SERVICE=ServiceType.DEEPSEEK,
    )


def _make_settings():
    settings = type(config_module.settings)()
    settings._infra = _build_infrastructure_settings()
    settings._business = _build_business_settings()
    return settings


def test_settings_proxies_sub_settings_and_computed_urls() -> None:
    settings = _make_settings()

    assert settings.DB_HOST == "mysql"
    assert settings.DEEPSEEK_MODEL == "deepseek-chat"
    assert settings.DATABASE_URL == "mysql+aiomysql://root:1234@mysql:3306/kefu_agent"
    assert settings.REDIS_URL == "redis://:secret@redis:6379/1"
    assert settings.MILVUS_URL == "milvus:19530"


def test_settings_redis_url_without_password_omits_auth_prefix() -> None:
    settings = _make_settings()
    settings._infra = settings._infra.model_copy(update={"REDIS_DB": 0, "REDIS_PASSWORD": ""})

    assert settings.REDIS_URL == "redis://redis:6379/0"


def test_settings_unknown_attribute_raises_attribute_error() -> None:
    settings = _make_settings()

    with pytest.raises(AttributeError):
        _ = settings.NOT_A_REAL_SETTING
