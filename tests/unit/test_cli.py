"""Tests for gamarr CLI."""

from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
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

    def test_resolve_version_matches_pyproject_toml(self) -> None:
        """_resolve_version should return the version from pyproject.toml, the source of truth."""
        import tomllib

        from gamarr.cli import _resolve_version
        from gamarr.utils import get_project_root

        pyproject_path = get_project_root() / "pyproject.toml"
        with pyproject_path.open("rb") as fh:
            data = tomllib.load(fh)
        expected = data["project"]["version"]

        version = _resolve_version()
        assert version == expected, (
            f"Version mismatch: _resolve_version() returned {version!r}, "
            f"but pyproject.toml has {expected!r}. "
            "The version must come from pyproject.toml, not installed package metadata."
        )

    def test_resolve_version_fallback_on_read_error(self) -> None:
        """_resolve_version should fall back through pyproject.toml -> importlib.metadata -> unknown."""
        with (
            patch("gamarr.cli.tomllib.load", side_effect=OSError("read error")),
            patch("gamarr.cli._pkg_version", side_effect=PackageNotFoundError),
        ):
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

    def test_clear_cache_flag_parses_single_source(self) -> None:
        """--clear-cache fitgirl calls clear_cache once."""
        import tempfile

        tmp_cfg = tempfile.mkdtemp()
        with patch("gamarr.database.Database") as mock_db:
            result = self.runner.invoke(cli, ["--config-path", tmp_cfg, "--clear-cache", "fitgirl"])
        assert result.exit_code == 0
        mock_db.return_value.clear_cache.assert_called_once_with("fitgirl")

    def test_clear_cache_flag_parses_all(self) -> None:
        """--clear-cache all calls clear_cache for all three sources."""
        import tempfile

        tmp_cfg = tempfile.mkdtemp()
        with patch("gamarr.database.Database") as mock_db:
            result = self.runner.invoke(cli, ["--config-path", tmp_cfg, "--clear-cache", "all"])
        assert result.exit_code == 0
        assert mock_db.return_value.clear_cache.call_count == 3
        mock_db.return_value.clear_cache.assert_any_call("fitgirl")
        mock_db.return_value.clear_cache.assert_any_call("freegog")
        mock_db.return_value.clear_cache.assert_any_call("metacritic")

    def test_clear_cache_unknown_source_logs_warning(self) -> None:
        """Unknown cache source is silently skipped."""
        import tempfile

        tmp_cfg = tempfile.mkdtemp()
        with patch("gamarr.database.Database") as mock_db, patch("gamarr.cli.logger") as mock_logger:
            result = self.runner.invoke(cli, ["--config-path", tmp_cfg, "--clear-cache", "bogus"])
        assert result.exit_code == 0
        mock_db.return_value.clear_cache.assert_not_called()
        mock_logger.warning.assert_any_call("Unknown cache source '{}' — skipping", "bogus")

    def test_log_path_accepts_directory(self) -> None:
        """--log-path should accept a directory and write gamarr.log inside it."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = tempfile.mkdtemp(dir=tmpdir)
            with patch("gamarr.scheduler.run"):
                result = self.runner.invoke(cli, ["--config-path", tmpdir, "--log-path", log_dir])
            assert result.exit_code == 0, f"CLI failed with: {result.output}"
            expected_log = os.path.join(log_dir, "gamarr.log")
            assert os.path.exists(expected_log), f"Log file not created at {expected_log}"

    def test_log_path_rejects_existing_file(self) -> None:
        """--log-path should reject an existing file (must be a directory)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file and pass it as --log-path
            existing_file = tempfile.mkstemp(dir=tmpdir, suffix=".log")[1]
            with patch("gamarr.scheduler.run"):
                result = self.runner.invoke(cli, ["--config-path", tmpdir, "--log-path", existing_file])
            assert result.exit_code != 0, f"CLI should reject file path, got exit code 0. Output: {result.output}"
            assert "is a file" in result.output.lower() or "not a directory" in result.output.lower()


