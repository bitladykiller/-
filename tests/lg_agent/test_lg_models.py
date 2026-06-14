import app.chat.infrastructure.modeling.models as lg_models


class DummyModel:
    def __init__(self) -> None:
        self.answer = "ok"


def test_create_chat_model_uses_resolved_factory(monkeypatch) -> None:
    monkeypatch.setattr(lg_models, "_resolve_model_factory", lambda: lambda temperature: ("factory", temperature))

    assert lg_models._create_chat_model(0.3) == ("factory", 0.3)


def test_get_model_caches_instances_by_role(monkeypatch) -> None:
    lg_models._models_cache.clear()
    created: list[tuple[str, float]] = []

    def fake_create_chat_model(temperature: float):
        created.append(("created", temperature))
        return {"temperature": temperature}

    monkeypatch.setattr(lg_models, "_create_chat_model", fake_create_chat_model)

    first = lg_models._get_model("agent", 0.7)
    second = lg_models._get_model("agent", 0.7)

    assert first is second
    assert created == [("created", 0.7)]
    lg_models._models_cache.clear()


def test_lazy_model_delegates_attribute_access_and_repr(monkeypatch) -> None:
    monkeypatch.setattr(lg_models, "_get_model", lambda name, temperature: DummyModel())
    lazy_model = lg_models._lazy_model("router")

    assert lazy_model.answer == "ok"
    assert bool(lazy_model) is True
    assert repr(lazy_model) == "_LazyModel(name=router, t=0.1)"
