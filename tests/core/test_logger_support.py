import logging

from app.core import logger_support


def test_format_log_context_filters_empty_values() -> None:
    context = logger_support.format_log_context(
        user_id=1,
        empty_text="  ",
        tags=[],
        note=None,
        status="ok",
    )

    assert context == "user_id=1 status=ok"


def test_configure_root_logger_adds_single_stream_handler() -> None:
    logger = logging.Logger("test.root")
    logger_support.configure_root_logger(
        logger,
        level=logging.INFO,
        format_str="%(message)s",
        date_format="%Y-%m-%d",
    )
    logger_support.configure_root_logger(
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


def test_has_log_value_treats_blank_and_empty_containers_as_false() -> None:
    assert logger_support.has_log_value("ok") is True
    assert logger_support.has_log_value(0) is True
    assert logger_support.has_log_value("   ") is False
    assert logger_support.has_log_value([]) is False
    assert logger_support.has_log_value({}) is False
    assert logger_support.has_log_value(None) is False
