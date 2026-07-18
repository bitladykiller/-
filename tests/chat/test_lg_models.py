import asyncio
import sys
from types import SimpleNamespace

import app.chat.infrastructure.modeling.models as lg_models


class DummyModel:
    def __init__(self) -> None:
        self.answer = "ok"


class AwaitableDummyModel:
    def __init__(self, value: str) -> None:
        self.value = value

    def __await__(self):
        async def _resolve() -> str:
            return self.value

        return _resolve().__await__()


def test_get_model_uses_provider_specific_client(monkeypatch) -> None:
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
    lg_models._create_model("agent", 0.3)

    monkeypatch.setattr(lg_models.settings, "AGENT_SERVICE", "ollama")
    lg_models._create_model("router", 0.6)

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


def test_create_model_creates_new_instance_each_call(monkeypatch) -> None:
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

    first = lg_models._create_model("agent", 0.7)
    second = lg_models._create_model("agent", 0.7)

    assert first is not second
    assert created == [
        {"model": "qwen3", "base_url": "http://ollama.local", "temperature": 0.7},
        {"model": "qwen3", "base_url": "http://ollama.local", "temperature": 0.7},
    ]


def test_lazy_model_proxy_delegates_attribute_access_and_repr(monkeypatch) -> None:
    monkeypatch.setattr(lg_models, "_get_model", lambda name, temperature: DummyModel())
    lazy_model = lg_models.LazyModelProxy(
        "router",
        lg_models.MODEL_TEMPERATURES["router"],
        lg_models._get_model,
    )

    assert lazy_model.answer == "ok"
    assert bool(lazy_model) is True
    assert repr(lazy_model) == "_LazyModel(name=router, t=0.1)"


def test_lazy_model_proxy_delegates_await(monkeypatch) -> None:
    monkeypatch.setattr(
        lg_models,
        "_get_model",
        lambda name, temperature: AwaitableDummyModel("awaited"),
    )
    lazy_model = lg_models.LazyModelProxy(
        "agent",
        lg_models.MODEL_TEMPERATURES["agent"],
        lg_models._get_model,
    )

    async def _await_value() -> str:
        return await lazy_model

    assert asyncio.run(_await_value()) == "awaited"
