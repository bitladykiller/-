from app.lg_agent.lg_model_support import (
    build_lazy_model,
    get_or_create_cached_model,
    resolve_model_factory,
)


class DummyModel:
    def __init__(self) -> None:
        self.answer = "ok"


def test_resolve_model_factory_switches_with_service_name() -> None:
    deepseek_factory = lambda temperature: ("deepseek", temperature)
    ollama_factory = lambda temperature: ("ollama", temperature)

    assert resolve_model_factory(
        "deepseek",
        deepseek_factory=deepseek_factory,
        ollama_factory=ollama_factory,
    ) is deepseek_factory
    assert resolve_model_factory(
        "ollama",
        deepseek_factory=deepseek_factory,
        ollama_factory=ollama_factory,
    ) is ollama_factory


def test_get_or_create_cached_model_calls_creator_once() -> None:
    cache: dict[str, object] = {}
    created: list[str] = []

    def creator() -> dict[str, int]:
        created.append("created")
        return {"value": 1}

    first = get_or_create_cached_model(cache, "agent", creator)
    second = get_or_create_cached_model(cache, "agent", creator)

    assert first is second
    assert created == ["created"]


def test_build_lazy_model_delegates_attribute_access_and_repr() -> None:
    lazy_model = build_lazy_model("router", lambda name, temperature: DummyModel())

    assert lazy_model.answer == "ok"
    assert bool(lazy_model) is True
    assert repr(lazy_model) == "_LazyModel(name=router, t=0.1)"
