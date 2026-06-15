import asyncio
import importlib
import re
import sys
import types
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _import_fresh(module_name: str):
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_bootstrap_compose_db_import_prepares_environment(monkeypatch) -> None:
    imported_modules: list[str] = []
    original_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None):
        if name == "app.user.infrastructure.models.conversation":
            imported_modules.append(name)
            return types.ModuleType(name)
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    _import_fresh("app.scripts.bootstrap_compose_db")

    assert imported_modules == ["app.user.infrastructure.models.conversation"]


def test_create_all_tables_runs_create_all(monkeypatch) -> None:
    calls: list[object] = []

    class FakeConn:
        async def run_sync(self, fn) -> None:
            calls.append(fn)

    class FakeBegin:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, _exc_type, exc, _tb) -> None:
            return None

    class FakeEngine:
        def begin(self):
            return FakeBegin()

    fake_base = types.SimpleNamespace(metadata=types.SimpleNamespace(create_all=object()))
    fake_module = types.ModuleType("app.shared.core.database")
    fake_module.Base = fake_base
    fake_module.engine = FakeEngine()
    monkeypatch.setitem(sys.modules, "app.shared.core.database", fake_module)
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name, package=None: (
            types.ModuleType(name)
            if name.startswith("app.user.infrastructure.models.")
            else importlib.__import__(name, fromlist=["*"])
        ),
    )

    module = _import_fresh("app.scripts.bootstrap_compose_db")
    asyncio.run(module.create_all_tables())

    assert calls == [fake_base.metadata.create_all]


def test_compose_startup_only_uses_internal_scripts() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    command = services["app"]["command"]

    assert command[:2] == ["/bin/sh", "-lc"]
    assert "bootstrap_compose_db import create_all_tables" in command[2]
    assert "exec python -c" in command[2]
    assert "uvicorn.run(create_app(), host='0.0.0.0', port=8000)" in command[2]
    assert (
        "./scripts/neo4j-import.sh:/scripts/neo4j-import.sh:ro"
        in services["neo4j-importer"]["volumes"]
    )
    assert "./configs/docker/neo4j-import:/import-data:ro" in services["neo4j-importer"]["volumes"]
    assert not (ROOT / "neo4j-import.sh").exists()
    assert not (ROOT / "run_server.py").exists()
    assert not (ROOT / "configs/docker/app").exists()
    assert not (ROOT / "configs/docker/app/start.sh").exists()
    assert not (ROOT / "configs/docker/app/run_server.py").exists()
    assert not (ROOT / "rag_doc_parser/cli.py").exists()
    assert not (ROOT / "rag_doc_parser/retrieval/cli.py").exists()
    assert (
        'CMD ["sh", "-lc", "echo \'This image must be started via docker compose up -d --build.\' >&2; exit 1"]'
        in dockerfile
    )
    assert "ENTRYPOINT " not in dockerfile
    assert "configs/docker/app/start.sh" not in dockerfile


def test_project_docs_do_not_reintroduce_local_start_commands() -> None:
    docs = {
        "README.md": ROOT / "README.md",
        "docs/DEPLOYMENT.md": ROOT / "docs/DEPLOYMENT.md",
        "docs/ARCHITECTURE.md": ROOT / "docs/ARCHITECTURE.md",
        "docs/CONTRIBUTING.md": ROOT / "docs/CONTRIBUTING.md",
        "docs/MIGRATION.md": ROOT / "docs/MIGRATION.md",
        "app/README.md": ROOT / "app/README.md",
        "app/scripts/README.md": ROOT / "app/scripts/README.md",
        "rag_doc_parser/README.md": ROOT / "rag_doc_parser/README.md",
    }
    forbidden_commands = (
        "uvicorn app.main:app",
        "python -m uvicorn",
        "python app/main.py",
        "python run_server.py",
        "fastapi run",
        "docker run",
        "configs/docker/app/start.sh",
        "configs/docker/app/run_server.py",
        "rag_doc_parser/cli.py",
        "rag_doc_parser/retrieval/cli.py",
        "python -m rag_doc_parser",
        "python -m rag_doc_parser.retrieval",
    )

    for label, path in docs.items():
        content = path.read_text(encoding="utf-8")
        for command in forbidden_commands:
            assert command not in content, f"{label} unexpectedly mentions {command!r}"


def test_app_module_does_not_expose_local_server_entrypoints() -> None:
    main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert re.search(r"^app\\s*=", main_source, flags=re.MULTILINE) is None
    assert "__main__" not in main_source


def test_project_metadata_does_not_register_extra_start_entrypoints() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "[project.scripts]" not in pyproject
    assert "console_scripts" not in pyproject

    for target in ("run", "start", "serve", "up"):
        assert re.search(rf"^{target}:", makefile, flags=re.MULTILINE) is None
