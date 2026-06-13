import logging

from app.core import database_support


class FakeLogger:
    def __init__(self) -> None:
        self.level: int | None = None

    def setLevel(self, level: int) -> None:
        self.level = level


def test_build_engine_options_returns_project_defaults() -> None:
    options = database_support.build_engine_options("mysql+aiomysql://user:pwd@db/app")

    assert options == {
        "url": "mysql+aiomysql://user:pwd@db/app",
        "echo": False,
        "pool_pre_ping": True,
        "pool_size": database_support.POOL_SIZE,
        "max_overflow": database_support.MAX_OVERFLOW,
    }


def test_configure_sqlalchemy_logging_sets_expected_level(monkeypatch) -> None:
    fake_logger = FakeLogger()
    original_get_logger = database_support.logging.getLogger

    def fake_get_logger(name: str | None = None):
        if name == database_support.SQLALCHEMY_LOGGER_NAME:
            return fake_logger
        return original_get_logger(name)

    monkeypatch.setattr(
        database_support.logging,
        "getLogger",
        fake_get_logger,
    )

    database_support.configure_sqlalchemy_logging()

    assert fake_logger.level == logging.WARNING


def test_create_session_factory_respects_expire_on_commit_flag() -> None:
    engine = object()

    session_factory = database_support.create_session_factory(
        engine, expire_on_commit=True
    )

    assert session_factory.kw["bind"] is engine
    assert session_factory.kw["expire_on_commit"] is True
