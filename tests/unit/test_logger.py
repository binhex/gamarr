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
