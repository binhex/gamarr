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
    ScheduleConfig,
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
        """ScheduleConfig defaults are correct (flattened format)."""
        cfg = ScheduleConfig()
        assert cfg.enabled is False
        assert cfg.schedule_time_mins == 60
        assert cfg.run_on_start is True

    def test_source_config_entry_defaults(self) -> None:
        cfg = SourceConfigEntry(name="fitgirl")
        assert cfg.name == "fitgirl"
        assert cfg.enabled is True
        assert cfg.platform == "pc"
        assert cfg.source_matching_days is None, "unset means inherit the top-level window"
        assert cfg.reject_keywords == []

    def test_source_config_entry_has_no_legacy_window(self) -> None:
        """The queue window is ``source_matching_days``; the legacy key is gone."""
        assert not hasattr(SourceConfigEntry(name="fitgirl"), "max_queue_days")

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

    def test_source_config_entry_source_matching_days_ge_zero(self) -> None:
        """source_matching_days must be >= 0 (0 disables expiry extension)."""
        with pytest.raises(ValidationError):
            SourceConfigEntry(name="fitgirl", source_matching_days=-1)
        SourceConfigEntry(name="fitgirl", source_matching_days=0)
        SourceConfigEntry(name="fitgirl", source_matching_days=1)

    def test_metacritic_platform_config_defaults(self) -> None:
        cfg = MetacriticPlatformConfig()
        assert cfg.min_criticscore == 75
        assert cfg.min_user_score == 7.5
        assert not hasattr(cfg, "days_since_release"), "Field was removed"
        assert not hasattr(cfg, "pending_days"), "Field was renamed to max_queue_days"
        assert not hasattr(cfg, "recheck_days"), "Field was renamed to max_queue_days"
        assert not hasattr(cfg, "cutoff_weeks"), "Field was renamed to max_pages"
        assert not hasattr(cfg, "max_games"), "Field was removed — max_pages controls game count"

    def test_flat_discovery_keys_were_removed(self) -> None:
        """Browsing keys live in ``discovery``; no flat copies remain on the platform config."""
        cfg = MetacriticPlatformConfig()
        legacy = (
            "mode",
            "max_queue_days",
            "max_pages",
            "max_cycle_pages",
            "max_date",
            "max_cycle_months",
            "sort_order",
        )
        for name in legacy:
            assert not hasattr(cfg, name), f"{name} must live in discovery or a per-source window"

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

    def test_cache_hours_replaces_cache_ttl_hours(self) -> None:
        """MetacriticPlatformConfig should use cache_hours, not cache_ttl_hours."""
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert not hasattr(cfg, "cache_ttl_hours"), "Field was renamed to cache_hours"
        assert not hasattr(cfg, "cache_pages_hours"), "Field was renamed again to cache_hours"
        assert cfg.cache_hours == 6, "Default should remain 6"

    def test_source_config_entry_cache_hours(self) -> None:
        """SourceConfigEntry should use cache_hours, not cache_ttl_hours."""
        cfg = SourceConfigEntry(name="fitgirl")
        assert not hasattr(cfg, "cache_ttl_hours"), "Field was renamed to cache_hours"
        assert not hasattr(cfg, "cache_pages_hours"), "Field was renamed again to cache_hours"
        assert cfg.cache_hours == 6, "Default should remain 6"

    def test_migrate_cache_ttl_hours_to_cache_hours(self) -> None:
        """_migrate_config should rename cache_ttl_hours to cache_hours."""
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
        assert mc_pc["cache_hours"] == 6, "New key should have the same value"

    def test_migrate_fitgirl_cache_ttl_hours_to_cache_hours(self) -> None:
        """_migrate_config should rename fitgirl.cache_ttl_hours to cache_hours."""
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
        assert fg["cache_hours"] == 12, "New key should have the same value"

    def test_default_config_dict_contains_cache_hours(self) -> None:
        """The default config dict should use cache_hours, not cache_ttl_hours."""
        from gamarr.config import _default_config_dict

        defaults = _default_config_dict()
        mc_pc = defaults["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "cache_ttl_hours" not in mc_pc, "Old key should not appear in defaults"
        assert mc_pc["cache_hours"] == 6, "New key should have default 6"

    def test_migrate_config_adds_freegog_to_download_sites(self) -> None:
        """_migrate_config should append freegog to download_sites when missing."""
        from gamarr.config import _migrate_config

        # Simulate existing config with only fitgirl in download_sites
        raw: dict[str, Any] = {
            "download_sites": [
                {"fitgirl": {"enabled": True, "feed_url": "https://example.com/feed/"}},
            ],
        }
        result = _migrate_config(raw)
        assert result is True, "Migration should return True when freegog is added"
        ds: Any = raw["download_sites"]
        assert len(ds) >= 2, f"Expected at least 2 download_sites entries, got {len(ds)}"
        names = [list(e.keys())[0] for e in ds]
        assert "freegog" in names, "freegog should be in download_sites after migration"
        assert "fitgirl" in names, "fitgirl should remain in download_sites"
        # freegog should be appended after fitgirl (preserving default source priority)
        assert names[-1] == "freegog", f"freegog should be last (appended), got {names}"
        # freegog entry should have full defaults, not just "enabled: true"
        fg_entry = next(e["freegog"] for e in ds if isinstance(e, dict) and "freegog" in e)
        assert fg_entry["enabled"] is True
        assert fg_entry["platform"] == "pc"
        assert fg_entry["cache_hours"] == 6
        assert fg_entry["reject_keywords"] == []
        assert "source_matching_days" not in fg_entry, "an unset window inherits the top-level default"

    def test_migrate_config_upgrades_sparse_freegog_entry(self) -> None:
        """_migrate_config should populate missing fields in an existing sparse freegog entry."""
        from gamarr.config import _migrate_config

        # Simulate a stale sparse entry left by the first version of the migration
        raw: dict[str, Any] = {
            "download_sites": [
                {"freegog": {"enabled": True}},  # only 'enabled', no defaults
                {"fitgirl": {"enabled": True, "feed_url": "https://example.com/feed/"}},
            ],
        }
        result = _migrate_config(raw)
        assert result is True, "Migration should return True when upgrading sparse entry"
        ds: Any = raw["download_sites"]
        fg_entry = next(e["freegog"] for e in ds if isinstance(e, dict) and "freegog" in e)
        assert fg_entry["enabled"] is True
        assert fg_entry["platform"] == "pc", "platform should be populated"
        assert fg_entry["cache_hours"] == 6, "cache_hours should be populated"
        assert fg_entry["reject_keywords"] == [], "reject_keywords should be populated"
        assert "source_matching_days" not in fg_entry, "an unset window inherits the top-level default"

    def test_malformed_freegog_entry_does_not_abort_later_migrations(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "download_sites": [{"freegog": "invalid"}],
            "review_sites": {"metacritic": {"platform_overrides": {"pc": {"search_mode": "backlog"}}}},
        }

        assert _migrate_config(raw) is True
        assert "search_mode" not in raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]

    def test_migrate_config_does_not_duplicate_freegog(self) -> None:
        """_migrate_config should not add freegog if it already exists with full defaults."""
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "download_sites": [
                {
                    "freegog": {
                        "enabled": True,
                        "platform": "pc",
                        "cache_hours": 6,
                        "reject_keywords": [],
                        "source_matching_days": 60,
                    }
                },
                {
                    "fitgirl": {
                        "enabled": True,
                        "platform": "pc",
                        "cache_hours": 6,
                        "reject_keywords": [],
                        "source_matching_days": 60,
                    }
                },
            ],
        }
        result = _migrate_config(raw)
        # Migration returns False because nothing was changed (both entries already complete)
        assert result is False, "Migration should return False when nothing needs changing"
        ds: Any = raw["download_sites"]
        names = [list(e.keys())[0] for e in ds]
        assert names.count("freegog") == 1, "freegog should appear exactly once"

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
        assert mc_pc["cache_hours"] == 4

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
        assert "max_games" not in mc_pc, "max_games was removed — use max_pages"
        assert mc_pc["cache_hours"] == 12

    def test_migrate_config_handles_exception_gracefully(self) -> None:
        """_migrate_config should catch exceptions and log a warning."""
        from copy import deepcopy

        from gamarr.config import _migrate_config

        # metacritic value is a list instead of dict → .get() fails → AttributeError
        raw = {
            "metacritic": ["not-a-dict"],
        }
        original = deepcopy(raw)
        _migrate_config(raw)  # Should not raise, logs warning
        assert raw == original

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
        assert cfg.review_sites.metacritic.platform_overrides["pc"].min_criticscore == 75
        assert cfg.torrent_client.selected == "qbittorrent"

    def test_pages_per_cycle_default(self) -> None:
        """discovery.backlog.score.pages_per_cycle defaults to 20."""
        from gamarr.config import BacklogScoreConfig

        assert BacklogScoreConfig().pages_per_cycle == 20

    def test_pages_per_cycle_ge_zero(self) -> None:
        """pages_per_cycle must be >= 0 (0 is coerced to the default pace at plan time)."""
        from pydantic import ValidationError

        from gamarr.config import BacklogScoreConfig

        BacklogScoreConfig(pages_per_cycle=0)
        BacklogScoreConfig(pages_per_cycle=4)
        with pytest.raises(ValidationError):
            BacklogScoreConfig(pages_per_cycle=-1)


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

    def test_load_config_existing_dotless_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "gamarr"
        config_file.write_text("general:\n  daemon_mode: foreground\n")

        cfg = load_config(config_file)

        assert cfg.general.daemon_mode == "foreground"
        assert config_file.is_file()

    def test_load_config_existing_dotted_directory(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config.d"
        config_dir.mkdir()

        cfg = load_config(config_dir)

        assert isinstance(cfg, Config)
        assert (config_dir / "gamarr.yml").is_file()

    def test_load_config_rejects_file_parent(self, tmp_path: Path) -> None:
        parent_file = tmp_path / "not-a-directory"
        parent_file.write_text("blocker")

        with pytest.raises(ValueError, match="is not a file"):
            load_config(parent_file / "child.yml")

    def test_create_default_config_handles_file_parent(self, tmp_path: Path) -> None:
        parent_file = tmp_path / "not-a-directory"
        parent_file.write_text("blocker")
        config_file = parent_file / "child.yml"

        create_default_config(config_file)

        assert not config_file.exists()

    @pytest.mark.parametrize("config_text", ["general: yes\n", "review_sites: yes\n", "download_sites: 42\n"])
    def test_load_config_rejects_malformed_top_level_sections(self, tmp_path: Path, config_text: str) -> None:
        config_file = tmp_path / "gamarr.yml"
        config_file.write_text(config_text)

        with pytest.raises(ValidationError):
            load_config(config_file)

    def test_failed_migration_does_not_rewrite_original_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "gamarr.yml"
        config_file.write_text("metacritic:\n  platform_overrides: {}\nreview_sites:\n  metacritic: invalid\n")
        original = config_file.read_text()

        with pytest.raises(ValidationError):
            load_config(config_file)

        assert config_file.read_text() == original

    def test_a_config_that_cannot_validate_is_not_written(self, tmp_path: Path) -> None:
        """A start that dies in validation must leave the operator's file alone.

        The file is missing most defaults, so a rewrite would normally fire; the
        invalid floor must stop the write instead of persisting it.
        """
        config_file = tmp_path / "gamarr.yml"
        config_file.write_text(
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n"
            "        discovery:\n          mode: backlog\n          backlog:\n            new:\n"
            "              oldest_month: 120\n"
        )
        original = config_file.read_text()

        with pytest.raises(ValidationError):
            load_config(config_file)

        assert config_file.read_text() == original

    def test_a_legacy_junk_floor_does_not_stop_startup(self, tmp_path: Path) -> None:
        """The migration repairs a legacy value instead of producing an unloadable file."""
        from gamarr.config import _default_floor

        config_file = tmp_path / "gamarr.yml"
        config_file.write_text(
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n"
            "        mode: backlog\n        max_date: 120\n        max_cycle_months: 1\n"
        )

        cfg = load_config(config_file)

        new_block = cfg.review_sites.metacritic.platform_overrides["pc"].discovery.backlog.new
        assert new_block.oldest_month == _default_floor(), "the floor is a real month, not 120"
        assert new_block.months_per_cycle == 1, "the legacy pace is preserved"

    def test_the_rewrite_does_not_persist_the_injected_name(self, tmp_path: Path) -> None:
        """Validation injects `name` into keyed source entries; the file must keep its shape."""
        config_file = tmp_path / "gamarr.yml"
        config_file.write_text('general: {config_version: "1.58.0"}\ndownload_sites:\n  fitgirl:\n    enabled: true\n')

        load_config(config_file)

        written = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert written["download_sites"][0] == {"fitgirl": {"enabled": True}}, "no injected `name` key on disk"

    def test_the_returned_config_carries_the_version_it_wrote(self, tmp_path: Path) -> None:
        """The model must agree with the file the same load just wrote."""
        config_file = tmp_path / "gamarr.yml"
        config_file.write_text('general: {config_version: "1.58.0"}\n')

        cfg = load_config(config_file)

        written = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert cfg.general.config_version == str(written["general"]["config_version"])

    def test_a_validation_error_with_no_details_falls_back_to_its_text(self) -> None:
        """The summary helper must not crash on an error object with no entries."""
        from gamarr.config import _first_validation_error

        empty = ValidationError.from_exception_data("Config", [])
        assert "validation error" in _first_validation_error(empty)

    def test_an_invalid_floor_names_the_file_key_and_value(self, tmp_path: Path) -> None:
        """The operator must see which file and key to fix, not just a pydantic path."""
        from loguru import logger

        config_file = tmp_path / "gamarr.yml"
        config_file.write_text(
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n"
            "        discovery:\n          mode: backlog\n          backlog:\n            new:\n"
            "              oldest_month: 120\n"
        )
        captured: list[str] = []
        sink_id = logger.add(lambda msg: captured.append(str(msg)), level="ERROR", format="{message}")
        try:
            with pytest.raises(ValidationError):
                load_config(config_file)
        finally:
            logger.remove(sink_id)

        message = "\n".join(captured)
        assert str(config_file) in message, f"the file must be named: {message!r}"
        assert "backlog.new.oldest_month" in message, f"the key must be named: {message!r}"
        assert "120" in message, f"the offending value must be named: {message!r}"

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
                    "pc": {"min_criticscore": 75, "max_pages": 12},
                },
            },
        }
        result = _migrate_config(raw)
        assert result is True
        assert "metacritic" not in raw, "Old top-level metacritic key should be removed"
        assert "review_sites" in raw
        assert raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["min_criticscore"] == 75

    def test_migrate_metacritic_preserves_new_section_values(self) -> None:
        from gamarr.config import _migrate_config
        from gamarr.utils import current_year_month, pages_to_months, subtract_months

        raw: dict[str, Any] = {
            "metacritic": {"platform_overrides": {"pc": {"max_pages": 100}}},
            "review_sites": {
                "metacritic": {"platform_overrides": {"pc": {"max_pages": 240}}},
            },
        }

        assert _migrate_config(raw) is True
        # The new section's 240 pages win over the legacy 100 pages.
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["discovery"]["backlog"]["new"]["oldest_month"] == subtract_months(
            current_year_month(), pages_to_months(240)
        )
        assert "max_pages" not in pc

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
            "sources": {"fitgirl": {"new_key": "old_val", "shared": "old_val"}},
            "download_sites": {"fitgirl": {"existing_key": "existing_val", "shared": "new_val"}},
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
        assert fg["shared"] == "new_val"

    def test_migrate_config_returns_false_on_no_change(self) -> None:
        """_migrate_config should return True when migrating to keyed-list format."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "download_sites": [
                {"name": "fitgirl", "reject_keywords": ["hv"]},
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

    def test_migrate_legacy_fitgirl_keys_in_keyed_list(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "download_sites": [
                {
                    "fitgirl": {
                        "exclude_keywords": ["DLC"],
                        "cache_ttl_hours": 12,
                        "pending_days": 30,
                    }
                }
            ]
        }

        assert _migrate_config(raw) is True
        fitgirl = raw["download_sites"][0]["fitgirl"]
        assert fitgirl["reject_keywords"] == ["DLC"]
        assert fitgirl["cache_hours"] == 12
        assert fitgirl["source_matching_days"] == 30
        assert "exclude_keywords" not in fitgirl
        assert "cache_ttl_hours" not in fitgirl
        assert "pending_days" not in fitgirl

    def test_migrate_recheck_days_in_keyed_list(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "download_sites": [{"fitgirl": {"recheck_days": 45}}],
        }

        assert _migrate_config(raw) is True
        fitgirl = raw["download_sites"][0]["fitgirl"]
        assert fitgirl["source_matching_days"] == 45
        assert "recheck_days" not in fitgirl

    def test_migrate_days_since_release_removes_field(self) -> None:
        """Old days_since_release in metacritic.platform_overrides is removed."""

        from gamarr.config import _migrate_config
        from gamarr.utils import current_year_month, pages_to_months, subtract_months

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"days_since_release": 90, "cutoff_weeks": 240},
                },
            },
        }
        result = _migrate_config(raw)
        assert result is True
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "days_since_release" not in pc
        assert "cutoff_weeks" not in pc, "cutoff_weeks was renamed to max_pages"
        # cutoff_weeks (240 pages) wins over days_since_release (90 days).
        assert pc["discovery"]["backlog"]["new"]["oldest_month"] == subtract_months(
            current_year_month(), pages_to_months(240)
        )

    def test_cutoff_weeks_does_not_overwrite_existing_max_pages(self) -> None:
        from gamarr.config import _migrate_cutoff_weeks_to_max_pages

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {"pc": {"cutoff_weeks": 12, "max_pages": 99}},
                },
            },
        }

        assert _migrate_cutoff_weeks_to_max_pages(raw) is True
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["max_pages"] == 99
        assert "cutoff_weeks" not in pc

    def test_migrate_days_since_release_converts_to_a_month_floor(self) -> None:
        """days_since_release without max_pages should be converted."""

        from gamarr.config import _migrate_config
        from gamarr.utils import current_year_month, subtract_months

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
        assert "cutoff_weeks" not in pc, "cutoff_weeks was renamed to max_pages"
        # 120 days -> 4 months of floor, converted directly (no page intermediate)
        assert pc["discovery"]["backlog"]["new"]["oldest_month"] == subtract_months(current_year_month(), 4)

    def test_migrate_pending_days_to_max_queue_days(self) -> None:
        """Old pending_days key is renamed to max_queue_days in metacritic and fitgirl sections."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"pending_days": 45, "cutoff_weeks": 12},
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
        assert "max_queue_days" not in pc, "the platform fallback is consumed"
        assert "source_matching_days" not in raw, "a source sets its own window, so the fallback is unreachable"
        # After migration, entries are in keyed format: {key: {...}}
        fg_entry = next(e for e in raw["download_sites"] if isinstance(e, dict) and "fitgirl" in e)
        fg = fg_entry["fitgirl"]
        assert "pending_days" not in fg
        assert "recheck_days" not in fg, "recheck_days was renamed to max_queue_days"
        assert "source_matching_days" not in fg, "the old default 60 is dropped so it inherits the top level"

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
        assert "max_queue_days" not in raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "source_matching_days" not in raw, "a source sets its own window, so the fallback is unreachable"
        # After migration, entries are in keyed format: {key: {...}}
        fg_entry = next(e for e in raw["download_sites"] if isinstance(e, dict) and "fitgirl" in e)
        fg = fg_entry["fitgirl"]
        assert "recheck_days" not in fg
        assert fg["source_matching_days"] == 90, "a non-default legacy value is kept"

    def test_migrate_recheck_days_to_max_queue_days(self) -> None:
        """Old recheck_days keys are renamed to max_queue_days."""

        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"recheck_days": 45, "max_pages": 12},
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
        assert "max_queue_days" not in pc, "the platform fallback is consumed"
        assert "source_matching_days" not in raw, "a source sets its own window, so the fallback is unreachable"
        # After migration, entries are in keyed format: {key: {...}}
        fg_entry = next(e for e in raw["download_sites"] if isinstance(e, dict) and "fitgirl" in e)
        fg = fg_entry["fitgirl"]
        assert "recheck_days" not in fg, "recheck_days was renamed to max_queue_days"
        assert "source_matching_days" not in fg, "the old default 60 is dropped so it inherits the top level"

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

    def test_empty_config_file_is_populated_with_defaults(self, tmp_path: Path) -> None:
        config_file = tmp_path / "gamarr.yml"
        config_file.write_text("")

        load_config(config_file)

        rewritten = yaml.safe_load(config_file.read_text())
        assert rewritten["general"]["config_version"]
        assert "post_process" in rewritten

    def test_atomic_rewrite_preserves_original_on_replace_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        config_file = tmp_path / "gamarr.yml"
        config_file.write_text("general:\n  daemon_mode: foreground\n")
        original = config_file.read_text()

        def fail_replace(_source: str | Path, _destination: str | Path) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", fail_replace)

        cfg = load_config(config_file)

        assert cfg.general.daemon_mode == "foreground"
        assert config_file.read_text() == original

    def test_atomic_rewrite_preserves_file_mode(self, tmp_path: Path) -> None:
        import stat

        config_file = tmp_path / "gamarr.yml"
        config_file.write_text("general:\n  daemon_mode: foreground\n")
        config_file.chmod(0o640)

        load_config(config_file)

        assert stat.S_IMODE(config_file.stat().st_mode) == 0o640

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
        "    reject_keywords: null\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(config_text)
        f.flush()
        try:
            cfg = load_config(f.name)
            fg = next(e for e in cfg.download_sites if e.name == "fitgirl")
            assert fg.reject_keywords == [], "None should be stripped from reject_keywords"
        finally:
            os.unlink(f.name)


