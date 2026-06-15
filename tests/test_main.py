import asyncio
import importlib
import sys
import types
from contextlib import asynccontextmanager

from fastapi import APIRouter
from fastapi.testclient import TestClient


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, tuple[object, ...]]] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> object:
        self.messages.append((msg, args))
        return None


def _run(awaitable):
    return asyncio.run(awaitable)


def _import_fresh(module_name: str):
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_import_app_main_exposes_factory_only() -> None:
    main_module = _import_fresh("app.main")
    app = main_module.create_app()

    route_names = {route.name for route in app.routes}
    route_paths = {getattr(route, "path", None) for route in app.routes}

    assert not hasattr(main_module, "app")
    assert app.title == "AssistGen REST API"
    assert "/health" in route_paths
    assert "static" not in route_names


def test_create_app_logs_and_skips_missing_static_dir(tmp_path) -> None:
    main_module = _import_fresh("app.main")
    logger = FakeLogger()
    missing_static_dir = tmp_path / "dist"
    main_module.logger = logger
    main_module.api_router = APIRouter()
    main_module.STATIC_DIR = missing_static_dir

    app = main_module.create_app()

    route_names = {route.name for route in app.routes}
    route_paths = {getattr(route, "path", None) for route in app.routes}

    assert "/health" in route_paths
    assert "static" not in route_names
    assert logger.messages[-1] == (
        "静态资源目录不存在，跳过挂载: %s",
        (missing_static_dir,),
    )


def test_create_app_registers_request_logging_middleware(monkeypatch, tmp_path) -> None:
    main_module = importlib.import_module("app.main")
    logger = FakeLogger()

    @asynccontextmanager
    async def fake_lifespan(_app):
        yield

    monkeypatch.setattr(
        main_module,
        "lifespan",
        fake_lifespan,
    )
    monkeypatch.setattr(main_module, "logger", logger)
    monkeypatch.setattr(main_module, "api_router", APIRouter())
    monkeypatch.setattr(main_module, "STATIC_DIR", tmp_path)

    app = main_module.create_app()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert len(logger.messages) == 1
    message, args = logger.messages[0]
    assert message == "%s %s → %s (%.1fms)"
    assert args[:3] == ("GET", "/health", 200)
    assert isinstance(args[3], float)
    assert args[3] >= 0


def test_lifespan_default_runtime_hooks_use_memory_and_task_runtime(monkeypatch) -> None:
    main_module = importlib.import_module("app.main")
    logger = FakeLogger()
    monkeypatch.setattr(main_module, "logger", logger)
    called: list[str] = []
    fake_module = types.ModuleType("app.chat.infrastructure.memory_bridge.runtime")
    fake_task_queue = types.ModuleType("app.chat.application.task_queue")

    async def fake_get_memory_middleware() -> None:
        called.append("get_memory")

    async def fake_close_memory_middleware() -> None:
        called.append("close_memory")

    async def fake_close_task_manager() -> None:
        called.append("close_task")

    fake_module.get_memory_middleware = fake_get_memory_middleware
    fake_module.close_memory_middleware = fake_close_memory_middleware
    fake_task_queue.close_task_manager = fake_close_task_manager
    monkeypatch.setitem(
        sys.modules,
        "app.chat.infrastructure.memory_bridge.runtime",
        fake_module,
    )
    monkeypatch.setitem(sys.modules, "app.chat.application.task_queue", fake_task_queue)

    async def scenario() -> None:
        async with main_module.lifespan(object()):
            assert called == ["get_memory"]

    _run(scenario())

    assert called == ["get_memory", "close_memory", "close_task"]
    assert logger.messages == [
        ("预热 MemoryMiddleware...", ()),
        ("启动完成", ()),
        ("关闭连接...", ()),
        ("关闭完成", ()),
    ]
