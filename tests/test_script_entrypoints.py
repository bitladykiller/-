import importlib
import sys
import types

import app.scripts.db_script_support as db_script_support


def _import_fresh(module_name: str):
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_bootstrap_compose_db_import_prepares_environment(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        db_script_support,
        "prepare_db_script_environment",
        lambda: calls.append("prepare"),
    )
    monkeypatch.setattr(
        db_script_support,
        "run_async_entrypoint",
        lambda entrypoint: calls.append("run"),
    )

    _import_fresh("app.scripts.bootstrap_compose_db")

    assert calls == ["prepare"]


def test_init_db_import_prepares_environment(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        db_script_support,
        "prepare_db_script_environment",
        lambda: calls.append("prepare"),
    )
    monkeypatch.setattr(
        db_script_support,
        "run_async_entrypoint",
        lambda entrypoint: calls.append("run"),
    )

    _import_fresh("app.scripts.init_db")

    assert calls == ["prepare"]


def test_create_all_tables_runs_create_all(monkeypatch) -> None:
    calls: list[object] = []

    class FakeConn:
        async def run_sync(self, fn) -> None:
            calls.append(fn)

    class FakeBegin:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class FakeEngine:
        def begin(self):
            return FakeBegin()

    fake_base = types.SimpleNamespace(
        metadata=types.SimpleNamespace(
            create_all=object(),
            drop_all=object(),
        )
    )
    fake_module = types.ModuleType("app.shared.core.database")
    fake_module.Base = fake_base
    fake_module.engine = FakeEngine()
    monkeypatch.setitem(sys.modules, "app.shared.core.database", fake_module)

    db_script_support.run_async_entrypoint(db_script_support.create_all_tables)

    assert calls == [fake_base.metadata.create_all]


def test_reset_all_tables_runs_drop_then_create(monkeypatch) -> None:
    calls: list[object] = []

    class FakeConn:
        async def run_sync(self, fn) -> None:
            calls.append(fn)

    class FakeBegin:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class FakeEngine:
        def begin(self):
            return FakeBegin()

    fake_base = types.SimpleNamespace(
        metadata=types.SimpleNamespace(
            create_all=object(),
            drop_all=object(),
        )
    )
    fake_module = types.ModuleType("app.shared.core.database")
    fake_module.Base = fake_base
    fake_module.engine = FakeEngine()
    monkeypatch.setitem(sys.modules, "app.shared.core.database", fake_module)

    db_script_support.run_async_entrypoint(db_script_support.reset_all_tables)

    assert calls == [fake_base.metadata.drop_all, fake_base.metadata.create_all]
