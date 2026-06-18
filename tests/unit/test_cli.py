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

    def test_clear_cache_flag_parses_multiple_sources(self) -> None:
        """--clear-cache fitgirl,dodi calls clear_cache for each."""
        import tempfile

        tmp_cfg = tempfile.mkdtemp()
        with patch("gamarr.database.Database") as mock_db:
            result = self.runner.invoke(cli, ["--config-path", tmp_cfg, "--clear-cache", "fitgirl,dodi"])
        assert result.exit_code == 0
        assert mock_db.return_value.clear_cache.call_count == 2
        mock_db.return_value.clear_cache.assert_any_call("fitgirl")
        mock_db.return_value.clear_cache.assert_any_call("dodi")

    def test_clear_cache_flag_parses_all(self) -> None:
        """--clear-cache all calls clear_cache for all three sources."""
        import tempfile

        tmp_cfg = tempfile.mkdtemp()
        with patch("gamarr.database.Database") as mock_db:
            result = self.runner.invoke(cli, ["--config-path", tmp_cfg, "--clear-cache", "all"])
        assert result.exit_code == 0
        assert mock_db.return_value.clear_cache.call_count == 3
        mock_db.return_value.clear_cache.assert_any_call("fitgirl")
        mock_db.return_value.clear_cache.assert_any_call("dodi")
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
