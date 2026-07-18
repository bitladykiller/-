import importlib
import sys
import types
import asyncio

def _import_fresh(module_name: str):
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_bootstrap_compose_db_import_prepares_environment(monkeypatch) -> None:
    imported_modules: list[str] = []
    original_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None):
        if name in {
            "app.user.infrastructure.models.user",
            "app.chat.infrastructure.models.conversation",
        }:
            imported_modules.append(name)
            return types.ModuleType(name)
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    _import_fresh("app.scripts.bootstrap_compose_db")

    assert imported_modules == [
        "app.user.infrastructure.models.user",
        "app.chat.infrastructure.models.conversation",
    ]


def test_init_db_import_prepares_environment(monkeypatch) -> None:
    imported_modules: list[str] = []
    original_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None):
        if name in {
            "app.user.infrastructure.models.user",
            "app.chat.infrastructure.models.conversation",
        }:
            imported_modules.append(name)
            return types.ModuleType(name)
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    _import_fresh("app.scripts.init_db")

    assert imported_modules == [
        "app.user.infrastructure.models.user",
        "app.chat.infrastructure.models.conversation",
    ]


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
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name, package=None: types.ModuleType(name)
        if name.startswith(("app.user.infrastructure.models.", "app.chat.infrastructure.models."))
        else importlib.__import__(name, fromlist=["*"]),
    )

    module = _import_fresh("app.scripts.bootstrap_compose_db")
    asyncio.run(module.create_all_tables())

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
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name, package=None: types.ModuleType(name)
        if name.startswith(("app.user.infrastructure.models.", "app.chat.infrastructure.models."))
        else importlib.__import__(name, fromlist=["*"]),
    )

    module = _import_fresh("app.scripts.init_db")
    asyncio.run(module.reset_all_tables())

    assert calls == [fake_base.metadata.drop_all, fake_base.metadata.create_all]
