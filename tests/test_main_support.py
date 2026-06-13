import asyncio
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, Response
from starlette.requests import Request

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "llm_backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_MODULE_PATH = Path(__file__).resolve().parents[1] / "llm_backend" / "main_support.py"
_SPEC = importlib.util.spec_from_file_location("main_support", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
main_support = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(main_support)


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, tuple[object, ...]]] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> object:
        self.messages.append((msg, args))
        return None


def _run(awaitable):
    return asyncio.run(awaitable)


def test_ensure_repo_root_on_path_is_idempotent(monkeypatch) -> None:
    fake_path = ["/tmp/existing"]
    monkeypatch.setattr(main_support.sys, "path", fake_path)

    repo_root = Path("/tmp/repo-root")
    main_support.ensure_repo_root_on_path(repo_root)
    main_support.ensure_repo_root_on_path(repo_root)

    assert fake_path == ["/tmp/repo-root", "/tmp/existing"]


def test_build_request_logger_logs_elapsed_ms() -> None:
    logger = FakeLogger()
    clock_values = iter([10.0, 10.125])

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

    response = _run(
        main_support.build_request_logger(
            logger,
            clock=lambda: next(clock_values),
        )(request, call_next)
    )

    assert response.status_code == 204
    assert logger.messages == [
        ("%s %s → %s (%.1fms)", ("GET", "/health", 204, 125.0))
    ]


def test_create_app_registers_routes_events_and_static_mount(tmp_path: Path) -> None:
    logger = FakeLogger()
    router = APIRouter()

    @router.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "pong"}

    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    called: list[str] = []

    async def fake_warm_up(logger_obj) -> None:
        called.append("warm_up")

    async def fake_close_runtime() -> None:
        called.append("close_runtime")

    app = main_support.create_app(
        api_router=router,
        logger=logger,
        app_title="Test App",
        static_dir=static_dir,
        health_status="ok",
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        warm_up=fake_warm_up,
        close_runtime=fake_close_runtime,
    )

    assert app.title == "Test App"
    assert any(getattr(route, "path", None) == "/api/ping" for route in app.routes)
    assert any(getattr(route, "path", None) == "/health" for route in app.routes)
    assert any(getattr(route, "name", None) == "static" for route in app.routes)

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            assert called == ["warm_up"]

    _run(scenario())

    assert called == ["warm_up", "close_runtime"]
