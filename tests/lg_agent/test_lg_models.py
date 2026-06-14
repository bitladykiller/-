import asyncio

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


def test_resolve_model_factory_switches_with_service_name(monkeypatch) -> None:
    monkeypatch.setattr(lg_models.settings, "AGENT_SERVICE", "deepseek")
    assert lg_models._resolve_model_factory() is lg_models._create_deepseek_model

    monkeypatch.setattr(lg_models.settings, "AGENT_SERVICE", "ollama")
    assert lg_models._resolve_model_factory() is lg_models._create_ollama_model


def test_get_model_caches_instances_by_role(monkeypatch) -> None:
    lg_models._models_cache.clear()
    created: list[tuple[str, float]] = []

    def fake_factory(temperature: float):
        created.append(("created", temperature))
        return {"temperature": temperature}

    monkeypatch.setattr(lg_models, "_resolve_model_factory", lambda: fake_factory)

    first = lg_models._get_model("agent", 0.7)
    second = lg_models._get_model("agent", 0.7)

    assert first is second
    assert created == [("created", 0.7)]
    lg_models._models_cache.clear()


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
