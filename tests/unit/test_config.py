"""Tests for gamarr config module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from pydantic import ValidationError

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
        assert cfg.enabled is False
        assert cfg.schedule_time_mins == 60
        assert cfg.run_on_start is True

    def test_fitgirl_source_config_defaults(self) -> None:
        cfg = FitGirlSourceConfig()
        assert cfg.enabled is True
        assert cfg.rss_url == "https://fitgirl-repacks.site/feed/"
        assert cfg.platform == "pc"
        assert cfg.pending_days == 60
        assert cfg.reject_keywords == []

    def test_fitgirl_reject_keywords_replaces_exclude_keywords(self) -> None:
        """FitGirlSourceConfig should have reject_keywords, not exclude_keywords."""
        cfg = FitGirlSourceConfig(reject_keywords=["hv"])
        assert cfg.reject_keywords == ["hv"]
        assert not hasattr(cfg, "exclude_keywords")

    def test_metacritic_exclude_keywords_removed(self) -> None:
        """MetacriticPlatformConfig should NOT have exclude_keywords (redundant with reject_title)."""
        cfg = MetacriticPlatformConfig()
        assert not hasattr(cfg, "exclude_keywords")

    def test_fitgirl_pending_days_ge_zero(self) -> None:
        """pending_days must be >= 0 (0 disables expiry extension)."""
        with pytest.raises(ValidationError):
            FitGirlSourceConfig(pending_days=-1)
        FitGirlSourceConfig(pending_days=0)
        FitGirlSourceConfig(pending_days=1)

    def test_metacritic_platform_config_defaults(self) -> None:
        cfg = MetacriticPlatformConfig()
        assert cfg.min_metascore == 75
        assert cfg.min_user_score == 7.5
        assert cfg.days_since_release == 90
        assert cfg.max_games == 1000

    def test_migrate_config_renames_browse_keys(self) -> None:
        """_migrate_config should rename old browse_* keys and drop deprecated cutoff_date."""
        from gamarr.config import _migrate_config

        raw = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {
                        "browse_max_pages": 200,
                        "browse_enabled": True,
                        "browse_cutoff_date": "2025-01-01",
                        "browse_cache_ttl_hours": 4,
                    }
                }
            }
        }
        _migrate_config(raw)
        mc_pc = raw["metacritic"]["platform_overrides"]["pc"]
        assert "browse_max_pages" not in mc_pc
        assert "browse_enabled" not in mc_pc
        assert "browse_cutoff_date" not in mc_pc
        assert "browse_cache_ttl_hours" not in mc_pc
        assert "cutoff_date" not in mc_pc
        assert mc_pc["enabled"] is True
        assert mc_pc["cache_ttl_hours"] == 4

    def test_migrate_config_ignores_non_dict_overrides(self) -> None:
        """_migrate_config should skip platform overrides that are not dicts."""
        from gamarr.config import _migrate_config

        raw = {
            "metacritic": {
                "platform_overrides": {
                    "pc": "not-a-dict",
                }
            }
        }
        _migrate_config(raw)  # Should not raise
        assert raw["metacritic"]["platform_overrides"]["pc"] == "not-a-dict"

    def test_migrate_config_renames_metacritic_keys(self) -> None:
        """_migrate_config should rename metacritic_* keys and drop deprecated cutoff_date."""
        from gamarr.config import _migrate_config

        raw = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {
                        "metacritic_enabled": False,
                        "metacritic_max_games": 500,
                        "metacritic_cutoff_date": "2026-06-01",
                        "metacritic_cache_ttl_hours": 12,
                    }
                }
            }
        }
        _migrate_config(raw)
        mc_pc = raw["metacritic"]["platform_overrides"]["pc"]
        assert "metacritic_enabled" not in mc_pc
        assert "metacritic_max_games" not in mc_pc
        assert "metacritic_cutoff_date" not in mc_pc
        assert "metacritic_cache_ttl_hours" not in mc_pc
        assert "cutoff_date" not in mc_pc
        assert mc_pc["enabled"] is False
        assert mc_pc["max_games"] == 500
        assert mc_pc["cache_ttl_hours"] == 12

    def test_migrate_config_handles_exception_gracefully(self) -> None:
        """_migrate_config should catch exceptions and log a warning."""
        from gamarr.config import _migrate_config

        # metacritic value is a list instead of dict → .get() fails → AttributeError
        raw = {
            "metacritic": ["not-a-dict"],
        }
        _migrate_config(raw)  # Should not raise, logs warning

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

    def test_migrate_config_returns_true_on_change(self) -> None:
        """_migrate_config should return True when it makes changes."""
        from gamarr.config import _migrate_config

        raw = {
            "sources": {"fitgirl": {"exclude_keywords": ["hv"]}},
            "metacritic": {"platform_overrides": {"pc": {"exclude_keywords": ["DLC"]}}},
        }
        result = _migrate_config(raw)
        assert result is True, "Should return True because migration ran"
        assert "reject_keywords" in raw["sources"]["fitgirl"]
        assert "exclude_keywords" not in raw["sources"]["fitgirl"]

    def test_migrate_config_returns_false_on_no_change(self) -> None:
        """_migrate_config should return False when nothing to migrate."""
        from gamarr.config import _migrate_config

        raw = {
            "sources": {"fitgirl": {"reject_keywords": ["hv"]}},
            "metacritic": {"platform_overrides": {"pc": {}}},
        }
        result = _migrate_config(raw)
        assert result is False, "Should return False because no migration needed"

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

    def test_next_version_with_bad_part(self) -> None:
        """_next_version should handle non-numeric version parts gracefully."""
        from gamarr.config import _next_version

        assert _next_version("bad") == "bad.1.0"
        assert _next_version("1.bad.0") == "1.1.0"
