import asyncio

from fastapi import Response
from starlette.requests import Request

import app.main_http_support as main_http_support


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, tuple[object, ...]]] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> object:
        self.messages.append((msg, args))
        return None


def _run(awaitable):
    return asyncio.run(awaitable)


def test_register_middleware_logs_elapsed_ms() -> None:
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
    main_http_support.register_middleware(
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

    response = _run(
        app.handler(request, call_next)
    )

    assert response.status_code == 204
    assert logger.messages == [
        ("%s %s → %s (%.1fms)", ("GET", "/health", 204, 125.0))
    ]
