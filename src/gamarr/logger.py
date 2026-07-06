"""Logging utilities for gamarr."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from loguru import logger as _logger

if TYPE_CHECKING:
    import loguru


class InterceptHandler(logging.Handler):
    """Bridge between Python's standard logging and Loguru.

    APScheduler uses Python's ``logging.getLogger('apscheduler')``
    internally. Without this handler, those messages fall through
    to ``logging.lastResort`` which prints raw to stderr, bypassing
    Loguru's format entirely.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Forward a standard logging record to Loguru."""
        try:
            level: str | int = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        _logger.opt(depth=6, exception=record.exc_info).log(
            level,
            record.getMessage(),
        )


def create_logger(
    log_format: str,
    log_level: str = "INFO",
    log_path: str | None = None,
) -> loguru.Logger:
    """Return a configured Loguru logger instance.

    Args:
        log_format: Loguru format string for console output.
        log_level: Minimum log level for both sinks.
        log_path: Optional path to a log file. The parent directory is created
            automatically if it does not already exist.
    """
    _logger.remove()

    # Console sink
    _logger.add(
        sink=lambda message: print(message, end=""),
        level=log_level.upper(),
        format=log_format,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )

    # File sink
    if log_path:
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        _logger.add(
            sink=log_path,
            level=log_level.upper(),
            format=log_format,
            rotation="10 MB",
            retention=3,
            encoding="utf-8",
            backtrace=False,
            diagnose=False,
        )

    # Intercept APScheduler's Python logging so its internal messages
    # (e.g. "maximum number of running instances reached") go through
    # Loguru's format instead of falling through to logging.lastResort.
    aps_logger = logging.getLogger("apscheduler")
    aps_logger.handlers[:] = []
    aps_logger.addHandler(InterceptHandler())
    aps_logger.propagate = False

    return _logger
