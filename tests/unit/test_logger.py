"""Tests for gamarr.logger."""

import os
import tempfile
import time

from loguru import logger as _loguru_logger

from gamarr.logger import create_logger


class TestCreateLogger:
    """Tests for create_logger()."""

    def test_returns_logger_instance(self) -> None:
        """Should return a loguru Logger instance."""
        logger = create_logger(log_format="{message}", log_level="DEBUG")
        assert logger is _loguru_logger

    def test_console_sink_added(self) -> None:
        """Should add at least one sink (console) — verify by writing a message."""
        logger = create_logger(log_format="{message}", log_level="DEBUG")
        # The logger should accept and handle messages without error
        logger.debug("test message")
        # If we got here without exception, sinks are configured

    def test_file_sink_creates_log_file(self) -> None:
        """When log_path is given, the log file should be created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "test.log")
            create_logger(log_format="{message}", log_level="INFO", log_path=log_path)
            assert os.path.exists(log_path)

    def test_file_sink_creates_parent_directory(self) -> None:
        """Parent directory should be created automatically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "nested", "subdir", "test.log")
            create_logger(log_format="{message}", log_level="INFO", log_path=log_path)
            assert os.path.exists(log_path)

    def test_file_sink_writes_messages(self) -> None:
        """Messages logged should appear in the log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "test.log")
            logger = create_logger(log_format="{message}", log_level="INFO", log_path=log_path)
            logger.info("Hello gamarr")
            # Loguru flushes asynchronously — check after a small delay
            content = ""
            for _ in range(10):
                _loguru_logger.remove()
                try:
                    with open(log_path) as f:
                        content = f.read()
                    if "Hello gamarr" in content:
                        break
                except FileNotFoundError:
                    pass
                time.sleep(0.05)
            assert "Hello gamarr" in content


class TestLoggingIntercept:
    """APScheduler Python logging must be intercepted by Loguru."""

    def test_intercept_handler_routes_to_loguru(self) -> None:
        """InterceptHandler must forward Python logging messages to Loguru.

        APScheduler uses Python's standard logging module internally.
        Without InterceptHandler, messages go to logging.lastResort
        which prints raw to stderr, bypassing Loguru entirely.
        """
        import io
        import logging

        from loguru import logger as _logger

        stream = io.StringIO()
        handler_id = _logger.add(stream, format="{message}", level="INFO")

        try:
            from gamarr.logger import InterceptHandler

            # Set up a test logger with InterceptHandler
            test_logger = logging.getLogger("apscheduler_test_route")
            test_logger.handlers = []
            test_logger.addHandler(InterceptHandler())
            test_logger.propagate = False

            test_logger.warning("intercepted message")

            output = stream.getvalue()
            assert "intercepted message" in output, f"Message not captured by Loguru via InterceptHandler:\n{output}"
        finally:
            _logger.remove(handler_id)

    def test_create_logger_configures_apscheduler_intercept(self) -> None:
        """create_logger must configure the apscheduler logger with InterceptHandler.

        After create_logger(), the apscheduler Python logger should have
        an InterceptHandler installed and propagation disabled, so messages
        are captured by Loguru instead of falling through to lastResort.
        """
        import logging

        from gamarr.logger import create_logger

        create_logger(log_format="{message}", log_level="DEBUG")

        aps_logger = logging.getLogger("apscheduler")
        assert len(aps_logger.handlers) >= 1, "No handlers on apscheduler logger — raw messages will leak to stderr"
        assert aps_logger.propagate is False, "apscheduler logger should not propagate"
