import logging

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


def test_configure_root_logger_adds_single_stream_handler() -> None:
    logger = logging.Logger("test.root")
    logger_module.configure_root_logger(
        logger,
        level=logging.INFO,
        format_str="%(message)s",
        date_format="%Y-%m-%d",
    )
    logger_module.configure_root_logger(
        logger,
        level=logging.INFO,
        format_str="%(message)s",
        date_format="%Y-%m-%d",
    )

    stream_handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
    ]
    assert len(stream_handlers) == 1


def test_setup_logging_is_idempotent(monkeypatch) -> None:
    logger = logging.Logger("test.idempotent")
    monkeypatch.setattr(logger_module, "_logging_initialized", False)
    monkeypatch.setattr(logger_module.logging, "getLogger", lambda name=None: logger)

    logger_module.setup_logging()
    handler_count = len(logger.handlers)
    logger_module.setup_logging()

    assert len(logger.handlers) == handler_count
    monkeypatch.setattr(logger_module, "_logging_initialized", False)