def test_rename_config_key_identical_keys() -> None:
    """_rename_config_key no-ops when old_key == new_key."""
    from gamarr.config import _rename_config_key

    mc_pc = {"cache_hours": 6}
    result = _rename_config_key(mc_pc, "cache_hours", "cache_hours", "pc")
    assert result is False
    assert mc_pc["cache_hours"] == 6  # Not deleted


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


def test_legacy_source_lookup_is_case_insensitive() -> None:
    """Legacy source mapping keys must match canonical names case-insensitively."""
    from gamarr.config import _source_config_dicts

    fitgirl = {"enabled": True}
    raw: dict[str, Any] = {"sources": {"FitGirl": fitgirl}}

    assert _source_config_dicts(raw, "sources", "fitgirl") == [fitgirl]


def test_legacy_source_lookup_prefers_valid_exact_key() -> None:
    """A case variant with an invalid value must not shadow the exact key."""
    from gamarr.config import _source_config_dicts

    fitgirl = {"enabled": True}
    raw: dict[str, Any] = {"sources": {"FitGirl": None, "fitgirl": fitgirl}}

    assert _source_config_dicts(raw, "sources", "fitgirl") == [fitgirl]


def test_parse_keyed_list() -> None:
    """DownloadSitesConfig parses [{'fitgirl': {'enabled': True}}] correctly."""
    from gamarr.config import DownloadSitesConfig

    raw: list[dict[str, Any]] = [
        {"fitgirl": {"enabled": True, "platform": "pc"}},
    ]
    cfg = DownloadSitesConfig(root=raw)  # type: ignore[arg-type]
    assert len(cfg) == 1
    assert cfg[0].name == "fitgirl"
    assert cfg[0].enabled is True
    # feed_url is no longer a field — stripped during parsing
    assert "feed_url" not in cfg[0].model_dump()


