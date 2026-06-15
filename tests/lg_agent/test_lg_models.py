import sys
from types import SimpleNamespace

import app.chat.infrastructure.modeling.models as lg_models


class DummyModel:
    def __init__(self) -> None:
        self.answer = "ok"


def test_get_model_uses_provider_specific_client(monkeypatch) -> None:
    lg_models._models_cache.clear()
    deepseek_calls: list[dict] = []
    ollama_calls: list[dict] = []

    class FakeDeepSeek:
        def __init__(self, **kwargs) -> None:
            deepseek_calls.append(kwargs)

    class FakeOllama:
        def __init__(self, **kwargs) -> None:
            ollama_calls.append(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "langchain_deepseek",
        SimpleNamespace(ChatDeepSeek=FakeDeepSeek),
    )
    monkeypatch.setitem(
        sys.modules,
        "langchain_ollama",
        SimpleNamespace(ChatOllama=FakeOllama),
    )
    monkeypatch.setattr(lg_models.settings, "DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setattr(lg_models.settings, "DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setattr(lg_models.settings, "OLLAMA_AGENT_MODEL", "qwen3")
    monkeypatch.setattr(lg_models.settings, "OLLAMA_BASE_URL", "http://ollama.local")

    monkeypatch.setattr(lg_models.settings, "AGENT_SERVICE", "deepseek")
    lg_models._get_model("agent", 0.3)
    lg_models._models_cache.clear()

    monkeypatch.setattr(lg_models.settings, "AGENT_SERVICE", "ollama")
    lg_models._get_model("router", 0.6)

    assert deepseek_calls == [
        {
            "api_key": "deepseek-key",
            "model_name": "deepseek-chat",
            "temperature": 0.3,
        }
    ]
    assert ollama_calls == [
        {
            "model": "qwen3",
            "base_url": "http://ollama.local",
            "temperature": 0.6,
        }
    ]
    lg_models._models_cache.clear()


def test_get_model_caches_instances_by_role(monkeypatch) -> None:
    lg_models._models_cache.clear()
    created: list[dict] = []

    class FakeOllama:
        def __init__(self, **kwargs) -> None:
            created.append(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "langchain_ollama",
        SimpleNamespace(ChatOllama=FakeOllama),
    )
    monkeypatch.setattr(lg_models.settings, "AGENT_SERVICE", "ollama")
    monkeypatch.setattr(lg_models.settings, "OLLAMA_AGENT_MODEL", "qwen3")
    monkeypatch.setattr(lg_models.settings, "OLLAMA_BASE_URL", "http://ollama.local")

    first = lg_models._get_model("agent", 0.7)
    second = lg_models._get_model("agent", 0.7)

    assert first is second
    assert created == [
        {
            "model": "qwen3",
            "base_url": "http://ollama.local",
            "temperature": 0.7,
        }
    ]
    lg_models._models_cache.clear()


def test_lazy_model_proxy_delegates_attribute_access(monkeypatch) -> None:
    monkeypatch.setattr(lg_models, "_get_model", lambda *_args: DummyModel())
    lazy_model = lg_models.LazyModelProxy(
        "router",
        0.1,
    )

    assert lazy_model.answer == "ok"
