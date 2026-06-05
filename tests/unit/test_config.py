"""Tests for gamarr config module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from gamarr.config import (
    Config,
    FitGirlSourceConfig,
    GeneralConfig,
    LibraryConfig,
    MetacriticPlatformConfig,
    NotificationConfig,
    QbittorrentConfig,
    ScheduleTaskConfig,
    TorrentClientConfig,
    create_default_config,
    load_config,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestConfigModels:
    """Pydantic config model construction."""

    def test_general_config_defaults(self) -> None:
        cfg = GeneralConfig()
        assert cfg.daemon_mode == "foreground"
        assert cfg.log_level_console == "INFO"

    def test_schedule_task_config_defaults(self) -> None:
        cfg = ScheduleTaskConfig()
        assert cfg.enabled is True
        assert cfg.schedule_time_mins == 60
        assert cfg.run_on_start is True

    def test_fitgirl_source_config_defaults(self) -> None:
        cfg = FitGirlSourceConfig()
        assert cfg.enabled is True
        assert cfg.rss_url == "https://fitgirl-repacks.site/feed/"
        assert cfg.platform == "pc"

    def test_metacritic_platform_config_defaults(self) -> None:
        cfg = MetacriticPlatformConfig()
        assert cfg.min_metascore == 75
        assert cfg.min_user_score == 7.5
        assert cfg.days_since_release == 90

    def test_qbittorrent_config_defaults(self) -> None:
        cfg = QbittorrentConfig()
        assert cfg.host == "localhost"
        assert cfg.port == 8080
        assert cfg.category == "games-gamarr"

    def test_torrent_client_config_defaults(self) -> None:
        cfg = TorrentClientConfig()
        assert cfg.selected == "qbittorrent"

    def test_notification_config_defaults(self) -> None:
        cfg = NotificationConfig()
        assert cfg.apprise_urls == []
        assert cfg.on_download is True

    def test_library_config_defaults(self) -> None:
        cfg = LibraryConfig()
        assert cfg.paths == []

    def test_library_in_root_config(self) -> None:
        cfg = Config()
        assert cfg.library.paths == []

    def test_root_config_defaults(self) -> None:
        cfg = Config()
        assert cfg.general.daemon_mode == "foreground"
        assert cfg.sources.fitgirl.enabled is True
        assert cfg.metacritic.platform_overrides["pc"].min_metascore == 75
        assert cfg.torrent_client.selected == "qbittorrent"


class TestLoadConfig:
    """Config file loading."""

    def test_create_default_config_creates_file(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "configs"
        create_default_config(str(config_dir))
        config_file = config_dir / "gamarr.yml"
        assert config_file.exists()
        with config_file.open() as fh:
            raw = yaml.safe_load(fh)
        assert raw is not None
        assert "general" in raw

    def test_create_default_config_does_not_overwrite(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "configs"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "gamarr.yml"
        config_file.write_text("general:\n  daemon_mode: background\n")
        create_default_config(str(config_dir))
        with config_file.open() as fh:
            raw = yaml.safe_load(fh)
        assert raw["general"]["daemon_mode"] == "background"

    def test_load_config_from_directory(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "configs"
        cfg = load_config(str(config_dir))
        assert isinstance(cfg, Config)

    def test_load_config_from_file(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "configs"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "gamarr.yml"
        config_file.write_text("general:\n  daemon_mode: background\n")
        cfg = load_config(str(config_file))
        assert cfg.general.daemon_mode == "background"

    def test_load_config_merges_with_defaults(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "configs"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "gamarr.yml"
        config_file.write_text("")
        cfg = load_config(str(config_file))
        assert cfg.general.daemon_mode == "foreground"
        assert cfg.sources.fitgirl.enabled is True

    def test_missing_optional_key_uses_default(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "configs"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "gamarr.yml"
        config_file.write_text("general:\n  daemon_mode: foreground\n  unknown_key: true\n")
        cfg = load_config(str(config_file))
        assert cfg.general.daemon_mode == "foreground"

    def test_load_config_handles_null_values(self, tmp_path: Path) -> None:
        """A YAML key with a null value should be skipped, not crash."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "gamarr.yml"
        config_file.write_text("general:\n  daemon_mode: ~\n")
        cfg = load_config(str(config_file))
        # null value is skipped, so daemon_mode stays as default
        assert cfg.general.daemon_mode == "foreground"

    def test_load_config_raises_on_non_dict(self, tmp_path: Path) -> None:
        """A YAML file with a non-mapping root raises ValueError."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "gamarr.yml"
        config_file.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            load_config(str(config_file))
