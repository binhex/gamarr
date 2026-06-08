"""Tests for gamarr CLI."""

from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

from click.testing import CliRunner

from gamarr.cli import cli


class TestCli:
    """Tests for the main CLI command."""

    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_help_succeeds(self) -> None:
        """--help should exit with code 0 and show usage."""
        result = self.runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output

    def test_version_shows_version(self) -> None:
        """--version should show the program name and version."""
        result = self.runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "gamarr" in result.output

    def test_default_invocation_does_not_print_wip(self) -> None:
        """Running with defaults should not print WIP message."""
        with patch("gamarr.scheduler.run"):
            result = self.runner.invoke(cli, [])
        assert "WIP: CLI logic not yet implemented." not in result.output

    def test_custom_log_level_accepted(self) -> None:
        """--log-level should be accepted."""
        with patch("gamarr.scheduler.run"):
            result = self.runner.invoke(cli, ["--log-level", "DEBUG"])
        assert result.exit_code == 0

    def test_custom_log_level_case_insensitive(self) -> None:
        """--log-level should accept lowercase values."""
        with patch("gamarr.scheduler.run"):
            result = self.runner.invoke(cli, ["--log-level", "debug"])
        assert result.exit_code == 0

    def test_invalid_log_level_fails(self) -> None:
        """--log-level should reject invalid values."""
        result = self.runner.invoke(cli, ["--log-level", "INVALID"])
        assert result.exit_code != 0
        assert "not one of" in result.output.lower() or "invalid value" in result.output.lower()

    def test_resolve_version_returns_installed(self) -> None:
        from gamarr.cli import _resolve_version

        version = _resolve_version()
        assert version == "0.1.0"

    def test_resolve_version_fallback(self) -> None:
        with patch("gamarr.cli._pkg_version") as mock_version:
            mock_version.side_effect = PackageNotFoundError
            from gamarr.cli import _resolve_version

            assert _resolve_version() == "unknown"

    def test_test_mode_validates_and_exits(self) -> None:
        """--test should validate config and exit without calling run()."""
        result = self.runner.invoke(cli, ["--test"])
        assert result.exit_code == 0
        assert "Configuration loaded successfully" in result.output

    def test_daemon_flag_removed(self) -> None:
        """--daemon should no longer be a valid CLI flag."""
        result = self.runner.invoke(cli, ["--daemon"])
        assert result.exit_code != 0
        assert "no such option" in result.output.lower()

    def test_config_path_option_accepted(self) -> None:
        """--config-path should be accepted."""
        with patch("gamarr.scheduler.run"):
            result = self.runner.invoke(cli, ["--config-path", "/tmp/configs"])
        assert result.exit_code == 0
