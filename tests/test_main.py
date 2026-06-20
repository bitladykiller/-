import asyncio
import importlib
import sys
import types

from fastapi import APIRouter, Response
from starlette.requests import Request


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


def test_import_app_main_skips_missing_static_dir() -> None:
    main_module = _import_fresh("app.main")

    route_names = {
        route.name for route in main_module.app.routes if getattr(route, "name", None)
    }
    route_paths = {getattr(route, "path", None) for route in main_module.app.routes}

    assert main_module.app.title == main_module.APP_TITLE
    assert "/health" in route_paths
    assert "static" not in route_names


def test_create_app_logs_and_skips_missing_static_dir(tmp_path) -> None:
    main_module = _import_fresh("app.main")
    logger = FakeLogger()
    missing_static_dir = tmp_path / "dist"

    app = main_module.create_app(
        runtime_logger=logger,
        app_api_router=APIRouter(),
        static_dir=missing_static_dir,
        health_status="healthy",
    )

    route_names = {route.name for route in app.routes if getattr(route, "name", None)}
    route_paths = {getattr(route, "path", None) for route in app.routes}

    assert "/health" in route_paths
    assert "static" not in route_names
    assert logger.messages[-1] == (
        "静态资源目录不存在，跳过挂载: %s",
        (missing_static_dir,),
    )


def test_register_middleware_logs_elapsed_ms() -> None:
    main_module = importlib.import_module("app.main")

    class FakeApp:
        def __init__(self) -> None:
            self.handler = None

        def middleware(self, kind: str):
            assert kind == "http"

            def decorator(func):
                self.handler = func
                return func

            return decorator

    logger = FakeLogger()
    clock_values = iter([10.0, 10.125])
    app = FakeApp()
    main_module.register_middleware(
        app,
        logger,
        clock=lambda: next(clock_values),
    )
    assert app.handler is not None

    async def call_next(request: Request) -> Response:
        return Response(status_code=204)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }
    )

    response = _run(app.handler(request, call_next))

    assert response.status_code == 204
    assert logger.messages == [
        ("%s %s → %s (%.1fms)", ("GET", "/health", 204, 125.0))
    ]


def test_warm_up_runtime_resources_delegates_to_memory_warmup(monkeypatch) -> None:
    main_module = importlib.import_module("app.main")
    logger = FakeLogger()
    called: list[str] = []
    fake_module = types.ModuleType("app.chat.infrastructure.memory_bridge.runtime")

    async def fake_warm_up_memory_middleware() -> None:
        called.append("warm_up_memory")

    fake_module.warm_up_memory_middleware = fake_warm_up_memory_middleware
    monkeypatch.setitem(
        sys.modules,
        "app.chat.infrastructure.memory_bridge.runtime",
        fake_module,
    )

    _run(main_module.warm_up_runtime_resources(logger))

    assert called == ["warm_up_memory"]
    assert logger.messages == [("预热 MemoryMiddleware...", ())]


def test_close_runtime_resources_delegates_to_runtime_closers(monkeypatch) -> None:
    main_module = importlib.import_module("app.main")
    called: list[str] = []
    fake_lg_context = types.ModuleType("app.chat.infrastructure.memory_bridge.runtime")
    fake_task_queue = types.ModuleType("app.chat.application.task_queue")

    async def fake_close_memory_middleware() -> None:
        called.append("close_memory")

    async def fake_close_task_manager() -> None:
        called.append("close_task")

    fake_lg_context.close_memory_middleware = fake_close_memory_middleware
    fake_task_queue.close_task_manager = fake_close_task_manager
    monkeypatch.setitem(
        sys.modules,
        "app.chat.infrastructure.memory_bridge.runtime",
        fake_lg_context,
    )
    monkeypatch.setitem(sys.modules, "app.chat.application.task_queue", fake_task_queue)

    _run(main_module.close_runtime_resources())

    assert called == ["close_memory", "close_task"]


def test_build_lifespan_delegates_runtime_hooks() -> None:
    main_module = importlib.import_module("app.main")
    logger = FakeLogger()
    called: list[str] = []

    async def fake_warm_up(logger_obj) -> None:
        assert logger_obj is logger
        called.append("warm_up")

    async def fake_close_runtime() -> None:
        called.append("close_runtime")

    async def scenario() -> None:
        lifespan = main_module.build_lifespan(
            logger,
            warm_up=fake_warm_up,
            close_runtime=fake_close_runtime,
        )
        async with lifespan(object()):
            assert called == ["warm_up"]

    _run(scenario())

    assert called == ["warm_up", "close_runtime"]
    assert logger.messages == [
        ("启动完成", ()),
        ("关闭连接...", ()),
        ("关闭完成", ()),
    ]
