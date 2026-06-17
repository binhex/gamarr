"""Tests for gamarr config module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml
from pydantic import ValidationError

from gamarr.config import (
    Config,
    GeneralConfig,
    LibraryConfig,
    MetacriticPlatformConfig,
    NotificationConfig,
    QbittorrentConfig,
    ScheduleTaskConfig,
    SourceConfigEntry,
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

    def test_source_config_entry_defaults(self) -> None:
        cfg = SourceConfigEntry(name="fitgirl")
        assert cfg.name == "fitgirl"
        assert cfg.enabled is True
        assert cfg.feed_url is None
        assert cfg.platform == "pc"
        assert cfg.max_queue_days == 60
        assert cfg.reject_keywords == []

    def test_source_config_entry_reject_keywords(self) -> None:
        """SourceConfigEntry should have reject_keywords, not exclude_keywords."""
        cfg = SourceConfigEntry(name="fitgirl", reject_keywords=["hv"])
        assert cfg.reject_keywords == ["hv"]
        assert not hasattr(cfg, "exclude_keywords")

    def test_metacritic_exclude_keywords_removed(self) -> None:
        """MetacriticPlatformConfig should NOT have exclude_keywords (redundant with reject_title)."""
        cfg = MetacriticPlatformConfig()
        assert not hasattr(cfg, "exclude_keywords")

    def test_metacritic_no_max_verify_attempts(self) -> None:
        """MetacriticPlatformConfig should NOT have max_verify_attempts."""
        cfg = MetacriticPlatformConfig()
        assert not hasattr(cfg, "max_verify_attempts")

    def test_source_config_entry_max_queue_days_ge_zero(self) -> None:
        """max_queue_days must be >= 0 (0 disables expiry extension)."""
        with pytest.raises(ValidationError):
            SourceConfigEntry(name="fitgirl", max_queue_days=-1)
        SourceConfigEntry(name="fitgirl", max_queue_days=0)
        SourceConfigEntry(name="fitgirl", max_queue_days=1)

    def test_metacritic_platform_config_defaults(self) -> None:
        cfg = MetacriticPlatformConfig()
        assert cfg.min_metascore == 75
        assert cfg.min_user_score == 7.5
        assert not hasattr(cfg, "days_since_release"), "Field was removed"
        assert not hasattr(cfg, "pending_days"), "Field was renamed to max_queue_days"
        assert not hasattr(cfg, "recheck_days"), "Field was renamed to max_queue_days"
        assert cfg.max_queue_days == 30
        assert cfg.max_weeks == 13, "Default max_weeks should be ~90 days (13 weeks)"
        assert not hasattr(cfg, "cutoff_weeks"), "Field was renamed to max_weeks"
        assert not hasattr(cfg, "max_games"), "Field was removed — max_weeks controls game count"

    def test_cache_details_days_replaces_cache_ttl_days(self) -> None:
        """MetacriticPlatformConfig should use cache_details_days, not cache_ttl_days."""
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert not hasattr(cfg, "cache_ttl_days"), "Field was renamed to cache_details_days"
        assert hasattr(cfg, "cache_details_days"), "New field name should exist"
        assert cfg.cache_details_days == 7, "Default should remain 7"

    def test_migrate_cache_ttl_days_to_cache_details_days(self) -> None:
        """_migrate_config should rename cache_ttl_days to cache_details_days."""
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {
                        "cache_ttl_days": 7,
                    },
                },
            },
        }
        _migrate_config(raw)
        mc_pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "cache_ttl_days" not in mc_pc, "Old key should be removed"
        assert mc_pc["cache_details_days"] == 7, "New key should have the same value"

    def test_default_config_dict_contains_cache_details_days(self) -> None:
        """The default config dict should use cache_details_days, not cache_ttl_days."""
        from gamarr.config import _default_config_dict

        defaults = _default_config_dict()
        mc_pc = defaults["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "cache_ttl_days" not in mc_pc, "Old key should not appear in defaults"
        assert mc_pc["cache_details_days"] == 7, "New key should have default 7"

    def test_cache_pages_hours_replaces_cache_ttl_hours(self) -> None:
        """MetacriticPlatformConfig should use cache_pages_hours, not cache_ttl_hours."""
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert not hasattr(cfg, "cache_ttl_hours"), "Field was renamed to cache_pages_hours"
        assert hasattr(cfg, "cache_pages_hours"), "New field name should exist"
        assert cfg.cache_pages_hours == 6, "Default should remain 6"

    def test_source_config_entry_cache_pages_hours(self) -> None:
        """SourceConfigEntry should use cache_pages_hours, not cache_ttl_hours."""
        cfg = SourceConfigEntry(name="fitgirl")
        assert not hasattr(cfg, "cache_ttl_hours"), "Field was renamed to cache_pages_hours"
        assert hasattr(cfg, "cache_pages_hours"), "New field name should exist"
        assert cfg.cache_pages_hours == 6, "Default should remain 6"

    def test_migrate_cache_ttl_hours_to_cache_pages_hours(self) -> None:
        """_migrate_config should rename cache_ttl_hours to cache_pages_hours."""
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {
                        "cache_ttl_hours": 6,
                    },
                },
            },
        }
        _migrate_config(raw)
        mc_pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "cache_ttl_hours" not in mc_pc, "Old key should be removed"
        assert mc_pc["cache_pages_hours"] == 6, "New key should have the same value"

    def test_migrate_fitgirl_cache_ttl_hours_to_cache_pages_hours(self) -> None:
        """_migrate_config should rename fitgirl.cache_ttl_hours to cache_pages_hours."""
        from gamarr.config import _migrate_config

        raw = {
            "download_sites": {
                "fitgirl": {
                    "cache_ttl_hours": 12,
                },
            },
        }
        _migrate_config(raw)
        download_sites: Any = raw["download_sites"]
        # After migration, entries are in keyed format: {key: {...}}
        fg_entry = next(e for e in download_sites if isinstance(e, dict) and "fitgirl" in e)
        fg = fg_entry["fitgirl"]
        assert "cache_ttl_hours" not in fg, "Old key should be removed"
        assert fg["cache_pages_hours"] == 12, "New key should have the same value"

    def test_default_config_dict_contains_cache_pages_hours(self) -> None:
        """The default config dict should use cache_pages_hours, not cache_ttl_hours."""
        from gamarr.config import _default_config_dict

        defaults = _default_config_dict()
        mc_pc = defaults["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "cache_ttl_hours" not in mc_pc, "Old key should not appear in defaults"
        assert mc_pc["cache_pages_hours"] == 6, "New key should have default 6"

    def test_migrate_config_renames_browse_keys(self) -> None:
        """_migrate_config should rename old browse_* keys and drop deprecated cutoff_date."""
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
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
        mc_pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "browse_max_pages" not in mc_pc
        assert "browse_enabled" not in mc_pc
        assert "browse_cutoff_date" not in mc_pc
        assert "browse_cache_ttl_hours" not in mc_pc
        assert "cutoff_date" not in mc_pc
        assert mc_pc["enabled"] is True
        assert mc_pc["cache_pages_hours"] == 4

    def test_migrate_config_ignores_non_dict_overrides(self) -> None:
        """_migrate_config should skip platform overrides that are not dicts."""
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": "not-a-dict",
                }
            }
        }
        _migrate_config(raw)  # Should not raise
        assert raw["review_sites"]["metacritic"]["platform_overrides"]["pc"] == "not-a-dict"

    def test_migrate_config_renames_metacritic_keys(self) -> None:
        """_migrate_config should rename metacritic_* keys and drop deprecated cutoff_date."""
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
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
        mc_pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "metacritic_enabled" not in mc_pc
        assert "metacritic_max_games" not in mc_pc
        assert "metacritic_cutoff_date" not in mc_pc
        assert "metacritic_cache_ttl_hours" not in mc_pc
        assert "cutoff_date" not in mc_pc
        assert mc_pc["enabled"] is False
        assert "max_games" not in mc_pc, "max_games was removed — use max_weeks"
        assert mc_pc["cache_pages_hours"] == 12

    def test_migrate_config_handles_exception_gracefully(self) -> None:
        """_migrate_config should catch exceptions and log a warning."""
        from gamarr.config import _migrate_config

        # metacritic value is a list instead of dict → .get() fails → AttributeError
        raw = {
            "metacritic": ["not-a-dict"],
        }
        _migrate_config(raw)  # Should not raise, logs warning

    def test_migrate_max_verify_attempts_removed(self) -> None:
        """Old max_verify_attempts in metacritic.platform_overrides is removed."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"max_verify_attempts": 3},
                },
            },
        }
        result = _migrate_config(raw)
        assert result is True
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "max_verify_attempts" not in pc

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

    def test_config_has_review_sites(self) -> None:
        """Config().review_sites.metacritic should exist, Config().metacritic should not."""
        from gamarr.config import Config

        cfg = Config()
        assert hasattr(cfg, "review_sites"), "Config must have review_sites field"
        assert cfg.review_sites.metacritic is not None
        assert not hasattr(cfg, "metacritic"), "Config should not have top-level metacritic field"

    def test_root_config_defaults(self) -> None:
        cfg = Config()
        assert cfg.general.daemon_mode == "foreground"
        fitgirl = next(e for e in cfg.download_sites if e.name == "fitgirl")
        assert fitgirl.enabled is True
        assert cfg.review_sites.metacritic.platform_overrides["pc"].min_metascore == 75
        assert cfg.torrent_client.selected == "qbittorrent"

    def test_age_recheck_weeks_default(self) -> None:
        """MetacriticPlatformConfig.age_recheck_weeks defaults to None."""
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert cfg.age_recheck_weeks is None

    def test_max_cycle_weeks_default(self) -> None:
        """MetacriticPlatformConfig.max_cycle_weeks defaults to 4."""
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert cfg.max_cycle_weeks == 4

    def test_max_cycle_weeks_ge_zero(self) -> None:
        """max_cycle_weeks must be >= 0 (0 or None = unlimited)."""
        from pydantic import ValidationError

        from gamarr.config import MetacriticPlatformConfig

        MetacriticPlatformConfig(max_cycle_weeks=0)
        MetacriticPlatformConfig(max_cycle_weeks=None)
        MetacriticPlatformConfig(max_cycle_weeks=4)
        with pytest.raises(ValidationError):
            MetacriticPlatformConfig(max_cycle_weeks=-1)


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

        raw: dict[str, Any] = {
            "sources": {"fitgirl": {"exclude_keywords": ["hv"]}},
            "metacritic": {"platform_overrides": {"pc": {"exclude_keywords": ["DLC"]}}},
        }
        result = _migrate_config(raw)
        assert result is True, "Should return True because migration ran"
        assert "sources" not in raw, "Old sources key should be removed by sources→download_sites migration"
        # After migration, entries are in keyed format: {key: {...}}
        fg_entry = next(e for e in raw["download_sites"] if isinstance(e, dict) and "fitgirl" in e)
        fg = fg_entry["fitgirl"]
        assert "reject_keywords" in fg
        assert "exclude_keywords" not in fg

    def test_migrate_metacritic_to_review_sites(self) -> None:
        """Old top-level metacritic key is moved under review_sites."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"min_metascore": 75, "max_weeks": 12},
                },
            },
        }
        result = _migrate_config(raw)
        assert result is True
        assert "metacritic" not in raw, "Old top-level metacritic key should be removed"
        assert "review_sites" in raw
        assert raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["min_metascore"] == 75

    def test_migrate_sources_to_download_sites(self) -> None:
        """Old sources key is migrated to download_sites."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "sources": {"fitgirl": {"reject_keywords": ["hv"]}},
            "metacritic": {"platform_overrides": {"pc": {}}},
        }
        result = _migrate_config(raw)
        assert result is True
        assert "sources" not in raw, "Old sources key should be removed"
        assert "download_sites" in raw, "New download_sites key should exist"
        # After migration, entries are in keyed format: {key: {...}}
        fg_entry = next(e for e in raw["download_sites"] if isinstance(e, dict) and "fitgirl" in e)
        fg = fg_entry["fitgirl"]
        assert fg["reject_keywords"] == ["hv"]
        # Other fitgirl defaults (enabled, feed_url, etc.) are added by _deep_merge
        # with _default_config_dict during load_config, not by the raw migration

    def test_migrate_sources_to_download_sites_both_exist(self) -> None:
        """When both old sources and new download_sites exist, merge and drop sources."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "sources": {"fitgirl": {"new_key": "old_val"}},
            "download_sites": {"fitgirl": {"existing_key": "existing_val"}},
            "metacritic": {"platform_overrides": {"pc": {}}},
        }
        result = _migrate_config(raw)
        assert result is True
        assert "sources" not in raw
        # After migration, entries are in keyed format: {key: {...}}
        fg_entry = next(e for e in raw["download_sites"] if isinstance(e, dict) and "fitgirl" in e)
        fg = fg_entry["fitgirl"]
        assert fg["existing_key"] == "existing_val"
        assert fg["new_key"] == "old_val"

    def test_migrate_config_returns_false_on_no_change(self) -> None:
        """_migrate_config should return True when migrating to keyed-list format."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "download_sites": [
                {"name": "fitgirl", "reject_keywords": ["hv"]},
                {"name": "dodi", "enabled": True},
            ],
            "review_sites": {"metacritic": {"platform_overrides": {"pc": {}}}},
        }
        result = _migrate_config(raw)
        # Migration converts [{name: ...}] to [{key: {...}}] format
        assert result is True, "Should return True because keyed-list migration runs"
        # Verify entries are in keyed format
        assert isinstance(raw["download_sites"], list)
        for entry in raw["download_sites"]:
            assert isinstance(entry, dict)
            # Should NOT have "name" key directly (it's nested under the source key)
            assert "name" not in entry

    def test_migrate_days_since_release_removes_field(self) -> None:
        """Old days_since_release in metacritic.platform_overrides is removed."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"days_since_release": 90, "cutoff_weeks": 12},
                },
            },
        }
        result = _migrate_config(raw)
        assert result is True
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "days_since_release" not in pc
        assert "cutoff_weeks" not in pc, "cutoff_weeks was renamed to max_weeks"
        assert pc["max_weeks"] == 12

    def test_migrate_days_since_release_converts_to_max_weeks(self) -> None:
        """days_since_release without max_weeks should be converted."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"days_since_release": 120},
                },
            },
        }
        result = _migrate_config(raw)
        assert result is True
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "days_since_release" not in pc
        assert "cutoff_weeks" not in pc, "cutoff_weeks was renamed to max_weeks"
        assert pc["max_weeks"] == 17  # 120 / 7 ≈ 17

    def test_migrate_pending_days_to_max_queue_days(self) -> None:
        """Old pending_days key is renamed to max_queue_days in metacritic and fitgirl sections."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"pending_days": 30, "cutoff_weeks": 12},
                },
            },
            "download_sites": {
                "fitgirl": {"pending_days": 60, "rss_url": "http://example.com"},
            },
        }
        result = _migrate_config(raw)
        assert result is True
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "pending_days" not in pc
        assert "recheck_days" not in pc, "recheck_days was renamed to max_queue_days"
        assert pc["max_queue_days"] == 30
        # After migration, entries are in keyed format: {key: {...}}
        fg_entry = next(e for e in raw["download_sites"] if isinstance(e, dict) and "fitgirl" in e)
        fg = fg_entry["fitgirl"]
        assert "pending_days" not in fg
        assert "recheck_days" not in fg, "recheck_days was renamed to max_queue_days"
        assert fg["max_queue_days"] == 60

    def test_migrate_pending_days_under_sources_key(self) -> None:
        """Old pending_days under the legacy sources key is also renamed."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"pending_days": 45},
                },
            },
            "sources": {
                "fitgirl": {"pending_days": 90, "reject_keywords": ["test"]},
            },
        }
        result = _migrate_config(raw)
        assert result is True
        assert "sources" not in raw
        assert "recheck_days" not in raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["max_queue_days"] == 45
        # After migration, entries are in keyed format: {key: {...}}
        fg_entry = next(e for e in raw["download_sites"] if isinstance(e, dict) and "fitgirl" in e)
        fg = fg_entry["fitgirl"]
        assert "recheck_days" not in fg
        assert fg["max_queue_days"] == 90

    def test_migrate_recheck_days_to_max_queue_days(self) -> None:
        """Old recheck_days keys are renamed to max_queue_days."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"recheck_days": 30, "max_weeks": 12},
                },
            },
            "download_sites": {
                "fitgirl": {"recheck_days": 60, "rss_url": "http://example.com"},
            },
        }
        result = _migrate_config(raw)
        assert result is True
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "recheck_days" not in pc, "recheck_days was renamed to max_queue_days"
        assert pc["max_queue_days"] == 30
        # After migration, entries are in keyed format: {key: {...}}
        fg_entry = next(e for e in raw["download_sites"] if isinstance(e, dict) and "fitgirl" in e)
        fg = fg_entry["fitgirl"]
        assert "recheck_days" not in fg, "recheck_days was renamed to max_queue_days"
        assert fg["max_queue_days"] == 60

    def test_migrate_metacritic_exclude_keywords_returns_true(self) -> None:
        """_migrate_metacritic_exclude_keywords should return True when it deletes a key."""

        from gamarr.config import _migrate_metacritic_exclude_keywords

        raw: dict[str, Any] = {
            "review_sites": {"metacritic": {"platform_overrides": {"pc": {"exclude_keywords": ["DLC"]}}}},
        }
        result = _migrate_metacritic_exclude_keywords(raw)
        assert result is True
        assert "exclude_keywords" not in raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]

    def test_migrate_metacritic_exclude_keywords_returns_false(self) -> None:
        """_migrate_metacritic_exclude_keywords should return False when no key to delete."""

        from gamarr.config import _migrate_metacritic_exclude_keywords

        raw: dict[str, Any] = {
            "review_sites": {"metacritic": {"platform_overrides": {"pc": {}}}},
        }
        result = _migrate_metacritic_exclude_keywords(raw)
        assert result is False

    def test_load_config_merges_with_defaults(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "configs"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "gamarr.yml"
        config_file.write_text("")
        cfg = load_config(str(config_file))
        assert cfg.general.daemon_mode == "foreground"
        fitgirl = next(e for e in cfg.download_sites if e.name == "fitgirl")
        assert fitgirl.enabled is True

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


def test_config_migration_flat_to_ordered() -> None:
    """Old flat download_sites.fitgirl.* auto-migrates to ordered list."""
    import os
    import tempfile

    import yaml

    from gamarr.config import load_config

    old_config = {
        "general": {"db_path": ":memory:"},
        "download_sites": {
            "fitgirl": {
                "enabled": True,
                "rss_url": "https://fitgirl-repacks.site/feed/",
                "platform": "pc",
                "cache_pages_hours": 6,
                "reject_keywords": ["update"],
                "max_queue_days": 60,
            }
        },
        "torrent_client": {
            "qbittorrent": {
                "host": "localhost",
                "port": 8080,
                "username": "admin",
                "password": "adminadmin",
            }
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(old_config, f)
        f.flush()
    try:
        cfg = load_config(f.name)
        ds = cfg.download_sites
        assert len(ds) == 2
        assert ds[0].name == "fitgirl"
        assert ds[0].feed_url == "https://fitgirl-repacks.site/feed/"
        assert ds[0].reject_keywords == ["update"]
        assert ds[1].name == "dodi"
        assert ds[1].enabled is True
    finally:
        os.unlink(f.name)


def _find_in_keyed(ds: list[Any], name: str) -> dict[str, Any] | None:
    """Find an entry by name in a keyed-format download_sites list.

    Handles both keyed format [{key: {...}}] and flat [{name: ..., ...}].
    """
    for entry in ds:
        if not isinstance(entry, dict):
            continue
        if name in entry:
            inner = entry[name]
            return inner if isinstance(inner, dict) else {}
        if entry.get("name") == name:
            return entry
    return None


def test_migrate_config_with_list_download_sites_does_not_crash() -> None:
    """_migrate_config handles list-format download_sites without crashing.

    Regression test: when download_sites is already a list (new format),
    dict-based migration functions must not call .get("fitgirl") on the list.

    Previously these functions did raw.get("download_sites", {}).get("fitgirl")
    which raises AttributeError on a list. The exception was caught and
    swallowed, silently failing the migration (#553).
    """

    from gamarr.config import _migrate_config

    raw: dict[str, Any] = {
        "download_sites": [
            {
                "name": "fitgirl",
                "enabled": True,
                "rss_url": "https://fitgirl-repacks.site/feed/",
                "platform": "pc",
                "cache_pages_hours": 6,
                "reject_keywords": [],
                "max_queue_days": 60,
            }
        ],
        "review_sites": {
            "metacritic": {
                "platform_overrides": {
                    "pc": {
                        "min_metascore": 75,
                        "min_metascore_reviews": 10,
                        "min_user_score": 7.5,
                        "min_user_reviews": 10,
                        "max_queue_days": 30,
                    }
                }
            }
        },
        "torrent_client": {
            "qbittorrent": {
                "host": "localhost",
                "port": 8080,
                "username": "admin",
                "password": "<placeholder>",
            }
        },
    }
    from unittest.mock import patch

    with patch("gamarr.config.logger") as mock_logger:
        result = _migrate_config(raw)
    # Verify no migration failure warning was emitted
    warning_calls = [c for c in mock_logger.warning.call_args_list if "Config migration failed" in str(c)]
    assert len(warning_calls) == 0, f"Expected no migration failures, got: {[str(c) for c in warning_calls]}"
    assert result is True  # DODI was added + keyed-list migration
    # After migration, entries are in keyed format: {key: {...}}
    fitgirl_entry = _find_in_keyed(raw["download_sites"], "fitgirl")
    assert fitgirl_entry is not None, "Expected fitgirl entry"
    dodi_entry = _find_in_keyed(raw["download_sites"], "dodi")
    assert dodi_entry is not None, "Expected dodi entry"
    assert dodi_entry.get("enabled") is True


def test_deep_merge_strips_null_from_download_sites_list_entries() -> None:
    """_deep_merge strips None dict-values from list items so Pydantic validation succeeds.

    When a user writes YAML such as::

        download_sites:
          - name: fitgirl
            reject_keywords: null

    yaml.safe_load parses ``null`` as ``None``.  Pydantic requires ``reject_keywords``
    to be a list, so passing ``None`` raises ValidationError.  _deep_merge must
    strip ``None`` from dict values inside lists to prevent this.
    """
    import os
    import tempfile

    from gamarr.config import load_config

    config_text = (
        "general:\n"
        "  daemon_mode: foreground\n"
        "download_sites:\n"
        "  - name: fitgirl\n"
        "    enabled: true\n"
        "    feed_url: null\n"
        "    reject_keywords: null\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(config_text)
        f.flush()
        try:
            cfg = load_config(f.name)
            fg = next(e for e in cfg.download_sites if e.name == "fitgirl")
            assert fg.reject_keywords == [], "None should be stripped from reject_keywords"
            assert fg.feed_url is None  # feed_url is Optional[str], so None is fine
        finally:
            os.unlink(f.name)


def test_default_config_includes_dodi() -> None:
    """_default_config_dict includes a dodi entry in download_sites."""
    from gamarr.config import _default_config_dict

    defaults = _default_config_dict()
    dodi_entry = None
    for entry in defaults.get("download_sites", []):
        if isinstance(entry, dict) and "dodi" in entry:
            dodi_entry = entry["dodi"]
            break
    assert dodi_entry is not None, "Expected a 'dodi' entry in default download_sites"
    assert dodi_entry.get("enabled") is True


def test_migrate_adds_dodi_when_missing() -> None:
    """_migrate_config adds a dodi entry when download_sites has no dodi entry."""
    from gamarr.config import _migrate_config

    raw: dict[str, Any] = {
        "download_sites": [
            {
                "name": "fitgirl",
                "enabled": True,
                "rss_url": "https://fitgirl-repacks.site/feed/",
                "platform": "pc",
                "cache_pages_hours": 6,
                "reject_keywords": [],
                "max_queue_days": 60,
            }
        ],
        "review_sites": {
            "metacritic": {
                "platform_overrides": {
                    "pc": {
                        "min_metascore": 75,
                        "min_metascore_reviews": 10,
                        "min_user_score": 7.5,
                        "min_user_reviews": 10,
                        "max_queue_days": 30,
                    }
                }
            }
        },
        "torrent_client": {
            "qbittorrent": {
                "host": "localhost",
                "port": 8080,
                "username": "admin",
                "password": "adminadmin",
            }
        },
    }
    from unittest.mock import patch

    with patch("gamarr.config.logger") as _:
        result = _migrate_config(raw)
    # Verify dodi was added - entries are now in keyed format
    assert result is True
    fitgirl_entry = _find_in_keyed(raw["download_sites"], "fitgirl")
    assert fitgirl_entry is not None, "Expected 'fitgirl' entry"
    dodi_entry = _find_in_keyed(raw["download_sites"], "dodi")
    assert dodi_entry is not None, "Expected 'dodi' to be added"
    assert dodi_entry.get("enabled") is True


def test_migrate_does_not_duplicate_dodi() -> None:
    """_migrate_config does not add a second dodi entry when one already exists."""
    from gamarr.config import _migrate_config

    raw: dict[str, Any] = {
        "download_sites": [
            {"name": "fitgirl", "enabled": True},
            {"name": "dodi", "enabled": True},
        ],
        "review_sites": {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"min_metascore": 75},
                }
            }
        },
    }
    from unittest.mock import patch

    with patch("gamarr.config.logger"):
        _ = _migrate_config(raw)
    # After migration, entries are in keyed format: {key: {...}}
    dodi_entry = _find_in_keyed(raw["download_sites"], "dodi")
    assert dodi_entry is not None, "dodi should exist"
    fitgirl_entry = _find_in_keyed(raw["download_sites"], "fitgirl")
    assert fitgirl_entry is not None, "fitgirl should exist"
    assert len(raw["download_sites"]) == 2, "Should not duplicate dodi entry"


def test_rename_config_key_identical_keys() -> None:
    """_rename_config_key no-ops when old_key == new_key."""
    from gamarr.config import _rename_config_key

    mc_pc = {"cache_pages_hours": 6}
    result = _rename_config_key(mc_pc, "cache_pages_hours", "cache_pages_hours", "pc")
    assert result is False
    assert mc_pc["cache_pages_hours"] == 6  # Not deleted


def test_rename_config_key_both_old_and_new_present() -> None:
    """_rename_config_key preserves new-key value when both old and new keys exist."""
    from gamarr.config import _rename_config_key

    mc_pc = {"exclude_keywords": ["hv"], "reject_keywords": ["update"]}
    result = _rename_config_key(mc_pc, "exclude_keywords", "reject_keywords", "pc")
    assert result is True  # Cleanup happened
    assert "exclude_keywords" not in mc_pc  # Old key deleted
    assert mc_pc["reject_keywords"] == ["update"]  # New key value preserved


def test_rename_config_key_delete_only() -> None:
    """_rename_config_key deletes key when new_key is None."""
    from gamarr.config import _rename_config_key

    mc_pc = {"old_browse_max_pages": 100}
    result = _rename_config_key(mc_pc, "old_browse_max_pages", None, "pc")
    assert result is True
    assert "old_browse_max_pages" not in mc_pc


def test_deep_merge_list_replaces_dict() -> None:
    """_deep_merge replaces dict entry with list entry when types differ."""
    from gamarr.config import _deep_merge

    base = {"download_sites": {"fitgirl": {"enabled": True}}}
    override = {"download_sites": [{"name": "fitgirl", "enabled": False}]}
    result = _deep_merge(base, override)
    assert isinstance(result["download_sites"], list)
    assert result["download_sites"][0]["enabled"] is False


def test_deep_merge_strips_none_nested() -> None:
    """_deep_merge strips None values from nested dicts within lists."""
    from gamarr.config import _deep_merge

    base: dict[str, Any] = {"download_sites": []}
    override = {
        "download_sites": [
            {"name": "fitgirl", "reject_keywords": ["hv", None]},
        ]
    }
    result = _deep_merge(base, override)
    entries = result["download_sites"]
    assert len(entries) == 1
    assert entries[0]["reject_keywords"] == ["hv"]


def test_deep_merge_override_key_not_in_base() -> None:
    """_deep_merge adds keys from override that don't exist in base."""
    from gamarr.config import _deep_merge

    base: dict[str, Any] = {}
    override = {"new_key": "value"}
    result = _deep_merge(base, override)
    assert result["new_key"] == "value"


# --- rss_url → feed_url, name excluded, keyed-list support ---


def test_parse_keyed_list() -> None:
    """DownloadSitesConfig parses [{'fitgirl': {'enabled': True}}] correctly."""
    from gamarr.config import DownloadSitesConfig

    raw: list[dict[str, Any]] = [
        {"fitgirl": {"enabled": True, "feed_url": "https://example.com/feed"}},
        {"dodi": {"enabled": True}},
    ]
    cfg = DownloadSitesConfig(root=raw)  # type: ignore[arg-type]
    assert len(cfg) == 2
    assert cfg[0].name == "fitgirl"
    assert cfg[0].enabled is True
    assert cfg[0].feed_url == "https://example.com/feed"
    assert cfg[1].name == "dodi"
    assert cfg[1].enabled is True


def test_parse_keyed_list_legacy() -> None:
    """DownloadSitesConfig still handles old [{name: ..., rss_url: ...}] format."""
    from gamarr.config import DownloadSitesConfig

    raw: list[dict[str, Any]] = [
        {"name": "fitgirl", "rss_url": "https://example.com/feed"},
    ]
    cfg = DownloadSitesConfig(root=raw)  # type: ignore[arg-type]
    assert len(cfg) == 1
    assert cfg[0].name == "fitgirl"
    assert cfg[0].feed_url == "https://example.com/feed"  # rss_url renamed
    assert "rss_url" not in cfg[0].model_dump()


def test_name_excluded_from_dump() -> None:
    """SourceConfigEntry.name is excluded from dict serialization."""
    from gamarr.config import SourceConfigEntry

    entry = SourceConfigEntry(name="fitgirl", feed_url="https://example.com/feed")
    data = entry.model_dump()
    assert "name" not in data
    assert data["feed_url"] == "https://example.com/feed"
    assert data["enabled"] is True


def test_migrate_download_sites_to_keyed_list() -> None:
    """_migrate_config converts legacy [{name: ..., rss_url: ...}] to keyed format."""
    from unittest.mock import patch

    from gamarr.config import _migrate_config

    raw: dict[str, Any] = {
        "download_sites": [
            {
                "name": "fitgirl",
                "enabled": True,
                "rss_url": "https://fitgirl-repacks.site/feed/",
                "platform": "pc",
                "cache_pages_hours": 6,
                "reject_keywords": [],
                "max_queue_days": 60,
            },
            {"name": "dodi", "enabled": True},
        ],
        "review_sites": {"metacritic": {"platform_overrides": {"pc": {}}}},
        "torrent_client": {
            "qbittorrent": {"host": "localhost", "port": 8080, "username": "admin", "password": "adminadmin"}
        },
    }
    with patch("gamarr.config.logger"):
        result = _migrate_config(raw)

    assert result is True
    ds = raw["download_sites"]
    assert isinstance(ds, list)
    # Find fitgirl entry
    for entry in ds:
        if isinstance(entry, dict) and "fitgirl" in entry:
            inner = entry["fitgirl"]
            assert "rss_url" not in inner
            assert inner["feed_url"] == "https://fitgirl-repacks.site/feed/"
            break
    else:
        raise AssertionError("fitgirl entry not found in keyed format")


def test_migrate_download_sites_to_keyed_list_renames_rss_url() -> None:
    """_migrate_download_sites_to_keyed_list renames rss_url to feed_url."""
    from unittest.mock import patch

    from gamarr.config import _migrate_download_sites_to_keyed_list

    raw: dict[str, Any] = {
        "download_sites": [
            {"name": "fitgirl", "enabled": True, "rss_url": "https://old-url.com/feed"},
        ],
    }
    with patch("gamarr.config.logger"):
        result = _migrate_download_sites_to_keyed_list(raw)
    assert result is True
    # Find fitgirl in keyed format
    ds = raw["download_sites"]
    assert len(ds) == 1
    entry = ds[0]
    assert isinstance(entry, dict)
    assert "fitgirl" in entry
    inner = entry["fitgirl"]
    assert inner["feed_url"] == "https://old-url.com/feed"
    assert "rss_url" not in inner


def test_parse_keyed_list_non_dict_val() -> None:
    """_parse_keyed_list handles non-dict values (e.g. None, str) gracefully."""
    from gamarr.config import DownloadSitesConfig

    raw: list[dict[str, Any]] = [
        {"fitgirl": None},
        {"dodi": "bare_string"},
    ]
    cfg = DownloadSitesConfig(root=raw)  # type: ignore[arg-type]
    assert len(cfg) == 2
    assert cfg[0].name == "fitgirl"
    assert cfg[1].name == "dodi"


def test_migrate_adds_dodi_with_full_fields() -> None:
    """_migrate_add_dodi_entry adds a dodi entry with all default fields."""
    from unittest.mock import patch

    from gamarr.config import _migrate_add_dodi_entry

    raw: dict[str, Any] = {
        "download_sites": [
            {"fitgirl": {"enabled": True, "feed_url": "https://example.com/feed"}},
        ],
    }
    with patch("gamarr.config.logger"):
        result = _migrate_add_dodi_entry(raw)
    assert result is True
    # Find dodi in keyed format
    dodi_entry = None
    for entry in raw["download_sites"]:
        if isinstance(entry, dict) and "dodi" in entry:
            dodi_entry = entry["dodi"]
            break
    assert dodi_entry is not None, "DODI entry not found"
    assert dodi_entry["enabled"] is True
    assert dodi_entry.get("feed_url") is not None, "DODI should have a feed_url"
    assert dodi_entry.get("platform") == "pc"
    assert dodi_entry.get("cache_pages_hours") == 6
    assert dodi_entry.get("reject_keywords") == []
    assert dodi_entry.get("max_queue_days") == 60
