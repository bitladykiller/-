import importlib
import logging
import types

import app.shared.core.config as config_module
import app.shared.core.database as database_module


class FakeLogger:
    def __init__(self) -> None:
        self.level: int | None = None

    def setLevel(self, level: int) -> None:  # noqa: N802
        self.level = level


def test_database_module_initializes_engine_and_session_factory(monkeypatch) -> None:
    fake_logger = FakeLogger()
    engine_calls: list[dict] = []
    session_factory_calls: list[dict] = []

    with monkeypatch.context() as m:
        m.setattr(
            config_module,
            "settings",
            types.SimpleNamespace(DATABASE_URL="mysql+aiomysql://user:pwd@db/app"),
        )
        m.setattr(
            "sqlalchemy.ext.asyncio.create_async_engine",
            lambda url, **kwargs: engine_calls.append({"url": url, **kwargs}) or "engine",
        )
        m.setattr(
            "sqlalchemy.ext.asyncio.async_sessionmaker",
            lambda **kwargs: session_factory_calls.append(kwargs)
            or types.SimpleNamespace(kw=kwargs),
        )
        m.setattr(logging, "getLogger", lambda name=None: fake_logger)

        reloaded = importlib.reload(database_module)

        assert fake_logger.level == reloaded.logging.WARNING
        assert engine_calls == [
            {
                "url": "mysql+aiomysql://user:pwd@db/app",
                "echo": False,
                "pool_pre_ping": True,
                "pool_size": 5,
                "max_overflow": 10,
            }
        ]
        assert session_factory_calls == [
            {
                "bind": "engine",
                "expire_on_commit": False,
            }
        ]
        assert reloaded.AsyncSessionLocal.kw == {
            "bind": "engine",
            "expire_on_commit": False,
        }

    importlib.reload(database_module)