class TestCliOverrides:
    """Tests for CLI override functions."""

    def test_apply_general_overrides_db_path(self) -> None:
        from gamarr.cli import _apply_cli_overrides
        from gamarr.config import Config

        config = Config()
        assert config.general.db_path == "db"
        _apply_cli_overrides(config, db_path="/custom/db")
        assert config.general.db_path == "/custom/db"

    def test_apply_general_overrides_pid_path(self) -> None:
        from gamarr.cli import _apply_cli_overrides
        from gamarr.config import Config

        config = Config()
        assert config.general.pid_path == "pids"
        _apply_cli_overrides(config, pid_path="/custom/pids")
        assert config.general.pid_path == "/custom/pids"

    def test_apply_general_overrides_library_paths(self) -> None:
        """--library-path should accept a | separated string like trimarr."""
        from gamarr.cli import _apply_cli_overrides
        from gamarr.config import Config

        config = Config()
        assert config.library.paths == []
        _apply_cli_overrides(config, library_path_list="/media/games|/media/more")
        assert config.library.paths == ["/media/games", "/media/more"]

    def test_apply_general_overrides_library_paths_single(self) -> None:
        """A single path without | should produce a single-element list."""
        from gamarr.cli import _apply_cli_overrides
        from gamarr.config import Config

        config = Config()
        _apply_cli_overrides(config, library_path_list="/media/games")
        assert config.library.paths == ["/media/games"]

    def test_apply_general_overrides_library_paths_empty(self) -> None:
        """An empty | separated string should not set any paths."""
        from gamarr.cli import _apply_cli_overrides
        from gamarr.config import Config

        config = Config()
        _apply_cli_overrides(config, library_path_list="")
        assert config.library.paths == []

    def test_apply_general_overrides_library_paths_whitespace(self) -> None:
        """Whitespace around paths should be stripped."""
        from gamarr.cli import _apply_cli_overrides
        from gamarr.config import Config

        config = Config()
        _apply_cli_overrides(config, library_path_list="/media/games | /media/more")
        assert config.library.paths == ["/media/games", "/media/more"]

    def test_apply_qbt_overrides(self) -> None:
        from gamarr.cli import _apply_cli_overrides
        from gamarr.config import Config

        config = Config()
        _apply_cli_overrides(
            config,
            qbt_host="192.168.1.10",
            qbt_port=9090,
            qbt_username="custom",
            qbt_password="secret",
        )
        assert config.torrent_client.qbittorrent.host == "192.168.1.10"
        assert config.torrent_client.qbittorrent.port == 9090
        assert config.torrent_client.qbittorrent.username == "custom"
        assert config.torrent_client.qbittorrent.password == "secret"

    def test_override_none_does_not_change_defaults(self) -> None:
        from gamarr.cli import _apply_cli_overrides
        from gamarr.config import Config

        config = Config()
        _apply_cli_overrides(config)  # no overrides
        assert config.general.db_path == "db"
        assert config.general.pid_path == "pids"


