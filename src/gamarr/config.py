"""Configuration loading, validation, and default creation for gamarr."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from loguru import logger
from pydantic import BaseModel, Field, RootModel, field_validator

if TYPE_CHECKING:
    from collections.abc import Iterator


_CONFIG_VERSION = "1.0.0"
_CONFIG_FILENAME = "gamarr.yml"


class GeneralConfig(BaseModel):
    """Top-level general runtime options."""

    config_version: str = _CONFIG_VERSION
    daemon_mode: str = "foreground"
    log_level_console: str = "INFO"
    log_level_file: str = "INFO"
    log_path: str = "logs"
    db_path: str = "db"
    pid_path: str = "pids"
    library_path_list: list[str] = Field(default_factory=list)


class ScheduleTaskConfig(BaseModel):
    """Settings for a single scheduled task (acquisition, scoring, etc.)."""

    enabled: bool = False
    schedule_time_mins: int = Field(default=60, gt=0)
    run_on_start: bool = True


class ScheduleConfig(BaseModel):
    """Scheduling configuration for periodic tasks."""

    acquisition: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=60))


class SourceConfigEntry(BaseModel):
    """A single download source entry."""

    name: str = Field(exclude=True)  # hidden from YAML, populated from dict key
    enabled: bool = True
    platform: str = "pc"
    cache_pages_hours: int = Field(default=6, gt=0, le=168)
    reject_keywords: list[str] = Field(default_factory=list)
    max_queue_days: int = Field(default=60, ge=0)
    feed_url: str | None = None


class DownloadSitesConfig(RootModel[list[SourceConfigEntry]]):
    """Ordered list of download source configurations.

    Position in the list defines priority: earlier = higher priority.
    """

    root: list[SourceConfigEntry] = [
        SourceConfigEntry(name="fitgirl", feed_url="https://fitgirl-repacks.site/feed/"),
        SourceConfigEntry(name="dodi", feed_url="https://hydralinks.cloud/sources/dodi.json"),
    ]

    @staticmethod
    def _rename_feed_url(entry: dict[str, Any]) -> None:
        """Rename rss_url to feed_url in-place if the old field name is present."""
        if "feed_url" not in entry and "rss_url" in entry:
            entry["feed_url"] = entry.pop("rss_url")

    @field_validator("root", mode="before")
    @classmethod
    def _parse_keyed_list(cls, v: Any) -> Any:
        """Convert [{'fitgirl': {...}}, {'dodi': {...}}] into populated list.

        Also handles legacy [{name: ..., rss_url: ...}] format.
        """
        if not isinstance(v, list):
            return v
        result: list[dict[str, Any]] = []
        for item in v:
            if not isinstance(item, dict):
                result.append(item)
                continue
            # Flat format: {"name": "fitgirl", ...}
            if "name" in item:
                cls._rename_feed_url(item)
                result.append(item)
                continue
            # Keyed format: {"fitgirl": {...}}
            for key, val in item.items():
                if isinstance(val, dict):
                    val["name"] = key
                    cls._rename_feed_url(val)
                    result.append(val)
                else:
                    result.append({"name": key})
        return result

    def __iter__(self) -> Iterator[SourceConfigEntry]:  # type: ignore[override]
        return iter(self.root)

    def __getitem__(self, idx: int) -> SourceConfigEntry:
        return self.root[idx]

    def __len__(self) -> int:
        return len(self.root)


class MetacriticPlatformConfig(BaseModel):
    """Metacritic scoring thresholds for a single platform."""

    min_metascore: int = 75
    min_metascore_reviews: int = 10
    min_user_score: float = 7.5
    min_user_reviews: int = 10
    cache_details_days: int = 7
    cache_pages_hours: int = 6
    max_queue_days: int = 30
    enabled: bool = True
    max_weeks: int | None = Field(default=13, ge=0)
    max_cycle_weeks: int | None = Field(default=4, ge=0)
    reject_genre: list[str] = Field(default_factory=list)
    reject_title: list[str] = Field(default_factory=list)  # case-insensitive substrings
    age_recheck_weeks: int | None = Field(default=None, ge=0)


class MetacriticConfig(BaseModel):
    """Metacritic integration options."""

    platform_overrides: dict[str, MetacriticPlatformConfig] = Field(
        default_factory=lambda: {"pc": MetacriticPlatformConfig()}
    )


class QbittorrentConfig(BaseModel):
    """qBittorrent client connection parameters."""

    host: str = "localhost"
    port: int = 8080
    username: str = "admin"
    password: str = "adminadmin"
    add_paused: bool = False
    category: str = "games-gamarr"


class TorrentClientConfig(BaseModel):
    """Torrent client selection and per-client settings."""

    selected: str = "qbittorrent"
    qbittorrent: QbittorrentConfig = Field(default_factory=QbittorrentConfig)


class NotificationConfig(BaseModel):
    """Notification delivery settings via Apprise."""

    apprise_urls: list[str] = Field(default_factory=list)
    on_download: bool = True
    on_failure: bool = False
    on_error: bool = False
    on_scrape_failure: bool = True


class DatabaseConfig(BaseModel):
    """Database retention and housekeeping settings."""

    processed_expiry_days: int = 365


class LibraryConfig(BaseModel):
    """Game library scanning settings.

    When ``paths`` is non-empty, library scanning is enabled.
    """

    paths: list[str] = Field(default_factory=list)


class ReviewSitesConfig(BaseModel):
    """Aggregated review site configurations."""

    metacritic: MetacriticConfig = Field(default_factory=MetacriticConfig)


class Config(BaseModel):
    """Root configuration model that aggregates all sub-configs."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    download_sites: DownloadSitesConfig = Field(
        default_factory=lambda: DownloadSitesConfig(
            root=[
                SourceConfigEntry(name="fitgirl", feed_url="https://fitgirl-repacks.site/feed/"),
                SourceConfigEntry(name="dodi", feed_url="https://hydralinks.cloud/sources/dodi.json"),
            ]
        )
    )
    review_sites: ReviewSitesConfig = Field(default_factory=ReviewSitesConfig)
    torrent_client: TorrentClientConfig = Field(default_factory=TorrentClientConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    library: LibraryConfig = Field(default_factory=LibraryConfig)


def _normalize_date_values(d: Any) -> Any:
    """Recursively convert datetime.date objects to ISO strings in dicts/lists.

    PyYAML parses ``2025-01-01`` as a ``datetime.date``, but the config
    model expects ``str``.  This catches that conversion at load time.
    """
    import datetime

    if isinstance(d, datetime.date):
        return d.isoformat()
    if isinstance(d, dict):
        return {k: _normalize_date_values(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_normalize_date_values(v) for v in d]
    return d


def _rename_config_key(mc_pc: dict[str, Any], old_key: str, new_key: str | None, platform_key: str) -> bool:
    """Rename *old_key* to *new_key* in *mc_pc* if it exists, logging the migration.

    If both old and new keys exist, the existing new-key value is preserved
    and only the old key is cleaned up (does not overwrite the user's value).

    Returns True if a key was renamed or removed.
    """
    if old_key not in mc_pc:
        return False
    if new_key == old_key:
        return False
    if new_key is None:
        del mc_pc[old_key]
        logger.info("Config: removed old key '{}' for platform '{}'", old_key, platform_key)
    elif new_key in mc_pc:
        # Both exist — clean up old key, preserve the user's new-key value
        del mc_pc[old_key]
        logger.info(
            "Config: old key '{}' already migrated to '{}' for platform '{}' — keeping existing value",
            old_key,
            new_key,
            platform_key,
        )
    else:
        mc_pc[new_key] = mc_pc.pop(old_key)
        logger.info("Config: migrated '{}'\u2192'{}' for platform '{}'", old_key, new_key, platform_key)
    return True


def _migrate_platform_overrides(raw: dict[str, Any]) -> bool:
    """Migrate renamed/deprecated keys in metacritic.platform_overrides.

    Returns True if any migration was applied.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if not isinstance(mc_pc, dict):
            continue
        changed |= _rename_config_key(mc_pc, "browse_enabled", "enabled", platform_key)
        changed |= _rename_config_key(mc_pc, "browse_cache_ttl_hours", "cache_pages_hours", platform_key)
        changed |= _rename_config_key(mc_pc, "metacritic_enabled", "enabled", platform_key)
        changed |= _drop_metacritic_max_games(mc_pc, platform_key)
        changed |= _drop_migrated_deprecated_keys(mc_pc, platform_key)
        changed |= _rename_config_key(mc_pc, "metacritic_cache_ttl_hours", "cache_pages_hours", platform_key)
        changed |= _rename_config_key(mc_pc, "cache_ttl_days", "cache_details_days", platform_key)
        changed |= _rename_config_key(mc_pc, "cache_ttl_hours", "cache_pages_hours", platform_key)
        changed |= _drop_max_verify_attempts(mc_pc, platform_key)
    return changed


def _drop_metacritic_max_games(mc_pc: dict[str, Any], platform_key: str) -> bool:
    """Remove deprecated metacritic_max_games key. Returns True if changed."""
    if "metacritic_max_games" not in mc_pc:
        return False
    logger.info(
        "Config: removing deprecated 'metacritic_max_games' for platform '{}' — use max_weeks instead",
        platform_key,
    )
    del mc_pc["metacritic_max_games"]
    return True


def _drop_migrated_deprecated_keys(mc_pc: dict[str, Any], platform_key: str) -> bool:
    """Drop keys that were deprecated in earlier versions. Returns True if any changed."""
    changed = False
    for old_key in ("browse_max_pages", "max_score_checks"):
        if old_key in mc_pc:
            logger.warning(
                "Config: '{}' is deprecated for platform '{}'; "
                "set 'max_weeks' to control the discovery window (count-based throttling removed). Ignoring value.",
                old_key,
                platform_key,
            )
            mc_pc.pop(old_key)
            changed |= True
    for old_key in ("browse_cutoff_date", "metacritic_cutoff_date", "cutoff_date"):
        if old_key in mc_pc:
            logger.warning(
                "Config: '{}' is deprecated for platform '{}'; "
                "set 'max_weeks' instead (e.g. max_weeks: 52 for ~1 year). Ignoring value.",
                old_key,
                platform_key,
            )
            mc_pc.pop(old_key)
            changed |= True
    return changed


def _drop_max_verify_attempts(mc_pc: dict[str, Any], platform_key: str) -> bool:
    """Remove deprecated max_verify_attempts key. Returns True if changed."""
    if "max_verify_attempts" not in mc_pc:
        return False
    logger.info(
        "Config: removing deprecated 'max_verify_attempts' for platform '{}' — max_queue_days controls expiry",
        platform_key,
    )
    del mc_pc["max_verify_attempts"]
    return True


def _migrate_download_sites_to_ordered(raw: dict[str, Any]) -> bool:
    """Migrate flat download_sites.{name}: {{...}} dict to ordered list."""
    ds = raw.get("download_sites")
    if not isinstance(ds, dict):
        return False
    ordered: list[dict[str, Any]] = []
    for name, cfg in ds.items():
        if isinstance(cfg, dict):
            cfg["name"] = name
            ordered.append(cfg)
        else:
            logger.warning(
                "Config: skipping non-dict entry '{}' in download_sites (type: {})",
                name,
                type(cfg).__name__,
            )
    if not ordered:
        logger.warning("Config: no valid entries in download_sites — keeping original")
        return False
    raw["download_sites"] = ordered
    logger.info("Config: migrated flat download_sites to ordered list (%d sources)", len(ordered))
    return True


def _migrate_download_sites(raw: dict[str, Any]) -> bool:
    """Rename old sources key to download_sites.

    Returns True if a migration was applied.
    """
    if "sources" not in raw:
        return False
    if "download_sites" not in raw or raw["download_sites"] is None:
        raw["download_sites"] = raw.pop("sources")
        logger.info("Config: migrated 'sources' to 'download_sites'")
        return True
    # Both exist — deep-merge sources into download_sites and drop sources
    old_sources = raw.pop("sources")
    if isinstance(old_sources, dict) and isinstance(raw["download_sites"], dict):
        raw["download_sites"] = _deep_merge(raw["download_sites"], old_sources)
        logger.info("Config: merged 'sources' into 'download_sites'")
    elif isinstance(old_sources, dict):
        logger.warning("Config: both 'sources' (dict) and 'download_sites' (list) exist — keeping download_sites")
    else:
        logger.warning("Config: dropped non-dict 'sources' value during migration")
    return True


def _migrate_fitgirl_exclude_keywords(raw: dict[str, Any]) -> bool:
    """Rename fitgirl.exclude_keywords to reject_keywords.

    Checks under both old (sources) and new (download_sites) keys.
    Returns True if a migration was applied.
    """
    for parent_key in ("download_sites", "sources"):
        parent = raw.get(parent_key)
        if not isinstance(parent, dict):
            continue
        fg = parent.get("fitgirl", {})
        if not isinstance(fg, dict) or "exclude_keywords" not in fg:
            continue
        if "reject_keywords" not in fg:
            fg["reject_keywords"] = fg.pop("exclude_keywords")
            logger.info("Config: migrated '{}.fitgirl.exclude_keywords' to 'reject_keywords'", parent_key)
            return True
        del fg["exclude_keywords"]
        return True
    return False


def _migrate_days_since_release(raw: dict[str, Any]) -> bool:
    """Convert deprecated days_since_release to max_weeks in metacritic.platform_overrides.

    max_weeks replaces this field (same purpose, weeks not days).
    Returns True if a migration was applied.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if isinstance(mc_pc, dict) and "days_since_release" in mc_pc:
            days = mc_pc.pop("days_since_release")
            if isinstance(days, (int, float)) and days > 0 and "max_weeks" not in mc_pc:
                mc_pc["max_weeks"] = max(1, round(days / 7))
                logger.info(
                    "Config: converted days_since_release={} to max_weeks={} for platform '{}'",
                    days,
                    mc_pc["max_weeks"],
                    platform_key,
                )
            else:
                logger.info(
                    "Config: removed deprecated 'days_since_release' for platform '{}'",
                    platform_key,
                )
            changed = True
    return changed


def _migrate_metacritic_exclude_keywords(raw: dict[str, Any]) -> bool:
    """Remove deprecated exclude_keywords from metacritic.platform_overrides.

    Returns True if a migration was applied.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if isinstance(mc_pc, dict) and "exclude_keywords" in mc_pc:
            logger.info(
                "Config: removing deprecated 'exclude_keywords' for platform '{}' — use reject_title instead",
                platform_key,
            )
            del mc_pc["exclude_keywords"]
            changed = True
    return changed


def _rename_pending_days(d: dict[str, Any], label: str) -> bool:
    """If *d* is a dict with ``pending_days`` (but no ``max_queue_days``), rename it.

    Returns True if the rename was applied.
    """
    if isinstance(d, dict) and "pending_days" in d and "max_queue_days" not in d:
        d["max_queue_days"] = d.pop("pending_days")
        logger.info("Config: renamed '{}.pending_days' to 'max_queue_days'", label)
        return True
    return False


def _migrate_pending_days_to_max_queue_days(raw: dict[str, Any]) -> bool:
    """Rename pending_days to max_queue_days in metacritic and fitgirl config sections.

    Returns True if a migration was applied.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if _rename_pending_days(mc_pc, f"review_sites.metacritic.platform_overrides.{platform_key}"):
            changed = True
    for parent_key in ("download_sites", "sources"):
        parent = raw.get(parent_key)
        if not isinstance(parent, dict):
            continue
        fg = parent.get("fitgirl", {})
        if _rename_pending_days(fg, f"{parent_key}.fitgirl"):
            changed = True
    return changed


def _migrate_fitgirl_cache_ttl_hours(raw: dict[str, Any]) -> bool:
    """Rename download_sites.fitgirl.cache_ttl_hours to cache_pages_hours.

    Returns True if a migration was applied.
    """
    changed = False
    for parent_key in ("download_sites", "sources"):
        parent = raw.get(parent_key)
        if not isinstance(parent, dict):
            continue
        fg = parent.get("fitgirl", {})
        if isinstance(fg, dict) and "cache_ttl_hours" in fg:
            if "cache_pages_hours" not in fg:
                fg["cache_pages_hours"] = fg.pop("cache_ttl_hours")
                logger.info(
                    "Config: renamed '{}.fitgirl.cache_ttl_hours' to 'cache_pages_hours'",
                    parent_key,
                )
            else:
                del fg["cache_ttl_hours"]
                logger.info(
                    "Config: removed old '{}.fitgirl.cache_ttl_hours' (already migrated to cache_pages_hours)",
                    parent_key,
                )
            changed = True
    return changed


def _migrate_cutoff_weeks_to_max_weeks(raw: dict[str, Any]) -> bool:
    """Rename cutoff_weeks to max_weeks in metacritic.platform_overrides.

    Returns True if any migration was applied.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if isinstance(mc_pc, dict) and "cutoff_weeks" in mc_pc:
            mc_pc["max_weeks"] = mc_pc.pop("cutoff_weeks")
            logger.info(
                "Config: renamed 'cutoff_weeks' to 'max_weeks' for platform '{}'",
                platform_key,
            )
            changed = True
    return changed


def _migrate_remove_max_games(raw: dict[str, Any]) -> bool:
    """Remove max_games from metacritic.platform_overrides.

    max_games is no longer needed — max_weeks controls the game count.
    Returns True if any migration was applied.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if isinstance(mc_pc, dict) and "max_games" in mc_pc:
            del mc_pc["max_games"]
            logger.info(
                "Config: removed 'max_games' for platform '{}' — use max_weeks to control game count",
                platform_key,
            )
            changed = True
    return changed


def _rename_recheck_days_in_dict(d: Any, label: str) -> bool:
    """If *d* is a dict with recheck_days (no max_queue_days), rename it.

    Returns True if a rename was applied.
    """
    if isinstance(d, dict) and "recheck_days" in d and "max_queue_days" not in d:
        d["max_queue_days"] = d.pop("recheck_days")
        logger.info("Config: renamed 'recheck_days' to 'max_queue_days' for '{}'", label)
        return True
    return False


def _migrate_recheck_days_to_max_queue_days(raw: dict[str, Any]) -> bool:
    """Rename recheck_days to max_queue_days in metacritic and fitgirl config sections.

    Returns True if any migration was applied.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if _rename_recheck_days_in_dict(mc_pc, f"review_sites.metacritic.platform_overrides.{platform_key}"):
            changed = True
    for parent_key in ("download_sites", "sources"):
        parent = raw.get(parent_key)
        if not isinstance(parent, dict):
            continue
        if _rename_recheck_days_in_dict(parent.get("fitgirl"), f"{parent_key}.fitgirl"):
            changed = True
    return changed


def _migrate_metacritic_to_review_sites(raw: dict[str, Any]) -> bool:
    """Move top-level metacritic key under review_sites.

    Runs early so all downstream migrations see the new path.
    Returns True if any migration was applied.
    """
    if "metacritic" not in raw:
        return False
    if "review_sites" not in raw:
        raw["review_sites"] = {}
    if "metacritic" not in raw["review_sites"]:
        raw["review_sites"]["metacritic"] = raw.pop("metacritic")
        logger.info("Config: migrated 'metacritic' to 'review_sites.metacritic'")
        return True
    raw["review_sites"]["metacritic"] = _deep_merge(raw["review_sites"]["metacritic"], raw.pop("metacritic"))
    return True


def _collect_download_site_names(ds: list) -> set[str]:
    """Collect entry names from download_sites list entries.

    Handles both keyed format (``{"fitgirl": {...}}``) and
    flat format (``{"name": "fitgirl", ...}``).
    """
    names: set[str] = set()
    for e in ds:
        if not isinstance(e, dict):
            continue
        # Keyed format: the dict keys ARE the source names
        if "name" not in e:
            names.update(k.casefold() for k in e)
        # Flat format: extract the "name" value only, not all field keys
        name_val = e.get("name")
        if isinstance(name_val, str):
            names.add(name_val.casefold())
    return names


def _apply_dodi_defaults(dodi_config: dict) -> None:
    """Apply missing default fields to a DODI config dict in-place."""
    dodi_defaults = SourceConfigEntry(name="dodi", feed_url="https://hydralinks.cloud/sources/dodi.json").model_dump(
        exclude={"name"}
    )
    for k, v in dodi_defaults.items():
        dodi_config.setdefault(k, v)


def _enhance_dodi_entry(ds: list) -> bool:
    """Find and enhance an existing DODI entry with any missing default fields.

    Handles both keyed format (``{"dodi": {...}}`` or ``{"DODI": {...}}``)
    and flat format (``{"name": "dodi", ...}``).
    Returns True if a DODI entry was found and enhanced, False otherwise.
    """
    for entry in ds:
        if not isinstance(entry, dict):
            continue
        # Keyed format: {Key: {...}} where Key casefolded matches "dodi"
        for key, val in entry.items():
            if key.casefold() == "dodi":
                if isinstance(val, dict):
                    _apply_dodi_defaults(val)
                    return True
                logger.warning("Config: DODI entry has non-dict value — skipping enhancement")
        # Flat format: {"name": "dodi", ...}
        if str(entry.get("name", "")).casefold() == "dodi":
            _apply_dodi_defaults(entry)
            return True
    return False


def _migrate_add_dodi_entry(raw: dict[str, Any]) -> bool:
    """Add a DODI entry to download_sites if one does not already exist.

    Assumes download_sites is in keyed format [{key: {...}}, ...].
    Returns True if a DODI entry was added or enhanced.
    """
    ds = raw.get("download_sites")
    if not isinstance(ds, list):
        return False

    if "dodi" in _collect_download_site_names(ds):
        return _enhance_dodi_entry(ds)

    # Use SourceConfigEntry defaults so field additions stay in sync
    dodi_entry = SourceConfigEntry(name="dodi", feed_url="https://hydralinks.cloud/sources/dodi.json").model_dump(
        exclude={"name"}
    )
    ds.append({"dodi": dodi_entry})
    logger.info("Config: added DODI entry to download_sites")
    return True


_KNOWN_BAD_DODI_HOSTS = {
    "1337x.to",
    "1377x.to",
    "x1337x.cc",
}


def _replace_bad_dodi_url(dodi_config: dict[str, Any]) -> bool:
    """Replace a DODI entry's feed_url if it uses a known-bad host.

    Returns True if the URL was replaced.
    """
    from urllib.parse import urlparse

    current = dodi_config.get("feed_url")
    if not isinstance(current, str):
        return False
    parsed = urlparse(current)
    if parsed.hostname and parsed.hostname.casefold() in _KNOWN_BAD_DODI_HOSTS:
        dodi_config["feed_url"] = "https://hydralinks.cloud/sources/dodi.json"
        logger.info(
            "Config: migrated DODI feed URL from '{}' to hydralinks.cloud",
            current,
        )
        return True
    return False


def _migrate_dodi_feed_url(raw: dict[str, Any]) -> bool:
    """Replace known-bad DODI feed URLs with the correct hydralinks.cloud endpoint.

    Scans ``download_sites`` for a DODI entry whose feed_url contains any
    known copycat or impersonation domain for 1337x.to and replaces it with
    ``https://hydralinks.cloud/sources/dodi.json``.

    Returns True if any URL was replaced.
    """
    ds = raw.get("download_sites")
    if not isinstance(ds, list):
        return False

    changed = False
    for entry in ds:
        if not isinstance(entry, dict):
            continue
        for key, val in entry.items():
            if key.casefold() != "dodi" or not isinstance(val, dict):
                continue
            if _replace_bad_dodi_url(val):
                changed = True
    return changed


def _convert_entry_to_keyed(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a legacy flat entry to keyed format.

    Input:  {"name": "fitgirl", "rss_url": "...", ...}
    Output: {"fitgirl": {"feed_url": "...", ...}}
    Returns None if *entry* is not a flat entry (no "name" key).
    """
    entry_name = entry.pop("name", None)
    if entry_name is None:
        return None
    if "rss_url" in entry and "feed_url" not in entry:
        entry["feed_url"] = entry.pop("rss_url")
    return {str(entry_name): dict(entry.items())}


def _migrate_download_sites_to_keyed_list(raw: dict[str, Any]) -> bool:
    """Convert [{name: ..., rss_url: ...}] to [{key: {feed_url: ...}}] format.

    Renames rss_url to feed_url and moves name into the dict key.
    Returns True if any migration was applied.
    """
    ds = raw.get("download_sites")
    if not isinstance(ds, list):
        return False

    changed = False
    for i, entry in enumerate(ds):
        if not isinstance(entry, dict):
            continue
        keyed = _convert_entry_to_keyed(entry)
        if keyed is not None:
            ds[i] = keyed
            changed = True

    if changed:
        logger.info("Config: migrated download_sites entries to keyed-list format")
    return changed


def _migrate_daemon_mode(raw: dict[str, Any]) -> bool:
    """Migrate deprecated general.daemon_mode to schedule.acquisition.enabled.

    Returns True if a migration was applied.
    """
    general = raw.get("general", {})
    if general.get("daemon_mode") != "background":
        return False
    schedule = raw.setdefault("schedule", {})
    acquisition = schedule.setdefault("acquisition", {})
    if "enabled" not in acquisition:
        acquisition["enabled"] = True
        logger.info(
            "Config: migrated deprecated 'general.daemon_mode: background' to 'schedule.acquisition.enabled: true'"
        )
        return True
    return False


def _migrate_config(raw: dict[str, Any]) -> bool:
    """Migrate renamed config keys in-place for all platforms.

    Returns True if any migration was applied.
    """
    try:
        changed = False
        # Run migration functions in order.  sources → download_sites
        # must run first so downstream migrations see the consolidated key.
        _migrations = [
            _migrate_metacritic_to_review_sites,
            _migrate_download_sites,
            _migrate_platform_overrides,
            _migrate_fitgirl_exclude_keywords,
            _migrate_fitgirl_cache_ttl_hours,
            _migrate_cutoff_weeks_to_max_weeks,
            _migrate_days_since_release,
            _migrate_pending_days_to_max_queue_days,
            _migrate_recheck_days_to_max_queue_days,
            _migrate_metacritic_exclude_keywords,
            _migrate_remove_max_games,
            _migrate_download_sites_to_ordered,
            _migrate_download_sites_to_keyed_list,
            _migrate_add_dodi_entry,
            _migrate_dodi_feed_url,
            _migrate_daemon_mode,
        ]
        for fn in _migrations:
            if fn(raw):
                changed = True
        return changed
    except Exception as exc:
        logger.warning("Config migration failed: {}", exc)
        return False


def _strip_none(value: Any) -> Any:
    """Strip None leaf values recursively (None dict entries and None list items)."""
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(item) for item in value if item is not None]
    return value


def _merge_value(base_value: Any, override_value: Any) -> Any:
    """Merge a single override value into a base value.

    Recursively deep-merges dicts. Replaces lists after stripping None items.
    None stripping prevents Pydantic ``ValidationError`` when YAML ``key: null``
    appears inside list entries (e.g. ``download_sites: [{reject_keywords: null}]``).
    Uses *override_value* directly for all other types.
    """
    if isinstance(base_value, dict) and isinstance(override_value, dict):
        return _deep_merge(base_value, override_value)
    if isinstance(override_value, list):
        return [v for v in (_strip_none(item) for item in override_value) if v is not None]
    return override_value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base* (shallow copy of base)."""
    result = dict(base)
    for key, value in override.items():
        if value is not None:
            result[key] = _merge_value(result.get(key), value)
    return result


def _config_keys(d: dict[str, Any], prefix: str = "") -> set[str]:
    """Return the full dotted key path set for a nested dict."""
    keys: set[str] = set()
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        keys.add(full)
        if isinstance(v, dict):
            keys |= _config_keys(v, full)
    return keys


def _needs_config_update(raw: dict[str, Any]) -> bool:
    """Return True if *raw* is missing any keys present in the current defaults."""
    raw_keys = _config_keys(raw)
    default_keys = _config_keys(_default_config_dict())
    return bool(default_keys - raw_keys)


def _next_version(current: str) -> str:
    """Bump the minor version component (e.g. 1.0.0 \u2192 1.1.0)."""
    parts = current.split(".")
    try:
        minor = int(parts[1]) + 1 if len(parts) >= 2 else 1
    except ValueError:
        minor = 1
    return f"{parts[0]}.{minor}.0"


def _default_config_dict() -> dict[str, Any]:
    """Return the default configuration as a plain nested dict."""
    cfg = Config()
    d = cfg.model_dump()
    # Re-include name in download_sites entries (excluded by Field(exclude=True))
    d["download_sites"] = [{entry.name: entry.model_dump(exclude={"name"})} for entry in cfg.download_sites]
    return d


def create_default_config(config_path: str | Path) -> None:
    """Write a default YAML config file if one does not already exist."""
    path = Path(config_path)
    if not path.suffix:
        path = path / _CONFIG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(_default_config_dict(), fh, default_flow_style=False, sort_keys=False)


def load_config(config_path: str | Path) -> Config:
    """Load a YAML config file and merge with defaults.

    Parameters
    ----------
    config_path:
        Path to a ``gamarr.yml`` file, or a directory containing one.

    Returns
    -------
    Config
        Fully populated configuration with defaults applied for any
        missing keys.
    """
    path = Path(config_path)
    if not path.suffix:
        path = path / _CONFIG_FILENAME

    if not path.exists():
        create_default_config(path)

    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)

    if loaded is None:
        raw: dict[str, Any] = {}
    elif not isinstance(loaded, dict):
        raise ValueError(f"Config file '{path}' must be a YAML mapping (got {type(loaded).__name__}).")
    else:
        raw = loaded

    # Migrate renamed fields (e.g. exclude_keywords → reject_keywords)
    migrated = _migrate_config(raw)

    merged = _deep_merge(_default_config_dict(), raw)

    # If migration made changes or the config is missing keys that the
    # current model defines, write the merged config back to the file
    # and bump the config version.  This keeps existing configs up-to-date
    # automatically when fields are renamed or added.
    if raw and (migrated or _needs_config_update(raw)):
        old_version = raw.get("general", {}).get("config_version", _CONFIG_VERSION)
        merged["general"]["config_version"] = _next_version(old_version)
        with path.open("w", encoding="utf-8") as fh:
            yaml.dump(merged, fh, default_flow_style=False, sort_keys=False)
        logger.info(
            "Config updated to version {} — added {} new fields",
            merged["general"]["config_version"],
            len(_config_keys(merged) - _config_keys(raw)),
        )

    # Convert any datetime.date objects back to ISO strings
    # (PyYAML parses ``2025-01-01`` as a date, but the model expects str)
    merged = _normalize_date_values(merged)
    return Config.model_validate(merged)
