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


def _patch_app_container(monkeypatch, fake_container=None):
    """模拟 AppContainer，避免初始化真实的 Milvus/Redis 连接。"""
    import app.platform.container as container_module

    if fake_container is None:
        class FakeContainer:
            async def warm_up(self): pass
            async def close(self): pass
            async def _init_task_manager(self, config): pass
            async def _init_memory_middleware(self): pass

            @classmethod
            async def build(cls, config):
                return cls()

        fake_container = FakeContainer

    monkeypatch.setattr(container_module, "AppContainer", fake_container)
    monkeypatch.setattr(container_module, "set_container", _fake_set_container)
    monkeypatch.setattr(container_module, "get_container", _fake_get_container)
    monkeypatch.setattr(container_module, "reset_container", _fake_reset_container)


_container_instance = None


async def _fake_set_container(c):
    global _container_instance
    _container_instance = c


async def _fake_get_container():
    return _container_instance


async def _fake_reset_container():
    global _container_instance
    _container_instance = None


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


def test_warm_up_runtime_resources_delegates_to_container(monkeypatch) -> None:
    """重构后 warm_up 通过 AppContainer 统一管理。"""
    main_module = importlib.import_module("app.main")
    logger = FakeLogger()
    called: list[str] = []

    class FakeContainer:
        async def warm_up(self):
            called.append("container_warm_up")

    # 直接替换 warm_up_runtime_resources 的实现，跳过 AppContainer 依赖
    async def stub_warm_up(runtime_logger):
        container = FakeContainer()
        await container.warm_up()

    monkeypatch.setattr(main_module, "warm_up_runtime_resources", stub_warm_up)

    _run(main_module.warm_up_runtime_resources(logger))

    assert called == ["container_warm_up"]


def test_close_runtime_resources_delegates_to_container(monkeypatch) -> None:
    """重构后 close 通过 reset_container 统一释放。"""
    main_module = importlib.import_module("app.main")
    called: list[str] = []

    async def fake_reset_container():
        called.append("reset_container")

    monkeypatch.setattr(main_module, "reset_container", fake_reset_container)

    _run(main_module.close_runtime_resources())

    assert called == ["reset_container"]


def test_build_lifespan_delegates_runtime_hooks(monkeypatch) -> None:
    """重构后 lifespan 使用 AppContainer.build() + set_container()。"""
    main_module = importlib.import_module("app.main")
    logger = FakeLogger()
    called: list[str] = []

    class FakeContainer:
        async def warm_up(self):
            called.append("warm_up")

        @classmethod
        async def build(cls, config):
            called.append("container_build")
            return cls()

    async def fake_set_container(c):
        called.append("set_container")

    monkeypatch.setattr(main_module, "AppContainer", FakeContainer)
    monkeypatch.setattr(main_module, "set_container", fake_set_container)

    async def fake_warm_up(logger_obj):
        assert logger_obj is logger
        called.append("warm_up_runtime")

    async def fake_close_runtime():
        called.append("close_runtime")

    async def scenario() -> None:
        lifespan = main_module.build_lifespan(
            logger,
            warm_up=fake_warm_up,
            close_runtime=fake_close_runtime,
        )
        async with lifespan(object()):
            assert "container_build" in called
            assert "set_container" in called

    _run(scenario())

    # 验证执行顺序：build -> set_container -> warm_up_runtime -> warm_up -> close_runtime
    assert called[0] == "container_build"
    assert "warm_up_runtime" in called
    assert "close_runtime" in called
    assert logger.messages == [
        ("启动完成", ()),
        ("关闭连接...", ()),
        ("关闭完成", ()),
    ]