class TestInvalidConfigReporting:
    """A broken config must read as a message the operator can act on, not a traceback."""

    @staticmethod
    def _write_config(directory: Path, body: str) -> None:
        (directory / "gamarr.yml").write_text(body, encoding="utf-8")

    def test_an_invalid_config_exits_cleanly_instead_of_raising(self, tmp_path: Path) -> None:
        """The command must exit 1 with the reasons, not leak the validation error."""
        self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n"
            "        discovery:\n          mode: backlog\n          backlog:\n"
            "            sort_order: score\n",
        )

        result = CliRunner().invoke(cli, ["--config-path", str(tmp_path), "--test"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit), f"unhandled {type(result.exception).__name__}"
        assert "gamarr.yml" in result.output, result.output
        assert "sort_order" in result.output, result.output
        assert "criticscore" in result.output, result.output

    def test_every_config_problem_is_reported_at_once(self, tmp_path: Path) -> None:
        """An operator should see all the bad keys in one run, not one per restart."""
        self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    pending_order: bogus\n"
            "    platform_overrides:\n      pc:\n        discovery:\n          mode: bogus\n",
        )

        result = CliRunner().invoke(cli, ["--config-path", str(tmp_path), "--test"])

        assert result.exit_code == 1
        assert "pending_order" in result.output, result.output
        assert "discovery.mode" in result.output, result.output

    def test_debug_shows_the_traceback(self, tmp_path: Path) -> None:
        """--debug is the escape hatch for a bug report: it must raise the original error."""
        from pydantic import ValidationError

        self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n"
            "        discovery:\n          mode: backlog\n          backlog:\n"
            "            sort_order: score\n",
        )

        result = CliRunner().invoke(cli, ["--config-path", str(tmp_path), "--test", "--debug"])

        assert isinstance(result.exception, ValidationError)

    def test_a_yaml_syntax_error_exits_cleanly(self, tmp_path: Path) -> None:
        """A malformed YAML file is a config problem, not a reason to dump a traceback."""
        self._write_config(tmp_path, "review_sites:\n\tmetacritic: broken\n")

        result = CliRunner().invoke(cli, ["--config-path", str(tmp_path), "--test"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit), f"unhandled {type(result.exception).__name__}"
        assert "line 2" in result.output, result.output
        problem_lines = [
            line for line in result.output.splitlines() if line.strip() and not line.startswith("Configuration")
        ]
        assert problem_lines, result.output
        for line in problem_lines[:-1]:  # the last line is the remediation trailer
            assert line.startswith("  - "), f"every problem line is a list item: {line!r}"

    def test_a_non_mapping_config_root_exits_cleanly(self, tmp_path: Path) -> None:
        """A YAML root that is not a mapping is reported, not raised."""
        self._write_config(tmp_path, "- just\n- a list\n")

        result = CliRunner().invoke(cli, ["--config-path", str(tmp_path), "--test"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit), f"unhandled {type(result.exception).__name__}"
        assert "must be a YAML mapping" in result.output, result.output

    def test_an_unreadable_config_exits_cleanly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A filesystem problem is reported as one line, not a PermissionError traceback.

        The denial is injected rather than set with file mode: root ignores mode 000,
        so a mode-based test would silently pass or fail depending on the runner's uid.
        """
        self._write_config(tmp_path, "general:\n  log_path: logs\n")
        real_open = Path.open

        def _deny(self: Path, *args: Any, **kwargs: Any) -> Any:
            raise PermissionError(13, "Permission denied", str(self))

        monkeypatch.setattr(Path, "open", _deny)
        try:
            result = CliRunner().invoke(cli, ["--config-path", str(tmp_path), "--test"])
        finally:
            monkeypatch.setattr(Path, "open", real_open)

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit), f"unhandled {type(result.exception).__name__}"
        assert "could not be read or parsed" in result.output, result.output
        assert "Fix the keys above" not in result.output, "a filesystem problem is not a key problem"

    def test_mixed_type_keys_do_not_crash_the_inert_key_report(self, tmp_path: Path) -> None:
        """A numeric key in the unread block is a config error, not a TypeError."""
        self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n"
            "        discovery:\n          mode: backlog\n          backlog:\n"
            "            sort_order: criticscore\n            new:\n              2025: 1\n"
            "              oldest_month: 0\n",
        )

        result = CliRunner().invoke(cli, ["--config-path", str(tmp_path), "--test"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit), f"unhandled {type(result.exception).__name__}"
        assert "Keys should be strings" in result.output, result.output

    def test_load_config_leaves_reporting_to_the_cli(self, tmp_path: Path) -> None:
        """One reporter: load_config must not also log the first problem."""
        from loguru import logger

        from gamarr.config import load_config

        self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n"
            "        discovery:\n          mode: backlog\n          backlog:\n"
            "            sort_order: score\n",
        )
        from pydantic import ValidationError

        captured: list[str] = []
        sink_id = logger.add(lambda msg: captured.append(str(msg)), level="ERROR", format="{message}")
        try:
            with pytest.raises(ValidationError):
                load_config(tmp_path / "gamarr.yml")
        finally:
            logger.remove(sink_id)

        assert captured == [], f"the CLI is the only reporter: {captured}"
