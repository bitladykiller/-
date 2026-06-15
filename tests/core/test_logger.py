import logging
import sys

import app.shared.core.logger as logger_module


def test_format_log_context_filters_empty_values() -> None:
    context = logger_module.format_log_context(
        user_id=1,
        empty_text="  ",
        tags=[],
        note=None,
        status="ok",
        zero_value=0,
    )

    assert context == "user_id=1 status=ok zero_value=0"


def test_setup_logging_adds_single_stream_handler_and_marks_noisy_loggers(monkeypatch) -> None:
    root_logger = logging.Logger("test.root")
    noisy_loggers = {
        name: logging.Logger(name)
        for name in (
            "sqlalchemy.engine",
            "pymilvus.client",
            "pymilvus.milvus_client",
            "httpx",
            "httpcore",
            "urllib3",
            "asyncio",
        )
    }

    def fake_get_logger(name=None):
        if name is None:
            return root_logger
        return noisy_loggers[name]

    monkeypatch.setattr(logger_module, "_logging_initialized", False)
    monkeypatch.setattr(logger_module.logging, "getLogger", fake_get_logger)

    logger_module.setup_logging()
    logger_module.setup_logging()

    stream_handlers = [
        handler
        for handler in root_logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
    ]
    assert len(stream_handlers) == 1
    assert stream_handlers[0].stream is sys.stdout
    assert root_logger.level == logging.INFO
    assert all(
        noisy_logger.level == logging.WARNING
        for noisy_logger in noisy_loggers.values()
    )
    monkeypatch.setattr(logger_module, "_logging_initialized", False)