def test_parse_keyed_list_legacy() -> None:
    """DownloadSitesConfig still handles old [{name: ..., rss_url: ...}] format."""
    from gamarr.config import DownloadSitesConfig

    raw: list[dict[str, Any]] = [
        {"name": "fitgirl", "rss_url": "https://example.com/feed"},
    ]
    cfg = DownloadSitesConfig(root=raw)  # type: ignore[arg-type]
    assert len(cfg) == 1
    assert cfg[0].name == "fitgirl"
    # feed_url is no longer a field — rss_url is ignored during parsing
    assert "feed_url" not in cfg[0].model_dump()
    assert "rss_url" not in cfg[0].model_dump()


def test_name_excluded_from_dump() -> None:
    """SourceConfigEntry.name is excluded from dict serialization."""
    from gamarr.config import SourceConfigEntry

    entry = SourceConfigEntry(name="fitgirl")
    data = entry.model_dump()
    assert "name" not in data
    assert "enabled" in data


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
            # feed_url is stripped by _migrate_remove_fitgirl_feed_url
            assert "feed_url" not in inner
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
        {"fitgirl": "bare_string"},
    ]
    cfg = DownloadSitesConfig(root=raw)  # type: ignore[arg-type]
    assert len(cfg) == 2
    assert cfg[0].name == "fitgirl"
    assert cfg[1].name == "fitgirl"


class TestMigrateRemoveDodi:
    """Tests for _migrate_remove_dodi."""

    @staticmethod
    def _make_fitgirl_entry() -> dict[str, Any]:
        return {"fitgirl": {"feed_url": "https://fitgirl-repacks.site/feed/"}}

    def test_removes_keyed_dodi_entry(self) -> None:
        """A keyed-format DODI entry {"dodi": {...}} should be removed."""
        from gamarr.config import _migrate_remove_dodi

        raw: dict[str, Any] = {
            "download_sites": [
                self._make_fitgirl_entry(),
                {"dodi": {"feed_url": "https://1337x.to/user/DODI/"}},
            ]
        }
        result = _migrate_remove_dodi(raw)
        assert result is True
        assert len(raw["download_sites"]) == 1
        assert "fitgirl" in raw["download_sites"][0]

    def test_removes_casefold_keyed_dodi(self) -> None:
        """A keyed-format DODI entry with uppercase key should be removed."""
        from gamarr.config import _migrate_remove_dodi

        raw: dict[str, Any] = {
            "download_sites": [
                self._make_fitgirl_entry(),
                {"DODI": {"feed_url": "https://1337x.to/user/DODI/"}},
            ]
        }
        result = _migrate_remove_dodi(raw)
        assert result is True
        assert len(raw["download_sites"]) == 1

    def test_removes_flat_dodi_entry(self) -> None:
        """A flat-format DODI entry with name="dodi" should be removed."""
        from gamarr.config import _migrate_remove_dodi

        raw: dict[str, Any] = {
            "download_sites": [
                self._make_fitgirl_entry(),
                {"name": "dodi", "feed_url": "https://1337x.to/user/DODI/"},
            ]
        }
        result = _migrate_remove_dodi(raw)
        assert result is True
        assert len(raw["download_sites"]) == 1

    def test_removes_multiple_dodi_entries(self) -> None:
        """Multiple DODI entries should all be removed."""
        from gamarr.config import _migrate_remove_dodi

        raw: dict[str, Any] = {
            "download_sites": [
                self._make_fitgirl_entry(),
                {"dodi": {"feed_url": "https://1337x.to/user/DODI/"}},
                {"name": "dodi", "feed_url": "https://hydralinks.cloud/sources/dodi.json"},
            ]
        }
        result = _migrate_remove_dodi(raw)
        assert result is True
        assert len(raw["download_sites"]) == 1

    def test_no_dodi_returns_false(self) -> None:
        """When no DODI entry exists, should return False."""
        from gamarr.config import _migrate_remove_dodi

        raw: dict[str, Any] = {
            "download_sites": [
                self._make_fitgirl_entry(),
            ]
        }
        result = _migrate_remove_dodi(raw)
        assert result is False
        assert len(raw["download_sites"]) == 1

    def test_empty_download_sites_returns_false(self) -> None:
        """Empty download_sites list should return False."""
        from gamarr.config import _migrate_remove_dodi

        raw: dict[str, Any] = {"download_sites": []}
        result = _migrate_remove_dodi(raw)
        assert result is False

    def test_not_a_list_returns_false(self) -> None:
        """download_sites that is not a list should return False."""
        from gamarr.config import _migrate_remove_dodi

        raw: dict[str, Any] = {"download_sites": "not_a_list"}
        result = _migrate_remove_dodi(raw)
        assert result is False

    def test_skips_non_dict_entry(self) -> None:
        """Non-dict entries in download_sites should be skipped gracefully."""
        from gamarr.config import _migrate_remove_dodi

        raw: dict[str, Any] = {
            "download_sites": [
                "string_entry",
                {"dodi": {"feed_url": "https://1337x.to/user/DODI/"}},
            ]
        }
        result = _migrate_remove_dodi(raw)
        assert result is True
        assert len(raw["download_sites"]) == 1
        assert raw["download_sites"][0] == "string_entry"


# --- feed_url removal from fitgirl (consistency with freegog) ---


