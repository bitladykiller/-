import pytest

from app.core.config import BusinessSettings, InfrastructureSettings, ServiceType
from app.core.config_support import resolve_setting_from_sources


def _build_infrastructure_settings() -> InfrastructureSettings:
    return InfrastructureSettings(
        DB_HOST="mysql",
        DB_PORT=3306,
        DB_USER="root",
        DB_PASSWORD="1234",
        DB_NAME="kefu_agent",
        REDIS_HOST="redis",
        REDIS_PORT=6379,
    )


def _build_business_settings() -> BusinessSettings:
    return BusinessSettings(
        DEEPSEEK_API_KEY="key",
        DEEPSEEK_BASE_URL="https://api.deepseek.com",
        DEEPSEEK_MODEL="deepseek-chat",
        OLLAMA_BASE_URL="http://ollama:11434",
        OLLAMA_CHAT_MODEL="qwen2.5:32b",
        OLLAMA_REASON_MODEL="deepseek-r1:32b",
        OLLAMA_AGENT_MODEL="qwen2.5:32b",
        SERPAPI_KEY="serp-key",
        CHAT_SERVICE=ServiceType.DEEPSEEK,
        REASON_SERVICE=ServiceType.OLLAMA,
        AGENT_SERVICE=ServiceType.DEEPSEEK,
    )


def test_resolve_setting_from_sources_prefers_earlier_source() -> None:
    infra = _build_infrastructure_settings()
    business = _build_business_settings()

    assert resolve_setting_from_sources("DB_HOST", (infra, business)) == "mysql"
    assert resolve_setting_from_sources("DEEPSEEK_MODEL", (infra, business)) == "deepseek-chat"


def test_resolve_setting_from_sources_raises_for_unknown_field() -> None:
    infra = _build_infrastructure_settings()
    business = _build_business_settings()

    with pytest.raises(AttributeError):
        resolve_setting_from_sources("NOT_A_REAL_SETTING", (infra, business))
