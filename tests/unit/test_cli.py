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

    def test_default_invocation_succeeds(self) -> None:
        """Running with defaults should exit with code 0."""
        result = self.runner.invoke(cli, [])
        assert result.exit_code == 0

    def test_default_invocation_logs_message(self) -> None:
        """Running with defaults should log the WIP message."""
        result = self.runner.invoke(cli, [])
        assert "WIP: CLI logic not yet implemented." in result.output

    def test_custom_log_level(self) -> None:
        """--log-level should be accepted."""
        result = self.runner.invoke(cli, ["--log-level", "DEBUG"])
        assert result.exit_code == 0

    def test_custom_log_level_case_insensitive(self) -> None:
        """--log-level should accept lowercase values."""
        result = self.runner.invoke(cli, ["--log-level", "debug"])
        assert result.exit_code == 0

    def test_invalid_log_level_fails(self) -> None:
        """--log-level should reject invalid values."""
        result = self.runner.invoke(cli, ["--log-level", "INVALID"])
        assert result.exit_code != 0
        assert "invalid choice" in result.output.lower() or "INVALID" in result.output

    def test_resolve_version_returns_installed(self) -> None:
        """When the package IS installed, _resolve_version returns the real version."""
        from gamarr.cli import _resolve_version

        version = _resolve_version()
        assert version == "0.1.0"

    def test_resolve_version_fallback(self) -> None:
        """When PackageNotFoundError is raised, _resolve_version returns 'unknown'."""
        with patch("gamarr.cli._pkg_version") as mock_version:
            mock_version.side_effect = PackageNotFoundError
            from gamarr.cli import _resolve_version

            assert _resolve_version() == "unknown"