def test_migrate_removes_fitgirl_feed_url() -> None:
    """_migrate_config strips feed_url from fitgirl entries in download_sites."""
    from unittest.mock import patch

    from gamarr.config import _migrate_config

    raw: dict[str, Any] = {
        "download_sites": [
            {"fitgirl": {"enabled": True, "feed_url": "https://fitgirl-repacks.site/feed/", "platform": "pc"}},
            {"freegog": {"enabled": True, "platform": "pc"}},
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
    fitgirl_entry = ds[0]["fitgirl"]
    assert "feed_url" not in fitgirl_entry, "feed_url should be stripped from fitgirl entry"


def test_source_config_entry_no_feed_url_field() -> None:
    """SourceConfigEntry should not accept or store feed_url."""
    from gamarr.config import SourceConfigEntry

    entry = SourceConfigEntry(name="fitgirl")
    assert not hasattr(entry, "feed_url"), "feed_url should not exist on SourceConfigEntry"

    data = entry.model_dump()
    assert "feed_url" not in data, "feed_url should not appear in serialized output"


def test_download_sites_default_has_no_feed_url() -> None:
    """Default DownloadSitesConfig must not include feed_url for any source."""
    from gamarr.config import DownloadSitesConfig

    cfg = DownloadSitesConfig()
    for entry in cfg:
        data = entry.model_dump()
        assert "feed_url" not in data, f"{entry.name} should not have feed_url in output"


def test_parse_keyed_list_strips_feed_url() -> None:
    """_parse_keyed_list strips feed_url from keyed entries during parsing."""
    from gamarr.config import DownloadSitesConfig

    raw: list[dict[str, Any]] = [
        {"fitgirl": {"enabled": True, "feed_url": "https://example.com/feed", "platform": "pc"}},
    ]
    cfg = DownloadSitesConfig(root=raw)  # type: ignore[arg-type]
    assert len(cfg) == 1
    data = cfg[0].model_dump()
    assert "feed_url" not in data, "feed_url should be stripped during parsing"


# --- schedule flattening: remove acquisition wrapper ---


def test_schedule_config_no_acquisition_field() -> None:
    """ScheduleConfig must not have an 'acquisition' sub-field."""
    from gamarr.config import ScheduleConfig

    cfg = ScheduleConfig()
    assert not hasattr(cfg, "acquisition"), "ScheduleConfig should not have acquisition"
    assert cfg.enabled is False
    assert cfg.schedule_time_mins == 60
    assert cfg.run_on_start is True


def test_migrate_flattens_schedule_acquisition() -> None:
    """_migrate_config flattens schedule.acquisition.* into schedule.*."""
    from unittest.mock import patch

    from gamarr.config import _migrate_config

    raw: dict[str, Any] = {
        "schedule": {
            "acquisition": {
                "enabled": True,
                "schedule_time_mins": 30,
                "run_on_start": False,
            }
        },
        "review_sites": {"metacritic": {"platform_overrides": {"pc": {}}}},
        "torrent_client": {
            "qbittorrent": {"host": "localhost", "port": 8080, "username": "admin", "password": "adminadmin"}
        },
    }
    with patch("gamarr.config.logger"):
        result = _migrate_config(raw)

    assert result is True
    schedule = raw["schedule"]
    assert "acquisition" not in schedule, "acquisition sub-key should be removed"
    assert schedule["enabled"] is True
    assert schedule["schedule_time_mins"] == 30
    assert schedule["run_on_start"] is False


def test_migrate_daemon_mode_writes_to_flat_schedule() -> None:
    """_migrate_daemon_mode writes to schedule.enabled, not schedule.acquisition.enabled."""
    from unittest.mock import patch

    from gamarr.config import _migrate_daemon_mode

    raw: dict[str, Any] = {
        "general": {"daemon_mode": "background"},
    }
    with patch("gamarr.config.logger"):
        result = _migrate_daemon_mode(raw)

    assert result is True
    schedule = raw["schedule"]
    assert "acquisition" not in schedule, "no acquisition sub-key should be created"
    assert schedule["enabled"] is True


def test_migrate_remove_fitgirl_feed_url_strips_rss_url() -> None:
    """_migrate_remove_fitgirl_feed_url also strips legacy rss_url keys."""
    from gamarr.config import _migrate_remove_fitgirl_feed_url

    raw: dict[str, Any] = {
        "download_sites": [
            {"fitgirl": {"enabled": True, "rss_url": "https://old-feed.com/feed/"}},
        ],
    }
    result = _migrate_remove_fitgirl_feed_url(raw)
    assert result is True
    inner = raw["download_sites"][0]["fitgirl"]
    assert "rss_url" not in inner
    assert "feed_url" not in inner


class TestSortOrder:
    """discovery.backlog.sort_order selects score-based Metacritic browsing."""

    def test_sort_order_defaults_to_new(self) -> None:
        """The backlog block must default to "new" (newest-first) for valid
        Metacritic browse URLs. Score-based sorts (criticscore/userscore)
        browse all-time and are filtered by the discovery window."""
        from gamarr.config import BacklogDiscoveryConfig

        assert BacklogDiscoveryConfig().sort_order == "new"

    def test_sort_order_accepts_three_values(self) -> None:
        from gamarr.config import BacklogDiscoveryConfig

        assert BacklogDiscoveryConfig(sort_order="new").sort_order == "new"
        assert BacklogDiscoveryConfig(sort_order="criticscore").sort_order == "criticscore"
        assert BacklogDiscoveryConfig(sort_order="userscore").sort_order == "userscore"

    def test_sort_order_rejects_metascore_and_unknown(self) -> None:
        import pydantic
        import pytest

        from gamarr.config import BacklogDiscoveryConfig

        with pytest.raises(pydantic.ValidationError):
            BacklogDiscoveryConfig(sort_order="metascore")  # type: ignore[arg-type]
        with pytest.raises(pydantic.ValidationError):
            BacklogDiscoveryConfig(sort_order="bogus")  # type: ignore[arg-type]


def test_drop_migrated_deprecated_keys_no_deprecated() -> None:
    """_drop_migrated_deprecated_keys returns False when no deprecated keys present."""
    from gamarr.config import _drop_migrated_deprecated_keys

    mc_pc: dict = {"enabled": True, "sort_order": "new"}
    changed = _drop_migrated_deprecated_keys(mc_pc, "pc")
    assert changed is False
    assert "enabled" in mc_pc
    assert "sort_order" in mc_pc


def test_drop_migrated_deprecated_keys_browse_pages() -> None:
    """_drop_migrated_deprecated_keys removes browse_max_pages and max_score_checks."""
    from gamarr.config import _drop_migrated_deprecated_keys

    mc_pc: dict = {"browse_max_pages": 10, "max_score_checks": 5, "enabled": True}
    changed = _drop_migrated_deprecated_keys(mc_pc, "pc")
    assert changed is True
    assert "browse_max_pages" not in mc_pc
    assert "max_score_checks" not in mc_pc
    assert "enabled" in mc_pc


def test_drop_migrated_deprecated_keys_cutoff_dates() -> None:
    """_drop_migrated_deprecated_keys removes cutoff/date related keys."""
    from gamarr.config import _drop_migrated_deprecated_keys

    mc_pc: dict = {"cutoff_date": "2024-01-01", "browse_cutoff_date": "2024-01-01", "enabled": True}
    changed = _drop_migrated_deprecated_keys(mc_pc, "pc")
    assert changed is True
    assert "cutoff_date" not in mc_pc
    assert "browse_cutoff_date" not in mc_pc
    assert "enabled" in mc_pc


def test_drop_migrated_deprecated_keys_age_recheck() -> None:
    """_drop_migrated_deprecated_keys removes age_recheck_weeks and max_verify_attempts."""
    from gamarr.config import _drop_migrated_deprecated_keys

    mc_pc: dict = {"age_recheck_weeks": 52, "max_verify_attempts": 3, "enabled": True}
    changed = _drop_migrated_deprecated_keys(mc_pc, "pc")
    assert changed is True
    assert "age_recheck_weeks" not in mc_pc
    assert "max_verify_attempts" not in mc_pc
    assert "enabled" in mc_pc


def test_migrate_config_adds_post_process_path_case() -> None:
    """_migrate_config should add path_case field to post_process when missing."""
    from gamarr.config import _migrate_config

    raw: dict[str, Any] = {
        "post_process": {"post_process_enabled": True},
    }
    result = _migrate_config(raw)
    assert result is True, "Migration should return True when adding path_case"
    assert "path_case" in raw["post_process"], "path_case should be added"
    assert raw["post_process"]["path_case"] == "pretty", "path_case should default to 'pretty'"


def test_migration_removes_search_mode() -> None:
    """A config containing search_mode should load and the field should be stripped from the model."""
    from gamarr.config import _migrate_config

    raw: dict[str, Any] = {
        "general": {"config_version": "1.0.0"},
        "review_sites": {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"search_mode": "backlog", "min_criticscore": 75},
                }
            }
        },
    }
    _migrate_config(raw)
    pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
    assert "search_mode" not in pc, "search_mode should be stripped from the raw dict by migration"

    # Also verify the parsed model has no search_mode attribute

    # Use load_config which merges with defaults for full-path validation
    import os
    import tempfile

    from gamarr.config import load_config

    config_text = (
        "general:\n"
        "  config_version: '1.0.0'\n"
        "review_sites:\n"
        "  metacritic:\n"
        "    platform_overrides:\n"
        "      pc:\n"
        "        search_mode: backlog\n"
        "        min_criticscore: 75\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(config_text)
        f.flush()
        try:
            cfg = load_config(f.name)
            pc_cfg = cfg.review_sites.metacritic.platform_overrides["pc"]
            assert not hasattr(pc_cfg, "search_mode"), "search_mode should not exist on the parsed config model"
        finally:
            os.unlink(f.name)


class TestScheduleAcquisitionTimeout:
    """schedule.acquisition_timeout_mins watchdog default."""

    def test_default_is_25(self) -> None:
        from gamarr.config import ScheduleConfig

        cfg = ScheduleConfig()
        assert cfg.acquisition_timeout_mins == 25

    def test_positive_only(self) -> None:
        import pydantic

        from gamarr.config import ScheduleConfig

        with pytest.raises(pydantic.ValidationError):
            ScheduleConfig(acquisition_timeout_mins=0)


class TestCriticThresholdRename:
    """Renamed critic-threshold fields on MetacriticPlatformConfig."""

    def test_defaults(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert cfg.min_criticscore == 75
        assert cfg.min_criticscore_reviews == 10

    def test_old_names_are_ignored_by_model(self) -> None:
        # pydantic ignores unknown fields; the migration rewrites the YAML
        # before validation, so a legacy key never influences the model.
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig(min_metascore=99)  # type: ignore[call-arg]
        assert cfg.min_criticscore == 75, "the legacy key must have no effect on the model"
        assert "min_metascore" not in cfg.model_dump()


class TestSortOrderCriticMigration:
    """One-time in-place migration of legacy metascore naming."""

    def _write_config(self, tmp_path: Path, content: str) -> Path:
        path = tmp_path / "gamarr.yml"
        path.write_text(content)
        return path

    def test_migrates_legacy_names_and_rewrites_file(self, tmp_path: Path) -> None:
        from gamarr.config import load_config

        path = self._write_config(
            tmp_path,
            "review_sites:\n"
            "  metacritic:\n"
            "    platform_overrides:\n"
            "      pc:\n"
            "        min_metascore: 80\n"
            "        min_metascore_reviews: 15\n"
            "        sort_order: metascore\n",
        )
        cfg = load_config(path)
        mc = cfg.review_sites.metacritic.platform_overrides["pc"]
        assert mc.discovery.backlog.sort_order == "criticscore"
        assert mc.min_criticscore == 80
        assert mc.min_criticscore_reviews == 15

        rewritten = path.read_text()
        assert "min_criticscore" in rewritten
        assert "min_metascore" not in rewritten
        assert "sort_order: criticscore" in rewritten

    def test_second_load_does_not_rewrite(self, tmp_path: Path) -> None:
        from gamarr.config import load_config

        path = self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n        sort_order: metascore\n",
        )
        load_config(path)
        first = path.read_text()
        load_config(path)
        assert path.read_text() == first

    def test_keeps_existing_new_value_on_collision(self, tmp_path: Path) -> None:
        from gamarr.config import load_config

        path = self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n"
            "        min_metascore: 70\n        min_criticscore: 90\n",
        )
        cfg = load_config(path)
        assert cfg.review_sites.metacritic.platform_overrides["pc"].min_criticscore == 90

    def test_userscore_value_passes_through_untouched(self, tmp_path: Path) -> None:
        from gamarr.config import load_config

        path = self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n        sort_order: userscore\n",
        )
        cfg = load_config(path)
        assert cfg.review_sites.metacritic.platform_overrides["pc"].discovery.backlog.sort_order == "userscore"

    def test_legacy_top_level_metacritic_layout_migrates(self, tmp_path: Path) -> None:
        """A legacy top-level metacritic: block must still migrate the rename."""
        from gamarr.config import load_config

        path = tmp_path / "gamarr.yml"
        path.write_text(
            "metacritic:\n  platform_overrides:\n    pc:\n      sort_order: metascore\n      min_metascore: 80\n"
        )
        cfg = load_config(path)
        mc = cfg.review_sites.metacritic.platform_overrides["pc"]
        assert mc.discovery.backlog.sort_order == "criticscore"
        assert mc.min_criticscore == 80

    def test_read_only_config_does_not_abort_startup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A read-only gamarr.yml must not abort startup; the migrated
        in-memory config is used and the rewrite retries next load."""
        from pathlib import Path as PathCls

        from loguru import logger

        from gamarr.config import load_config

        path = tmp_path / "gamarr.yml"
        path.write_text(
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n        sort_order: metascore\n"
        )
        original = path.read_text()

        real_open = PathCls.open

        def guarded_open(self: PathCls, *args: object, **kwargs: object) -> Any:
            mode = kwargs.get("mode") if kwargs.get("mode") is not None else (args[0] if args else "r")
            if mode == "w":
                raise PermissionError("read-only filesystem")
            from typing import cast

            return cast("Any", real_open)(self, *args, **kwargs)

        monkeypatch.setattr(PathCls, "open", guarded_open)

        captured: list[str] = []
        sink_id = logger.add(lambda msg: captured.append(str(msg)), level="INFO", format="{message}")
        try:
            cfg = load_config(path)
        finally:
            logger.remove(sink_id)

        mc = cfg.review_sites.metacritic.platform_overrides["pc"]
        assert mc.discovery.backlog.sort_order == "criticscore", (
            "migrated in-memory config must load despite read-only file"
        )
        assert path.read_text() == original, "a failed rewrite must leave the file untouched"
        assert any("Could not rewrite config file" in m for m in captured), captured

    def test_multiple_platform_overrides_migrate_independently(self, tmp_path: Path) -> None:
        from gamarr.config import load_config

        path = tmp_path / "gamarr.yml"
        path.write_text(
            "review_sites:\n  metacritic:\n    platform_overrides:\n"
            "      pc:\n        sort_order: metascore\n        min_metascore: 80\n"
            "      ps5:\n        sort_order: userscore\n        min_metascore_reviews: 15\n"
        )
        cfg = load_config(path)
        pc = cfg.review_sites.metacritic.platform_overrides["pc"]
        ps5 = cfg.review_sites.metacritic.platform_overrides["ps5"]
        assert pc.discovery.backlog.sort_order == "criticscore"
        assert pc.min_criticscore == 80
        assert ps5.discovery.backlog.sort_order == "userscore", "userscore must pass through untouched"
        assert ps5.min_criticscore_reviews == 15

    def test_rewrite_retries_on_next_startup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """After a read-only failure, the next writable load persists the migration."""
        from pathlib import Path as PathCls
        from typing import cast

        from gamarr.config import load_config

        path = tmp_path / "gamarr.yml"
        path.write_text(
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n        sort_order: metascore\n"
        )

        real_open = PathCls.open

        def read_only_open(self: PathCls, *args: object, **kwargs: object) -> Any:
            mode = kwargs.get("mode") if kwargs.get("mode") is not None else (args[0] if args else "r")
            if mode == "w":
                raise PermissionError("read-only filesystem")
            return cast("Any", real_open)(self, *args, **kwargs)

        monkeypatch.setattr(PathCls, "open", read_only_open)
        load_config(path)  # write fails; in-memory config still works
        assert "metascore" in path.read_text(), "the failed write must leave the legacy value in place"

        monkeypatch.undo()  # filesystem becomes writable again
        cfg = load_config(path)
        assert cfg.review_sites.metacritic.platform_overrides["pc"].discovery.backlog.sort_order == "criticscore"
        rewritten = path.read_text()
        assert "min_metascore" not in rewritten
        assert "sort_order: criticscore" in rewritten, "the second load must persist the migration"


class TestDiscoveryWindowConfig:
    """The discovery floor and pace keys (oldest_month / months_per_cycle)."""

    def test_defaults_are_no_floor_and_one_month_of_pace(self) -> None:
        """The latest block defaults to no floor and one month of pace."""
        from gamarr.config import LatestDiscoveryConfig

        latest = LatestDiscoveryConfig()
        assert latest.oldest_month == 0
        assert latest.months_per_cycle == 1

    @pytest.mark.parametrize("value", [0, 195801, 202401])
    def test_accepts_zero_and_valid_year_month(self, value: int) -> None:
        """0 disables the floor; a real YYYYMM month is accepted."""
        from gamarr.config import LatestDiscoveryConfig

        assert LatestDiscoveryConfig(oldest_month=value).oldest_month == value

    @pytest.mark.parametrize("value", [2024, 202413, 202400, -202401, 999, 100801, 195701])
    def test_rejects_invalid_values(self, value: int) -> None:
        """Anything that is not 0 or a valid YYYYMM month is a config error."""
        from gamarr.config import LatestDiscoveryConfig

        with pytest.raises(ValidationError) as excinfo:
            LatestDiscoveryConfig(oldest_month=value)
        assert "oldest_month must be 0 (no floor) or YYYYMM with month 01-12" in str(excinfo.value)

    def test_rejects_dashed_string(self) -> None:
        """A dashed date string is not an integer and must fail validation."""
        from gamarr.config import LatestDiscoveryConfig

        with pytest.raises(ValidationError):
            LatestDiscoveryConfig(oldest_month="2024-01")  # type: ignore[arg-type]

    def test_future_floor_clamps_to_current_month(self) -> None:
        """A future floor would sweep nothing, so it clamps with a warning."""
        import datetime

        from loguru import logger as loguru_logger

        from gamarr.config import LatestDiscoveryConfig

        now = datetime.datetime.now(tz=datetime.UTC)
        future = (now.year + 2) * 100 + now.month
        captured: list[str] = []
        sink_id = loguru_logger.add(lambda msg: captured.append(str(msg)), level="WARNING", format="{message}")
        try:
            latest = LatestDiscoveryConfig(oldest_month=future)
        finally:
            loguru_logger.remove(sink_id)
        assert latest.oldest_month == now.year * 100 + now.month
        assert any("clamping to" in line for line in captured), captured

    def test_months_per_cycle_rejects_negative(self) -> None:
        """months_per_cycle is never negative."""
        from gamarr.config import LatestDiscoveryConfig

        with pytest.raises(ValidationError):
            LatestDiscoveryConfig(months_per_cycle=-1)

    def test_platform_override_carries_the_window(self) -> None:
        """The window works inside review_sites.metacritic.platform_overrides."""
        from gamarr.config import Config

        cfg = Config(
            review_sites={
                "metacritic": {
                    "platform_overrides": {
                        "pc": {"discovery": {"latest": {"oldest_month": 202401, "months_per_cycle": 3}}},
                    },
                },
            },  # type: ignore[arg-type]
        )
        latest = cfg.review_sites.metacritic.platform_overrides["pc"].discovery.latest
        assert latest.oldest_month == 202401
        assert latest.months_per_cycle == 3


class TestCacheHoursRename:
    """cache_pages_hours is renamed to cache_hours everywhere it appears."""

    def test_migrates_metacritic_override(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {"pc": {"cache_pages_hours": 12}},
                },
            },
        }
        assert _migrate_config(raw) is True
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["cache_hours"] == 12
        assert "cache_pages_hours" not in pc

    def test_migrates_every_download_site(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "download_sites": [
                {"fitgirl": {"cache_pages_hours": 3}},
                {"freegog": {"cache_pages_hours": 9}},
            ],
        }
        assert _migrate_config(raw) is True
        entries = {list(e.keys())[0]: e[list(e.keys())[0]] for e in raw["download_sites"]}
        assert entries["fitgirl"]["cache_hours"] == 3
        assert entries["freegog"]["cache_hours"] == 9
        assert "cache_pages_hours" not in entries["fitgirl"]
        assert "cache_pages_hours" not in entries["freegog"]

    def test_existing_cache_hours_wins(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {"pc": {"cache_pages_hours": 1, "cache_hours": 24}},
                },
            },
        }
        _migrate_config(raw)
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["cache_hours"] == 24
        assert "cache_pages_hours" not in pc

    def test_default_config_dict_uses_cache_hours(self) -> None:
        from gamarr.config import _default_config_dict

        defaults = _default_config_dict()
        pc = defaults["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["cache_hours"] == 6
        assert "cache_pages_hours" not in pc
        for entry in defaults["download_sites"]:
            name = list(entry.keys())[0]
            assert entry[name]["cache_hours"] == 6
            assert "cache_pages_hours" not in entry[name]

    def test_migrates_every_key_of_a_multi_source_entry(self) -> None:
        """A single entry carrying two sources must have both bodies renamed."""
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "download_sites": [
                {"fitgirl": {"cache_pages_hours": 3}, "freegog": {"cache_pages_hours": 9}},
            ],
        }
        _migrate_config(raw)
        entry = raw["download_sites"][0]
        assert entry["fitgirl"]["cache_hours"] == 3
        assert entry["freegog"]["cache_hours"] == 9
        assert "cache_pages_hours" not in entry["fitgirl"]
        assert "cache_pages_hours" not in entry["freegog"]

    def test_migration_runs_before_freegog_defaults_injection(self) -> None:
        """A sparse freegog entry keeps its legacy value, not the injected default.

        The cache rename must run before the freegog default injection; run in
        the other order, the injected ``cache_hours`` looks already-migrated and
        the user's legacy value is discarded.
        """
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {"download_sites": [{"freegog": {"cache_pages_hours": 9}}]}
        _migrate_config(raw)
        freegog = next(e["freegog"] for e in raw["download_sites"] if "freegog" in e)
        assert freegog["cache_hours"] == 9, "legacy value must survive the defaults injection"
        assert "cache_pages_hours" not in freegog

    def test_freegog_injection_uses_new_key(self) -> None:
        """A config missing freegog gets the new key, not the legacy one."""
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {"download_sites": [{"fitgirl": {"enabled": True}}]}
        _migrate_config(raw)
        freegog = next(e["freegog"] for e in raw["download_sites"] if "freegog" in e)
        assert freegog["cache_hours"] == 6
        assert "cache_pages_hours" not in freegog


class TestDiscoveryModeConfig:
    """mode: latest | backlog."""

    def test_default_is_latest(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        assert MetacriticPlatformConfig().discovery.mode == "latest"

    def test_accepts_backlog(self) -> None:
        from gamarr.config import DiscoveryConfig, MetacriticPlatformConfig

        backlog = DiscoveryConfig(mode="backlog")
        assert MetacriticPlatformConfig(discovery=backlog).discovery.mode == "backlog"

    def test_rejects_unknown_mode(self) -> None:
        import pytest
        from pydantic import ValidationError

        from gamarr.config import DiscoveryConfig

        with pytest.raises(ValidationError):
            DiscoveryConfig(mode="newest")  # type: ignore[arg-type]


class TestDiscoveryConfig:
    """The per-mode discovery blocks (ADR 0001, 0003, 0005)."""

    def _defaults(self) -> Any:
        from gamarr.config import MetacriticPlatformConfig

        return MetacriticPlatformConfig().discovery

    def test_default_mode_is_latest_with_one_month_of_pace(self) -> None:
        discovery = self._defaults()
        assert discovery.mode == "latest"
        assert discovery.latest.oldest_month == 0
        assert discovery.latest.months_per_cycle == 1

    def test_backlog_defaults_keep_todays_shape(self) -> None:
        backlog = self._defaults().backlog
        assert backlog.sort_order == "new"
        assert backlog.new.oldest_month == 0
        assert backlog.new.months_per_cycle == 1, "default pace bounds an otherwise unbounded sweep"

    def test_backlog_score_reaches_by_month_and_paces_by_pages(self) -> None:
        score = self._defaults().backlog.score
        assert score.oldest_month == 0, "no floor by default: the sweep reaches the earliest listing"
        assert score.pages_per_cycle == 20, "the default workload of one cycle"

    def test_latest_has_no_page_keys(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import LatestDiscoveryConfig

        with pytest.raises(ValidationError):
            LatestDiscoveryConfig.model_validate({"page_depth": 500})

    def test_a_legacy_key_inside_a_block_is_an_error(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import BacklogDiscoveryConfig

        with pytest.raises(ValidationError):
            BacklogDiscoveryConfig.model_validate({"sort_order": "new", "new": {"max_date": 202001}})

    def test_oldest_month_is_clamped_when_in_the_future(self) -> None:
        from gamarr.config import LatestDiscoveryConfig
        from gamarr.utils import current_year_month, subtract_months

        future = current_year_month() + 100
        assert LatestDiscoveryConfig(oldest_month=future).oldest_month == current_year_month()
        assert LatestDiscoveryConfig(oldest_month=202001).oldest_month == 202001
        assert subtract_months(current_year_month(), 1) > 0

    def test_oldest_month_rejects_a_bad_month_and_an_early_year(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import LatestDiscoveryConfig

        with pytest.raises(ValidationError):
            LatestDiscoveryConfig(oldest_month=202013)
        with pytest.raises(ValidationError):
            LatestDiscoveryConfig(oldest_month=195701)

    def test_score_oldest_month_is_validated_like_the_other_blocks(self) -> None:
        """An unclamped future floor would make the year range empty and scan nothing."""
        from pydantic import ValidationError

        from gamarr.config import BacklogScoreConfig
        from gamarr.utils import current_year_month

        future = current_year_month() + 100
        assert BacklogScoreConfig(oldest_month=future).oldest_month == current_year_month()
        assert BacklogScoreConfig(oldest_month=202001).oldest_month == 202001
        with pytest.raises(ValidationError):
            BacklogScoreConfig(oldest_month=202013)
        with pytest.raises(ValidationError):
            BacklogScoreConfig(oldest_month=195701)

    def test_page_depth_is_rejected_once_migrated(self) -> None:
        """The key is gone from the model; a file that still has it is converted before validation."""
        from pydantic import ValidationError

        from gamarr.config import BacklogScoreConfig

        with pytest.raises(ValidationError):
            BacklogScoreConfig.model_validate({"page_depth": 500})

    def test_months_per_cycle_is_capped(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import LatestDiscoveryConfig

        assert LatestDiscoveryConfig(months_per_cycle=1200).months_per_cycle == 1200
        with pytest.raises(ValidationError):
            LatestDiscoveryConfig(months_per_cycle=1201)


class TestQueueConfig:
    """`pending_order` and `source_matching_days` (ADR 0004, 0006)."""

    def test_pending_order_defaults_to_new_at_metacritic_level(self) -> None:
        from gamarr.config import MetacriticConfig

        assert MetacriticConfig().pending_order == "new"

    def test_top_level_source_matching_days_defaults_to_60(self) -> None:
        from gamarr.config import Config

        assert Config().source_matching_days == 60

    def test_a_source_overrides_the_window(self) -> None:
        from gamarr.config import SourceConfigEntry

        entry = SourceConfigEntry(name="fitgirl", source_matching_days=90)
        assert entry.source_matching_days == 90
        assert SourceConfigEntry(name="fitgirl").source_matching_days is None, "unset inherits the top-level window"

    def test_pending_order_rejects_an_unknown_value(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import MetacriticConfig

        with pytest.raises(ValidationError):
            MetacriticConfig(pending_order="metascore")  # type: ignore[arg-type]


class TestDiscoveryBlockMigration:
    """Legacy flat keys move into the per-mode blocks (ADR 0001, 0007)."""

    def _migrate(self, platform: dict[str, Any], **metacritic: Any) -> dict[str, Any]:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {"review_sites": {"metacritic": {"platform_overrides": {"pc": platform}, **metacritic}}}
        _migrate_config(raw)
        return raw

    def test_latest_floor_and_pace_move_into_the_latest_block(self) -> None:
        raw = self._migrate({"mode": "latest", "max_date": 202601, "max_cycle_months": 1, "sort_order": "new"})
        discovery = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]
        assert discovery["mode"] == "latest"
        assert discovery["latest"] == {"oldest_month": 202601, "months_per_cycle": 1}
        assert "max_date" not in raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]

    def test_latest_page_depth_converts_to_a_month_pace(self) -> None:
        raw = self._migrate({"mode": "latest", "max_pages": 1000, "sort_order": "new"})
        latest = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["latest"]
        assert latest["months_per_cycle"] == 31

    def test_latest_drops_the_page_keys_it_never_read(self) -> None:
        raw = self._migrate({"mode": "latest", "max_date": 202601, "max_cycle_pages": 16, "max_pages": 1000})
        latest = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["latest"]
        assert latest == {"oldest_month": 202601, "months_per_cycle": 0}, "the old cycle read to its floor"
        assert "max_pages" not in raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]

    def test_backlog_new_page_budget_becomes_a_floor(self) -> None:
        from gamarr.utils import current_year_month, subtract_months

        raw = self._migrate({"mode": "backlog", "max_pages": 48, "sort_order": "new"})
        new_block = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["new"]
        assert new_block["oldest_month"] == subtract_months(current_year_month(), 2)

    def test_backlog_score_converts_the_page_budget_to_a_floor(self) -> None:
        from gamarr.utils import current_year_month, pages_to_months, subtract_months

        raw = self._migrate({"mode": "backlog", "max_pages": 40, "max_cycle_pages": 5, "sort_order": "criticscore"})
        score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
        assert score == {
            "oldest_month": subtract_months(current_year_month(), pages_to_months(40)),
            "pages_per_cycle": 5,
        }

    def test_a_block_shaped_page_depth_converts_to_a_floor(self) -> None:
        """The shape the current release writes must load and convert (ADR 0007)."""
        from gamarr.utils import current_year_month, pages_to_months, subtract_months

        raw = self._migrate(
            {
                "discovery": {
                    "mode": "backlog",
                    "backlog": {"sort_order": "criticscore", "score": {"page_depth": 500, "pages_per_cycle": 20}},
                }
            }
        )
        score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
        assert score == {
            "oldest_month": subtract_months(current_year_month(), pages_to_months(500)),
            "pages_per_cycle": 20,
        }

    def test_a_zero_page_depth_writes_no_floor(self) -> None:
        raw = self._migrate(
            {"discovery": {"mode": "backlog", "backlog": {"sort_order": "criticscore", "score": {"page_depth": 0}}}}
        )
        score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
        assert score["oldest_month"] == 0, "a legacy 0 budget means no budget, not a floor of now"
        assert "page_depth" not in score

    def test_an_explicit_zero_in_a_mixed_shape_beats_the_default_floor(self) -> None:
        """A stated 0 is a bound, so the flat-key default must not invent a floor over it."""
        raw = self._migrate(
            {
                "sort_order": "criticscore",
                "discovery": {
                    "mode": "backlog",
                    "backlog": {
                        "sort_order": "criticscore",
                        "score": {"page_depth": 0, "pages_per_cycle": 20},
                    },
                },
            }
        )
        score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
        assert score["oldest_month"] == 0
        assert score["pages_per_cycle"] == 20

    def test_a_flat_budget_beats_the_default_floor_in_a_mixed_shape(self) -> None:
        """A stated flat budget converts; the no-bound default must not pre-empt it."""
        from gamarr.utils import current_year_month, pages_to_months, subtract_months

        raw = self._migrate(
            {
                "max_pages": 1000,
                "sort_order": "criticscore",
                "discovery": {
                    "mode": "backlog",
                    "backlog": {"sort_order": "criticscore", "score": {"pages_per_cycle": 5}},
                },
            }
        )
        score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
        assert score["oldest_month"] == subtract_months(current_year_month(), pages_to_months(1000))
        assert score["pages_per_cycle"] == 5

    def test_a_score_block_stating_no_bound_keeps_the_old_default_reach(self) -> None:
        """A pre-floor block that omitted page_depth ran on the default budget; keep that reach."""
        from gamarr.config import _default_floor

        raw = self._migrate(
            {
                "discovery": {
                    "mode": "backlog",
                    "backlog": {"sort_order": "criticscore", "score": {"pages_per_cycle": 9}},
                }
            }
        )
        score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
        assert score["oldest_month"] == _default_floor()
        assert score["pages_per_cycle"] == 9

    def test_a_block_shaped_page_depth_beats_the_default_floor(self) -> None:
        """A released-shape budget must convert before the no-bound default is invented."""
        from gamarr.utils import current_year_month, pages_to_months, subtract_months

        raw = self._migrate(
            {
                "sort_order": "criticscore",
                "discovery": {
                    "mode": "backlog",
                    "backlog": {"sort_order": "criticscore", "score": {"page_depth": 100}},
                },
            }
        )
        score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
        assert score["oldest_month"] == subtract_months(current_year_month(), pages_to_months(100)), (
            "the operator's own budget must win over the invented default reach"
        )
        assert "page_depth" not in score

    def test_a_bound_free_score_config_keeps_the_old_default_reach(self) -> None:
        """An upgrade must not widen a score sweep from the default budget to the whole catalogue."""
        from gamarr.config import _default_floor

        raw = self._migrate({"mode": "backlog", "sort_order": "criticscore"})
        score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
        assert score["oldest_month"] == _default_floor()

    def test_an_explicit_zero_page_budget_stays_floorless(self) -> None:
        """An explicit 0 said "no budget", so the upgrade honours it rather than inventing a floor."""
        raw = self._migrate({"mode": "backlog", "max_pages": 0, "sort_order": "criticscore"})
        backlog = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]
        assert "oldest_month" not in backlog.get("score", {})

    def test_a_numeric_string_page_depth_is_coerced_not_fatal(self) -> None:
        """The released int field accepted '500', so the conversion must too (ADR 0007)."""
        from gamarr.utils import current_year_month, pages_to_months, subtract_months

        raw = self._migrate(
            {
                "discovery": {
                    "mode": "backlog",
                    "backlog": {"sort_order": "criticscore", "score": {"page_depth": "500"}},
                }
            }
        )
        score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
        assert score["oldest_month"] == subtract_months(current_year_month(), pages_to_months(500))
        assert "page_depth" not in score

    def test_an_unusable_page_depth_is_dropped_without_aborting_the_load(self) -> None:
        from gamarr.config import DiscoveryConfig, _default_floor

        for bad in ("lots", True, None):
            raw = self._migrate(
                {
                    "discovery": {
                        "mode": "backlog",
                        "backlog": {"sort_order": "criticscore", "score": {"page_depth": bad}},
                    }
                }
            )
            discovery = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]
            score = discovery["backlog"]["score"]
            assert score["oldest_month"] == _default_floor(), f"{bad!r} must fall back to the default reach"
            assert "page_depth" not in score
            DiscoveryConfig.model_validate(discovery)

    def test_an_explicit_floor_beats_a_legacy_page_depth(self) -> None:
        raw = self._migrate(
            {
                "discovery": {
                    "mode": "backlog",
                    "backlog": {
                        "sort_order": "criticscore",
                        "score": {"oldest_month": 201601, "page_depth": 500},
                    },
                }
            }
        )
        score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
        assert score["oldest_month"] == 201601
        assert "page_depth" not in score

    def test_sort_order_feeds_the_shared_pending_order(self) -> None:
        raw = self._migrate({"mode": "backlog", "sort_order": "userscore"})
        metacritic = raw["review_sites"]["metacritic"]
        assert metacritic["pending_order"] == "userscore"

    def test_a_latest_config_does_not_write_a_backlog_sort_order(self) -> None:
        raw = self._migrate({"mode": "latest", "sort_order": "new"})
        backlog = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"].get("backlog", {})
        assert "sort_order" not in backlog

    def test_an_existing_block_wins_over_the_legacy_key(self) -> None:
        raw = self._migrate({"max_date": 202001, "discovery": {"latest": {"oldest_month": 202501}}})
        latest = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["latest"]
        assert latest["oldest_month"] == 202501

    def test_a_migrated_file_loads_and_reloads_identically(self, tmp_path: Path) -> None:
        path = tmp_path / "gamarr.yml"
        path.write_text(
            "review_sites:\n"
            "  metacritic:\n"
            "    platform_overrides:\n"
            "      pc:\n"
            "        mode: latest\n"
            "        max_date: 202601\n"
            "        max_cycle_months: 1\n"
            "        max_pages: 1000\n"
            "        max_cycle_pages: 16\n"
            "        sort_order: new\n",
            encoding="utf-8",
        )
        first = load_config(path)
        second = load_config(path)
        assert first.review_sites.metacritic.platform_overrides["pc"].discovery.model_dump() == (
            second.review_sites.metacritic.platform_overrides["pc"].discovery.model_dump()
        )
        written = path.read_text(encoding="utf-8")
        assert "max_pages: 1000" not in written, "the migrated page budget must not stay on disk"
        assert "max_cycle_pages: 16" not in written
        assert "oldest_month: 202601" in written, "the migrated floor must be written to disk"


class TestSourceMatchingDaysMigration:
    """Three keys collapse into one, overridable per source (ADR 0004)."""

    def _migrate(self, raw: dict[str, Any]) -> dict[str, Any]:
        from gamarr.config import _migrate_config

        _migrate_config(raw)
        return raw

    def test_a_source_key_is_renamed(self) -> None:
        raw = self._migrate({"download_sites": [{"fitgirl": {"max_queue_days": 90}}]})
        assert raw["download_sites"][0]["fitgirl"]["source_matching_days"] == 90
        assert "max_queue_days" not in raw["download_sites"][0]["fitgirl"]

    def test_the_metacritic_fallback_is_hoisted(self) -> None:
        raw = self._migrate(
            {
                "review_sites": {"metacritic": {"max_queue_days": 45, "platform_overrides": {"pc": {}}}},
                "download_sites": [{"fitgirl": {"enabled": True}}],
            }
        )
        assert raw["source_matching_days"] == 45
        assert "max_queue_days" not in raw["review_sites"]["metacritic"]

    def test_the_existing_top_level_value_wins(self) -> None:
        raw = self._migrate(
            {
                "source_matching_days": 12,
                "review_sites": {"metacritic": {"max_queue_days": 45, "platform_overrides": {"pc": {}}}},
            }
        )
        assert raw["source_matching_days"] == 12
        assert "max_queue_days" not in raw["review_sites"]["metacritic"]

    def test_a_negative_source_window_means_never_expire(self) -> None:
        raw = self._migrate({"download_sites": [{"fitgirl": {"max_queue_days": -1}}]})
        assert raw["download_sites"][0]["fitgirl"]["source_matching_days"] == 0
        assert "max_queue_days" not in raw["download_sites"][0]["fitgirl"]

    def test_a_renamed_source_key_wins(self) -> None:
        raw = self._migrate({"download_sites": [{"fitgirl": {"max_queue_days": 90, "source_matching_days": 15}}]})
        assert raw["download_sites"][0]["fitgirl"]["source_matching_days"] == 15
        assert "max_queue_days" not in raw["download_sites"][0]["fitgirl"]

    def test_a_sparse_freegog_keeps_its_legacy_window(self) -> None:
        raw = self._migrate({"download_sites": [{"freegog": {"max_queue_days": 10}}]})
        assert raw["download_sites"][0]["freegog"]["source_matching_days"] == 10


class TestDiscoveryMigrationBranches:
    """Each migration branch is deterministic: convert, ignore, or skip."""

    def _migrate(self, raw: dict[str, Any]) -> dict[str, Any]:
        from gamarr.config import _migrate_config

        _migrate_config(raw)
        return raw

    def test_a_latest_page_depth_is_dropped_when_a_floor_is_set(self) -> None:
        raw = self._migrate(
            {
                "review_sites": {
                    "metacritic": {
                        "platform_overrides": {"pc": {"mode": "latest", "max_date": 202601, "max_pages": 900}}
                    }
                }
            }
        )
        latest = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["latest"]
        assert latest == {"oldest_month": 202601, "months_per_cycle": 0}, "the old cycle read to its floor"

    def test_a_latest_cycle_pace_is_dropped(self) -> None:
        raw = self._migrate(
            {"review_sites": {"metacritic": {"platform_overrides": {"pc": {"mode": "latest", "max_cycle_pages": 5}}}}}
        )
        discovery = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]
        assert discovery["latest"]["months_per_cycle"] == 16, "the old default page reach is kept"

    def test_a_backlog_page_budget_is_dropped_when_a_floor_is_set(self) -> None:
        raw = self._migrate(
            {
                "review_sites": {
                    "metacritic": {
                        "platform_overrides": {
                            "pc": {"mode": "backlog", "max_date": 202001, "max_pages": 900, "sort_order": "new"}
                        }
                    }
                }
            }
        )
        new_block = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["new"]
        assert new_block == {"oldest_month": 202001, "months_per_cycle": 0}, "an unpaced floor stays unpaced"

    def test_a_backlog_page_pace_is_dropped_when_a_month_pace_is_set(self) -> None:
        raw = self._migrate(
            {
                "review_sites": {
                    "metacritic": {
                        "platform_overrides": {
                            "pc": {
                                "mode": "backlog",
                                "max_cycle_months": 3,
                                "max_cycle_pages": 900,
                                "sort_order": "new",
                            }
                        }
                    }
                }
            }
        )
        new_block = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["new"]
        assert new_block["months_per_cycle"] == 3, "the legacy pace is kept"
        assert new_block["oldest_month"], "a pace-only legacy config still gets the default floor"

    def test_a_non_mapping_source_entry_is_skipped(self) -> None:
        from gamarr.config import _migrate_source_matching_days

        assert _migrate_source_matching_days({"download_sites": ["broken"]}) is False
        assert _migrate_source_matching_days({"download_sites": "not-a-list"}) is False

    def test_a_missing_discovery_shape_is_skipped(self) -> None:
        from gamarr.config import _migrate_discovery_blocks, _migrate_metacritic_window

        assert _migrate_discovery_blocks({"download_sites": []}) is False
        assert _migrate_discovery_blocks({"review_sites": {"metacritic": {}}}) is False
        assert _migrate_metacritic_window({"review_sites": []}, False) is False
        assert _migrate_metacritic_window({"review_sites": {"metacritic": {}}}, False) is False


class TestLegacyValueConversion:
    """Legacy values convert to usable bounds, or are dropped as unset (ADR 0007)."""

    def _migrate(self, platform: dict[str, Any]) -> dict[str, Any]:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {"review_sites": {"metacritic": {"platform_overrides": {"pc": platform}}}}
        _migrate_config(raw)
        discovery: dict[str, Any] = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]
        return discovery

    def test_a_floor_the_operator_already_set_keeps_its_default_pace(self) -> None:
        """Only a floor the migration lands is unpaced; an existing one keeps 1 month."""
        discovery = self._migrate({"mode": "backlog", "discovery": {"backlog": {"new": {"oldest_month": 202001}}}})
        assert discovery["backlog"]["new"] == {"oldest_month": 202001}

    def test_a_converted_page_pace_survives_the_unpaced_guard(self) -> None:
        """The guard runs last, so a legacy page pace is not overwritten by its 0."""
        discovery = self._migrate({"mode": "backlog", "sort_order": "new", "max_date": 202001, "max_cycle_pages": 48})
        assert discovery["backlog"]["new"] == {"oldest_month": 202001, "months_per_cycle": 2}

    def test_a_score_sorted_floor_stays_unpaced_when_switched_to_new(self) -> None:
        """Date keys land in `new` under a score sort; the floor there stays unpaced."""
        discovery = self._migrate({"mode": "backlog", "sort_order": "criticscore", "max_date": 202001})
        assert discovery["backlog"]["new"] == {"oldest_month": 202001, "months_per_cycle": 0}

    def test_a_legacy_max_date_that_is_not_a_month_is_ignored(self) -> None:
        """A page-shaped number in max_date must not become an unloadable floor."""
        from gamarr.config import _default_floor

        discovery = self._migrate({"mode": "backlog", "max_date": 120})
        new_block = discovery["backlog"]["new"]
        assert new_block["oldest_month"] == _default_floor(), "a junk floor falls back to the default reach"
        assert new_block["months_per_cycle"] == 0, "and keeps the old one-sweep reach"

    def test_a_converted_page_budget_stays_unpaced_to_its_floor(self) -> None:
        """A legacy page budget that becomes a floor keeps reading to it in one sweep."""
        discovery = self._migrate({"mode": "backlog", "max_pages": 48, "sort_order": "new"})
        new_block = discovery["backlog"]["new"]
        assert new_block["oldest_month"], "the page budget becomes a floor"
        assert new_block["months_per_cycle"] == 0, "and the old sweep stays unpaced"

    def test_a_default_floor_stays_unpaced(self) -> None:
        """The invented default floor preserves the old one-cycle reach too."""
        discovery = self._migrate({"mode": "backlog"})
        new_block = discovery["backlog"]["new"]
        assert new_block["oldest_month"], "a default floor is written"
        assert new_block["months_per_cycle"] == 0, "without narrowing the old sweep"

    def test_a_huge_page_budget_is_clamped_to_a_usable_floor(self) -> None:
        from gamarr.config import DiscoveryConfig
        from gamarr.utils import EARLIEST_BROWSE_YEAR

        discovery = self._migrate({"mode": "backlog", "max_pages": 100000, "sort_order": "new"})
        assert discovery["backlog"]["new"]["oldest_month"] == EARLIEST_BROWSE_YEAR * 100 + 1
        DiscoveryConfig.model_validate(discovery)

    def test_a_huge_latest_budget_is_clamped_to_the_pace_cap(self) -> None:
        from gamarr.config import DiscoveryConfig

        discovery = self._migrate({"mode": "latest", "max_pages": 100000})
        assert discovery["latest"]["months_per_cycle"] == 1200
        DiscoveryConfig.model_validate(discovery)

    def test_zero_page_keys_are_not_converted_into_bounds(self) -> None:
        from gamarr.utils import current_year_month, pages_to_months, subtract_months

        discovery = self._migrate({"mode": "backlog", "max_pages": 0, "max_cycle_pages": 0, "sort_order": "new"})
        expected = subtract_months(current_year_month(), pages_to_months(500))
        assert discovery["backlog"]["new"]["oldest_month"] == expected, "0 is unset, so the old default reach applies"

    def test_an_explicit_zero_latest_budget_becomes_one_month(self) -> None:
        discovery = self._migrate({"mode": "latest", "max_pages": 0, "max_cycle_pages": 0})
        assert discovery["latest"]["months_per_cycle"] == 1, "ADR 0007 converts every page count to at least one month"

    def test_numeric_strings_and_integral_floats_are_accepted(self) -> None:
        discovery = self._migrate({"mode": "backlog", "max_date": "202401", "max_pages": 500.0, "sort_order": "new"})
        assert discovery["backlog"]["new"]["oldest_month"] == 202401

    def test_the_platform_window_is_hoisted(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {"review_sites": {"metacritic": {"platform_overrides": {"pc": {"max_queue_days": 45}}}}}
        _migrate_config(raw)
        assert raw["source_matching_days"] == 45
        assert "max_queue_days" not in raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]

    def test_a_huge_legacy_budget_still_loads(self, tmp_path: Path) -> None:
        from gamarr.utils import EARLIEST_BROWSE_YEAR

        path = tmp_path / "gamarr.yml"
        path.write_text(
            "review_sites:\n"
            "  metacritic:\n"
            "    platform_overrides:\n"
            "      pc:\n"
            "        mode: backlog\n"
            "        max_pages: 100000\n"
            "        sort_order: new\n",
            encoding="utf-8",
        )
        cfg = load_config(path)
        block = cfg.review_sites.metacritic.platform_overrides["pc"].discovery.backlog.new
        assert block.oldest_month == EARLIEST_BROWSE_YEAR * 100 + 1

    def test_date_keys_migrate_into_the_new_block_under_a_score_sort(self) -> None:
        discovery = self._migrate(
            {"mode": "backlog", "max_date": 202001, "max_cycle_months": 3, "sort_order": "criticscore"}
        )
        assert discovery["backlog"]["new"] == {"oldest_month": 202001, "months_per_cycle": 3}

    def test_a_page_pace_converts_when_no_month_pace_is_set(self) -> None:
        discovery = self._migrate({"mode": "backlog", "max_cycle_pages": 48, "sort_order": "new"})
        assert discovery["backlog"]["new"]["months_per_cycle"] == 2

    def test_a_floored_latest_without_a_pace_reads_to_the_floor(self) -> None:
        discovery = self._migrate({"mode": "latest", "max_date": 202601})
        assert discovery["latest"] == {"oldest_month": 202601, "months_per_cycle": 0}

    def test_an_unusable_page_budget_falls_back_to_the_default(self) -> None:
        from gamarr.utils import current_year_month, pages_to_months, subtract_months

        discovery = self._migrate({"mode": "backlog", "max_pages": "not-a-number", "sort_order": "new"})
        expected = subtract_months(current_year_month(), pages_to_months(500))
        assert discovery["backlog"]["new"]["oldest_month"] == expected

    def test_an_unusable_date_value_falls_back_to_the_default_floor(self) -> None:
        from gamarr.utils import current_year_month, pages_to_months, subtract_months

        discovery = self._migrate({"mode": "backlog", "max_date": "2024-01", "sort_order": "new"})
        expected = subtract_months(current_year_month(), pages_to_months(500))
        assert discovery["backlog"]["new"]["oldest_month"] == expected

    def test_a_non_mapping_discovery_value_does_not_abort_the_migration(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {"platform_overrides": {"pc": {"mode": "backlog", "discovery": None, "max_date": 202001}}}
            }
        }
        _migrate_config(raw)
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["discovery"]["mode"] == "backlog"
        assert pc["discovery"]["backlog"]["new"]["oldest_month"] == 202001

    def test_a_quoted_old_default_window_is_dropped(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {"review_sites": {"metacritic": {"platform_overrides": {"pc": {"max_queue_days": "30"}}}}}
        _migrate_config(raw)
        assert "source_matching_days" not in raw

    def test_an_exotic_string_window_does_not_abort_the_migration(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {"metacritic": {"platform_overrides": {"pc": {"max_queue_days": "--5"}}}}
        }
        assert _migrate_config(raw) is True
        assert "max_queue_days" not in raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]

    def test_a_score_sort_writes_no_floor_for_an_explicit_zero_page_budget(self) -> None:
        discovery = self._migrate(
            {"mode": "backlog", "max_pages": 0, "max_cycle_pages": 0, "sort_order": "criticscore"}
        )
        assert discovery["backlog"]["score"] == {"pages_per_cycle": 0}, "a 0 budget means no budget, not a floor"

    def test_an_existing_block_mode_keeps_its_selection(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {
                        "pc": {
                            "max_date": 202001,
                            "discovery": {"mode": "latest", "latest": {"oldest_month": 202501}},
                        }
                    }
                }
            }
        }
        _migrate_config(raw)
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["discovery"]["mode"] == "latest", "the block's own mode wins"
        assert pc["discovery"]["latest"]["oldest_month"] == 202501, "the block keeps its floor"
        assert "max_date" not in pc, "the losing legacy key is consumed, not left on disk"

    def test_an_explicit_zero_floor_wins_over_a_legacy_key(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {
                        "pc": {"max_date": 202001, "discovery": {"mode": "latest", "latest": {"oldest_month": 0}}}
                    }
                }
            }
        }
        _migrate_config(raw)
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["discovery"]["latest"]["oldest_month"] == 0, "an explicit no-floor is a real value"
        assert "max_date" not in pc, "the losing legacy key is consumed"

    def test_migrate_config_preserves_an_unrelated_key(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {"metacritic": {"platform_overrides": {"pc": {"mode": "latest", "reject_genre": ["RPG"]}}}}
        }
        _migrate_config(raw)
        assert raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["reject_genre"] == ["RPG"]

    def test_a_mixed_file_migrates_the_keys_the_block_does_not_set(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {
                        "pc": {
                            "mode": "latest",
                            "sort_order": "userscore",
                            "max_date": 202001,
                            "discovery": {"mode": "latest", "latest": {"oldest_month": 202501}},
                        }
                    }
                }
            }
        }
        _migrate_config(raw)
        metacritic = raw["review_sites"]["metacritic"]
        pc = metacritic["platform_overrides"]["pc"]
        assert pc["discovery"]["latest"]["oldest_month"] == 202501, "the block wins"
        assert metacritic["pending_order"] == "userscore", "the unset shared key still migrates"
        assert "sort_order" not in pc and "max_date" not in pc, "consumed keys are dropped from the file"

    def test_a_negative_window_migrates_to_never_expire(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {"review_sites": {"metacritic": {"platform_overrides": {"pc": {"max_queue_days": -1}}}}}
        _migrate_config(raw)
        assert raw["source_matching_days"] == 0

    def test_migration_writes_only_the_block_the_mode_reads(self) -> None:
        from gamarr.config import _default_floor

        # A latest cycle leaves no backlog block behind.
        assert self._migrate({"mode": "latest"}) == {"mode": "latest", "latest": {"months_per_cycle": 16}}
        # A score-sorted backlog keeps the reach the old default page budget gave it, so
        # its block is filled rather than pruned (ADR 0007: an upgrade neither narrows
        # nor widens the sweep).
        assert self._migrate({"mode": "backlog", "sort_order": "criticscore"}) == {
            "mode": "backlog",
            "backlog": {"sort_order": "criticscore", "score": {"oldest_month": _default_floor()}},
        }

    def test_a_second_platform_sort_order_does_not_override_pending_order(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {
                        "pc": {"mode": "backlog", "sort_order": "criticscore"},
                        "ps5": {"mode": "backlog", "sort_order": "userscore"},
                    }
                }
            }
        }
        _migrate_config(raw)
        assert raw["review_sites"]["metacritic"]["pending_order"] == "criticscore"


class TestInertDiscoveryKeys:
    """Inert keys are file keys whose value differs from the model default."""

    def test_every_stray_placement_is_reported(self) -> None:
        from gamarr.config import _stray_discovery_keys

        assert _stray_discovery_keys({"discovery": {"mode": "latest"}}) == ["top-level: discovery"]
        assert _stray_discovery_keys({"review_sites": {"discovery": {}}}) == ["review_sites: discovery"]
        assert _stray_discovery_keys({"review_sites": {"metacritic": {"discovery": {}}}}) == ["metacritic: discovery"]
        assert _stray_discovery_keys(
            {"review_sites": {"metacritic": {"platform_overrides": {"pc": {"pending_order": "new"}}}}}
        ) == ["pc: pending_order"]

    def test_the_top_level_window_rejects_a_negative_value(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import Config

        with pytest.raises(ValidationError):
            Config(source_matching_days=-1)

    def test_a_key_outside_the_discovery_block_is_reported(self) -> None:
        from gamarr.config import _stray_discovery_keys

        raw: dict[str, Any] = {"review_sites": {"metacritic": {"platform_overrides": {"pc": {"oldest_month": 202401}}}}}
        assert _stray_discovery_keys(raw) == ["pc: oldest_month"]

    def test_a_generated_config_writes_no_null_source_window(self, tmp_path: Path) -> None:
        from gamarr.config import create_default_config

        create_default_config(tmp_path)
        assert "source_matching_days: null" not in (tmp_path / "gamarr.yml").read_text(encoding="utf-8")

    def test_defaults_report_nothing(self) -> None:
        from gamarr.config import _default_config_dict, _inert_discovery_keys

        raw = _default_config_dict()
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert "discovery" in pc, "the block is written so the operator has keys to edit"
        assert _inert_discovery_keys(raw) == [], "but its defaults are not operator intent"

    def test_a_stale_key_in_the_unread_block_is_reported(self) -> None:
        from gamarr.config import _inert_discovery_keys

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {
                        "pc": {"discovery": {"mode": "latest", "backlog": {"new": {"oldest_month": 202001}}}}
                    }
                }
            }
        }
        assert _inert_discovery_keys(raw) == ["pc: discovery.backlog.new.oldest_month"]

    def test_a_score_backlog_reports_latest_and_the_new_block(self) -> None:
        from gamarr.config import _inert_discovery_keys

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {
                        "pc": {
                            "discovery": {
                                "mode": "backlog",
                                "latest": {"months_per_cycle": 2},
                                "backlog": {"sort_order": "criticscore", "new": {"oldest_month": 202001}},
                            }
                        }
                    }
                }
            }
        }
        assert _inert_discovery_keys(raw) == [
            "pc: discovery.latest.months_per_cycle",
            "pc: discovery.backlog.new.oldest_month",
        ]

    def test_loading_a_mixed_file_reports_the_inert_key(self, tmp_path: Path) -> None:
        from loguru import logger as loguru_logger

        path = tmp_path / "gamarr.yml"
        path.write_text(
            "review_sites:\n"
            "  metacritic:\n"
            "    platform_overrides:\n"
            "      pc:\n"
            "        discovery:\n"
            "          mode: latest\n"
            "          backlog:\n"
            "            new:\n"
            "              oldest_month: 202001\n",
            encoding="utf-8",
        )
        captured: list[str] = []
        sink_id = loguru_logger.add(lambda msg: captured.append(str(msg)), level="INFO", format="{message}")
        try:
            load_config(path)
        finally:
            loguru_logger.remove(sink_id)
        assert any("discovery.backlog.new.oldest_month" in line and "not read" in line for line in captured), captured

    def test_a_rewritten_default_file_reports_nothing(self, tmp_path: Path) -> None:
        from gamarr.config import _inert_discovery_keys, _load_raw_config, create_default_config

        create_default_config(tmp_path)
        load_config(tmp_path)
        assert _inert_discovery_keys(_load_raw_config(tmp_path / "gamarr.yml")) == []


class TestGeneratedConfigCarriesDiscovery:
    """A generated or rewritten config must offer the discovery keys (bug 2026-11-23)."""

    def test_a_generated_config_contains_the_discovery_block(self, tmp_path: Path) -> None:
        import yaml

        from gamarr.config import create_default_config

        create_default_config(tmp_path)
        raw = yaml.safe_load((tmp_path / "gamarr.yml").read_text(encoding="utf-8"))
        discovery = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]
        assert discovery["mode"] == "latest"
        assert set(discovery["latest"]) == {"oldest_month", "months_per_cycle"}
        assert discovery["backlog"]["sort_order"] == "new"
        assert set(discovery["backlog"]["new"]) == {"oldest_month", "months_per_cycle"}
        assert set(discovery["backlog"]["score"]) == {"oldest_month", "pages_per_cycle"}

    def test_an_empty_discovery_block_is_filled_in(self, tmp_path: Path) -> None:
        """`ps5: {discovery: {}}` is an empty block, not an opinion to respect."""
        import yaml

        from gamarr.config import create_default_config

        create_default_config(tmp_path)
        path = tmp_path / "gamarr.yml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["review_sites"]["metacritic"]["platform_overrides"]["ps5"] = {"discovery": {}}
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        load_config(path)
        written = yaml.safe_load(path.read_text(encoding="utf-8"))
        ps5 = written["review_sites"]["metacritic"]["platform_overrides"]["ps5"]
        assert ps5["discovery"]["backlog"]["new"]["months_per_cycle"] == 1

    def test_a_non_mapping_discovery_value_is_left_to_validation(self) -> None:
        """A non-mapping `discovery:` value is the operator's data, not ours to overwrite."""
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {"review_sites": {"metacritic": {"platform_overrides": {"pc": {"discovery": "backlog"}}}}}
        _migrate_config(raw)
        assert raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"] == "backlog"

    def test_a_second_platform_gains_the_discovery_block(self, tmp_path: Path) -> None:
        """`pc` gets the block from the defaults; any other platform needs the migration."""
        import yaml

        from gamarr.config import create_default_config

        create_default_config(tmp_path)
        path = tmp_path / "gamarr.yml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["review_sites"]["metacritic"]["platform_overrides"]["ps5"] = {"min_criticscore": 80}
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        load_config(path)
        written = yaml.safe_load(path.read_text(encoding="utf-8"))
        ps5 = written["review_sites"]["metacritic"]["platform_overrides"]["ps5"]
        assert "discovery" in ps5, "the second platform must gain editable discovery keys"

    def test_loading_a_file_without_discovery_adds_the_block(self, tmp_path: Path) -> None:
        path = tmp_path / "gamarr.yml"
        path.write_text(
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n        min_criticscore: 80\n",
            encoding="utf-8",
        )
        load_config(path)
        assert "discovery:" in path.read_text(encoding="utf-8"), "the rewrite must add the block"
