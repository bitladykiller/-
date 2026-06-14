import importlib
import sys

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
