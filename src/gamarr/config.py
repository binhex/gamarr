"""Configuration loading, validation, and default creation for gamarr."""

from __future__ import annotations

import contextlib
import copy
import datetime
import math
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, field_validator

from gamarr.utils import (
    EARLIEST_BROWSE_YEAR,
    current_year_month,
    is_valid_year_month,
    pages_to_months,
    subtract_months,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


_CONFIG_VERSION = "1.59.0"
_CONFIG_FILENAME = "gamarr.yml"

# Month bounds the migration may produce.  The pace mirrors the model's field cap so
# a converted value can always be validated; the floor is the catalogue's first month.
_MAX_MONTHS_PER_CYCLE = 1200
# The documented page budget a legacy key falls back to when its value is unusable.
_DEFAULT_PAGE_BUDGET = 500
# The per-cycle page pace a score sweep falls back to when the config says 0: one
# cycle must never crawl the catalogue.  The model default, the plan and the README
# all read this one value.
DEFAULT_SCORE_PAGES_PER_CYCLE = 20
_EARLIEST_MONTH = EARLIEST_BROWSE_YEAR * 100 + 1
# New-style keys an operator can plausibly write one level too high.
_STRAY_DISCOVERY_KEYS = ("latest", "backlog", "oldest_month", "months_per_cycle", "pages_per_cycle", "page_depth")


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


class ScheduleConfig(BaseModel):
    """Scheduling configuration for periodic tasks."""

    enabled: bool = False
    schedule_time_mins: int = Field(default=60, gt=0)
    run_on_start: bool = True
    # Hard watchdog budget for one acquisition cycle: a cycle that exceeds
    # this (e.g. a wedged browser) is aborted so the next scheduled run can
    # proceed. Bump this for very large fresh source indexes.
    acquisition_timeout_mins: int = Field(default=25, gt=0)


class SourceConfigEntry(BaseModel):
    """A single download source entry."""

    name: str = Field(exclude=True)  # hidden from YAML, populated from dict key
    enabled: bool = True
    platform: str = "pc"
    cache_hours: int = Field(default=6, gt=0, le=168)
    reject_keywords: list[str] = Field(default_factory=list)
    # ``None`` inherits the top-level window; a value here overrides it.
    source_matching_days: int | None = Field(default=None, ge=0)


class DownloadSitesConfig(RootModel[list[SourceConfigEntry]]):
    """Ordered list of download source configurations.

    Position in the list defines priority: earlier = higher priority.
    """

    root: list[SourceConfigEntry] = [
        SourceConfigEntry(name="fitgirl"),
        SourceConfigEntry(name="freegog"),
    ]

    @field_validator("root", mode="before")
    @classmethod
    def _parse_keyed_list(cls, v: Any) -> Any:
        """Convert [{'fitgirl': {...}}] into populated list.

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
                result.append(item)
                continue
            # Keyed format: {"fitgirl": {...}}
            for key, val in item.items():
                if isinstance(val, dict):
                    val["name"] = key
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


def validate_oldest_month(value: int) -> int:
    """Validate an ``oldest_month`` value and clamp a future month.

    ``0`` means "no floor".  A floor later than the current calendar month would
    read nothing, so it is clamped rather than rejected.

    Args:
        value: The configured ``YYYYMM`` floor, or 0.

    Returns:
        ``0`` when there is no floor, otherwise the validated (possibly clamped)
        month.

    Raises:
        ValueError: When *value* is neither 0 nor a valid ``YYYYMM`` month.
    """
    if not is_valid_year_month(value):
        raise ValueError(
            "oldest_month must be 0 (no floor) or YYYYMM with month 01-12 and year >= "
            f"{EARLIEST_BROWSE_YEAR}, e.g. 202401",
        )
    if value == 0:
        return 0
    current_month = current_year_month()
    if value > current_month:
        logger.warning("Config: oldest_month {} is in the future — clamping to {}", value, current_month)
        return current_month
    return value


class LatestDiscoveryConfig(BaseModel):
    """Month-paced settings for ``mode: latest`` (ADR 0003)."""

    model_config = ConfigDict(extra="forbid")

    oldest_month: int = 0
    months_per_cycle: int = Field(default=1, ge=0, le=_MAX_MONTHS_PER_CYCLE)

    @field_validator("oldest_month")
    @classmethod
    def _check_oldest_month(cls, value: int) -> int:
        return validate_oldest_month(value)


class BacklogNewConfig(BaseModel):
    """Date-paced settings for ``backlog`` with ``sort_order: new``."""

    model_config = ConfigDict(extra="forbid")

    oldest_month: int = 0
    # One month by default, matching ``latest``: with no floor and no pace a sweep
    # would read the whole catalogue in a single cycle (bug 2026-11-23).
    months_per_cycle: int = Field(default=1, ge=0, le=_MAX_MONTHS_PER_CYCLE)

    @field_validator("oldest_month")
    @classmethod
    def _check_oldest_month(cls, value: int) -> int:
        return validate_oldest_month(value)


class BacklogScoreConfig(BaseModel):
    """Floor- and page-paced settings for ``backlog`` with a score sort (ADR 0005, 0008).

    ``oldest_month`` is the reach of the sweep, read at year grain; ``pages_per_cycle``
    is a workload pace, not a reach.
    """

    model_config = ConfigDict(extra="forbid")

    oldest_month: int = 0
    pages_per_cycle: int = Field(default=DEFAULT_SCORE_PAGES_PER_CYCLE, ge=0)

    @field_validator("oldest_month")
    @classmethod
    def _check_oldest_month(cls, value: int) -> int:
        return validate_oldest_month(value)


class BacklogDiscoveryConfig(BaseModel):
    """Backlog blocks, selected by ``sort_order`` (ADR 0005)."""

    model_config = ConfigDict(extra="forbid")

    sort_order: Literal["new", "criticscore", "userscore"] = "new"
    new: BacklogNewConfig = Field(default_factory=BacklogNewConfig)
    score: BacklogScoreConfig = Field(default_factory=BacklogScoreConfig)


class DiscoveryConfig(BaseModel):
    """How one cycle walks the release list (ADR 0001)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["latest", "backlog"] = "latest"
    latest: LatestDiscoveryConfig = Field(default_factory=LatestDiscoveryConfig)
    backlog: BacklogDiscoveryConfig = Field(default_factory=BacklogDiscoveryConfig)


class MetacriticPlatformConfig(BaseModel):
    """Metacritic scoring, caching, and browse options for a single platform."""

    min_criticscore: int = 75
    min_criticscore_reviews: int = 10
    min_user_score: float = 7.5
    min_user_reviews: int = 10
    cache_details_days: int = 7
    cache_hours: int = 6
    enabled: bool = True
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    reject_genre: list[str] = Field(default_factory=list)
    reject_title: list[str] = Field(default_factory=list)  # case-insensitive substrings


class MetacriticConfig(BaseModel):
    """Metacritic integration options."""

    pending_order: Literal["new", "criticscore", "userscore"] = "new"
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

    processed_expiry_days: int = 365  # NOTE: currently unused — no history-pruning mechanism exists yet


class LibraryConfig(BaseModel):
    """Game library scanning settings.

    When ``paths`` is non-empty, library scanning is enabled.
    """

    paths: list[str] = Field(default_factory=list)


class ReviewSitesConfig(BaseModel):
    """Aggregated review site configurations."""

    metacritic: MetacriticConfig = Field(default_factory=MetacriticConfig)


class PostProcessConfig(BaseModel):
    """Post-processing settings for copying completed downloads to library."""

    post_process_enabled: bool = True
    schedule_time_mins: int = Field(default=5, gt=0, description="Polling interval in minutes (must be > 0).")
    run_on_start: bool = True
    library_path: str = ""
    copy_completed: bool = True
    remove_completed: bool = True
    max_seed_wait_hours: int = Field(
        default=168, ge=0, description="Fallback: delete after hours even if seeding. 0 = never."
    )
    exclude_file_min_kb: int = 0
    exclude_file_regex_list: list[str] = Field(default_factory=list)
    exclude_folder_regex_list: list[str] = Field(default_factory=list)
    path_case: Literal["pretty", "lowercase"] = "pretty"


class Config(BaseModel):
    """Root configuration model that aggregates all sub-configs."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    download_sites: DownloadSitesConfig = Field(
        default_factory=lambda: DownloadSitesConfig(
            root=[
                SourceConfigEntry(name="fitgirl"),
                SourceConfigEntry(name="freegog"),
            ]
        )
    )
    source_matching_days: int = Field(default=60, ge=0)
    review_sites: ReviewSitesConfig = Field(default_factory=ReviewSitesConfig)
    torrent_client: TorrentClientConfig = Field(default_factory=TorrentClientConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    library: LibraryConfig = Field(default_factory=LibraryConfig)
    post_process: PostProcessConfig = Field(default_factory=PostProcessConfig)


def _normalize_date_values(d: Any) -> Any:
    """Recursively convert datetime.date objects to ISO strings in dicts/lists.

    PyYAML parses ``2025-01-01`` as a ``datetime.date``, but the config
    model expects ``str``.  This catches that conversion at load time.
    """

    if isinstance(d, datetime.date):
        return d.isoformat()
    if isinstance(d, dict):
        return {k: _normalize_date_values(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_normalize_date_values(v) for v in d]
    return d


def _rename_config_key(
    mc_pc: dict[str, Any],
    old_key: str,
    new_key: str | None,
    platform_key: str,
    scope: str = "platform",
) -> bool:
    """Rename *old_key* to *new_key* in *mc_pc* if it exists, logging the migration.

    If both old and new keys exist, the existing new-key value is preserved
    and only the old key is cleaned up (does not overwrite the user's value).

    Args:
        mc_pc: The raw config section, mutated in place.
        old_key: The legacy key.
        new_key: The replacement key, or ``None`` to drop the legacy key.
        platform_key: The platform or source the section belongs to.
        scope: What kind of owner *platform_key* names, for the log line.

    Returns True if a key was renamed or removed.
    """
    if old_key not in mc_pc:
        return False
    if new_key == old_key:
        return False
    if new_key is None:
        del mc_pc[old_key]
        logger.info("Config: removed old key '{}' for {} '{}'", old_key, scope, platform_key)
    elif new_key in mc_pc:
        # Both exist — clean up old key, preserve the user's new-key value
        del mc_pc[old_key]
        logger.info(
            "Config: old key '{}' already migrated to '{}' for {} '{}' — keeping existing value",
            old_key,
            new_key,
            scope,
            platform_key,
        )
    else:
        mc_pc[new_key] = mc_pc.pop(old_key)
        logger.info("Config: migrated '{}'\u2192'{}' for {} '{}'", old_key, new_key, scope, platform_key)
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
        changed |= _rename_config_key(mc_pc, "max_cycle_weeks", "max_cycle_pages", platform_key)
        changed |= _rename_config_key(mc_pc, "max_weeks", "max_pages", platform_key)
    return changed


def _drop_metacritic_max_games(mc_pc: dict[str, Any], platform_key: str) -> bool:
    """Remove deprecated metacritic_max_games key. Returns True if changed."""
    if "metacritic_max_games" not in mc_pc:
        return False
    logger.info(
        "Config: removing deprecated 'metacritic_max_games' for platform '{}' — use discovery instead",
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
                "set the discovery blocks to control the scan window. Ignoring value.",
                old_key,
                platform_key,
            )
            mc_pc.pop(old_key)
            changed |= True
    for old_key in ("browse_cutoff_date", "metacritic_cutoff_date", "cutoff_date"):
        if old_key in mc_pc:
            logger.warning(
                "Config: '{}' is deprecated for platform '{}'; "
                "set 'discovery.backlog.new.oldest_month' or "
                "'discovery.backlog.score.oldest_month' instead. Ignoring value.",
                old_key,
                platform_key,
            )
            mc_pc.pop(old_key)
            changed |= True
    for old_key in ("age_recheck_weeks", "max_verify_attempts"):
        if old_key in mc_pc:
            logger.info(
                "Config: removing deprecated '{}' from platform '{}' — handled by source_matching_days",
                old_key,
                platform_key,
            )
            mc_pc.pop(old_key)
            changed |= True
    return changed


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
    # Both exist — deep-merge sources into download_sites and drop sources.
    old_sources = raw["sources"]
    if isinstance(old_sources, dict) and isinstance(raw["download_sites"], dict):
        raw["download_sites"] = _deep_merge(old_sources, raw["download_sites"])
        del raw["sources"]
        logger.info("Config: merged 'sources' into 'download_sites'")
    elif isinstance(old_sources, dict):
        del raw["sources"]
        logger.warning("Config: both 'sources' (dict) and 'download_sites' (list) exist — keeping download_sites")
    else:
        del raw["sources"]
        logger.warning("Config: dropped non-dict 'sources' value during migration")
    return True


def _legacy_source_config(parent: dict[str, Any], source_name: str) -> list[dict[str, Any]]:
    """Return zero or one named source configs from the legacy mapping layout."""
    exact = parent.get(source_name)
    if isinstance(exact, dict):
        return [exact]
    wanted = source_name.casefold()
    for key, candidate in parent.items():
        if isinstance(key, str) and key.casefold() == wanted and isinstance(candidate, dict):
            return [candidate]
    return []


def _source_configs_from_entry(entry: Any, wanted: str) -> list[dict[str, Any]]:
    """Return matching source dictionaries from one list entry."""
    if not isinstance(entry, dict):
        return []
    direct_name = entry.get("name")
    if isinstance(direct_name, str) and direct_name.casefold() == wanted:
        return [entry]
    return [
        value
        for key, value in entry.items()
        if isinstance(key, str) and key.casefold() == wanted and isinstance(value, dict)
    ]


def _source_config_dicts(raw: dict[str, Any], parent_key: str, source_name: str) -> list[dict[str, Any]]:
    """Return named source dictionaries from legacy and keyed-list layouts."""
    parent = raw.get(parent_key)
    if isinstance(parent, dict):
        return _legacy_source_config(parent, source_name)
    if not isinstance(parent, list):
        return []

    wanted = source_name.casefold()
    return [config for entry in parent for config in _source_configs_from_entry(entry, wanted)]


def _migrate_fitgirl_exclude_keywords(raw: dict[str, Any]) -> bool:
    """Rename fitgirl.exclude_keywords to reject_keywords.

    Checks under both old (sources) and new (download_sites) keys.
    Returns True if a migration was applied.
    """
    for parent_key in ("download_sites", "sources"):
        for fg in _source_config_dicts(raw, parent_key, "fitgirl"):
            if "exclude_keywords" not in fg:
                continue
            if "reject_keywords" not in fg:
                fg["reject_keywords"] = fg.pop("exclude_keywords")
                logger.info("Config: migrated '{}.fitgirl.exclude_keywords' to 'reject_keywords'", parent_key)
                return True
            del fg["exclude_keywords"]
            return True
    return False


def _migrate_days_since_release(raw: dict[str, Any]) -> bool:
    """Convert deprecated days_since_release to a floor month.

    The field meant "only review games released within N days", which is a window
    floor, so it becomes ``max_date`` (moved into a discovery block by the later
    step).  Converting it to a page count would have been re-converted a second
    time by that step, at a different days-per-page assumption.

    Returns True if a migration was applied.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if isinstance(mc_pc, dict) and "days_since_release" in mc_pc:
            days = mc_pc.pop("days_since_release")
            if isinstance(days, (int, float)) and days > 0 and "max_date" not in mc_pc and "max_pages" not in mc_pc:
                months_back = max(1, math.ceil(days / 30))
                mc_pc["max_date"] = _clamp_floor(subtract_months(current_year_month(), months_back), platform_key)
                logger.info(
                    "Config: converted days_since_release={} to max_date={} for platform '{}'",
                    days,
                    mc_pc["max_date"],
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
        for fg in _source_config_dicts(raw, parent_key, "fitgirl"):
            if _rename_pending_days(fg, f"{parent_key}.fitgirl"):
                changed = True
    return changed


def _migrate_fitgirl_cache_ttl_hours(raw: dict[str, Any]) -> bool:
    """Rename download_sites.fitgirl.cache_ttl_hours to cache_pages_hours.

    Returns True if a migration was applied.
    """
    changed = False
    for parent_key in ("download_sites", "sources"):
        for fg in _source_config_dicts(raw, parent_key, "fitgirl"):
            if "cache_ttl_hours" not in fg:
                continue
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


def _migrate_cutoff_weeks_to_max_pages(raw: dict[str, Any]) -> bool:
    """Rename cutoff_weeks to max_pages in metacritic.platform_overrides.

    Returns True if any migration was applied.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if not isinstance(mc_pc, dict) or "cutoff_weeks" not in mc_pc:
            continue
        cutoff_weeks = mc_pc.pop("cutoff_weeks")
        if "max_pages" not in mc_pc:
            mc_pc["max_pages"] = cutoff_weeks
            logger.info(
                "Config: renamed 'cutoff_weeks' to 'max_pages' for platform '{}'",
                platform_key,
            )
        else:
            logger.info(
                "Config: removed obsolete 'cutoff_weeks' for platform '{}' because max_date/max_pages already exist",
                platform_key,
            )
        changed = True
    return changed


def _migrate_remove_max_games(raw: dict[str, Any]) -> bool:
    """Remove max_games from metacritic.platform_overrides.

    max_games is no longer needed — max_pages controls the browse page limit.
    Returns True if any migration was applied.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if isinstance(mc_pc, dict) and "max_games" in mc_pc:
            del mc_pc["max_games"]
            logger.info(
                "Config: removed 'max_games' for platform '{}' — use discovery to control the scan window",
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
        for fg in _source_config_dicts(raw, parent_key, "fitgirl"):
            if _rename_recheck_days_in_dict(fg, f"{parent_key}.fitgirl"):
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
    legacy_metacritic = raw["metacritic"]
    current_metacritic = raw["review_sites"]["metacritic"]
    raw["review_sites"]["metacritic"] = _deep_merge(legacy_metacritic, current_metacritic)
    del raw["metacritic"]
    return True


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


def _is_dodi_entry(entry: dict[str, Any]) -> bool:
    """Check if a download_sites entry matches DODI in keyed or flat format.

    Keyed format: ``{"dodi": {...}}`` or ``{"DODI": {...}}``
    Flat format: ``{"name": "dodi", ...}``
    """
    for key in entry:
        if key.casefold() == "dodi":
            return True
    return str(entry.get("name", "")).casefold() == "dodi"


def _migrate_remove_dodi(raw: dict[str, Any]) -> bool:
    """Remove DODI entries from download_sites (DODI support removed).

    Scans ``download_sites`` for any DODI entry (keyed or flat format)
    and removes it.  Returns True if any entry was removed.
    """
    ds = raw.get("download_sites")
    if not isinstance(ds, list):
        return False

    changed = False
    # Remove in reverse order to preserve index correctness
    for i in range(len(ds) - 1, -1, -1):
        entry = ds[i]
        if isinstance(entry, dict) and _is_dodi_entry(entry):
            ds.pop(i)
            changed = True

    if changed:
        logger.info("Config: removed DODI entries from download_sites")
    return changed


def _strip_feed_url_from_entry(entry: dict[str, Any]) -> bool:
    """Pop feed_url and rss_url from a keyed-format entry's inner dict.

    Returns True if any keys were stripped.
    """
    changed = False
    for inner in entry.values():
        if isinstance(inner, dict):
            if inner.pop("feed_url", None) is not None:
                changed = True
            # Also strip any lingering rss_url (legacy edge case)
            if inner.pop("rss_url", None) is not None:
                changed = True
    return changed


def _migrate_remove_fitgirl_feed_url(raw: dict[str, Any]) -> bool:
    """Remove feed_url from all download_sites entries.

    The FitGirl sitemap URL is now hard-coded in fitgirl.py (FEED_URL
    constant), making this config field obsolete.  Stripping it keeps
    the config clean and consistent with FreeGOG which never had a URL
    field.

    Returns True if any feed_url entries were removed.
    """
    ds = raw.get("download_sites")
    if not isinstance(ds, list):
        return False

    changed = False
    for entry in ds:
        if isinstance(entry, dict) and _strip_feed_url_from_entry(entry):
            changed = True
    if changed:
        logger.info("Config: removed obsolete feed_url from download_sites")
    return changed


def _migrate_daemon_mode(raw: dict[str, Any]) -> bool:
    """Migrate deprecated general.daemon_mode to schedule.enabled.

    Returns True if a migration was applied.
    """
    general = raw.get("general", {})
    if general.get("daemon_mode") != "background":
        return False
    schedule = raw.setdefault("schedule", {})
    if "enabled" not in schedule:
        schedule["enabled"] = True
        logger.info("Config: migrated deprecated 'general.daemon_mode: background' to 'schedule.enabled: true'")
        return True
    return False


def _migrate_flatten_schedule_acquisition(raw: dict[str, Any]) -> bool:
    """Flatten schedule.acquisition.* into schedule.* directly.

    The old format nested enabled/schedule_time_mins/run_on_start under
    an ``acquisition`` sub-key that had no sibling keys.  Flattening
    removes unnecessary indirection.

    Returns True if a flattening was applied.
    """
    schedule = raw.get("schedule")
    if not isinstance(schedule, dict):
        return False
    acq = schedule.get("acquisition")
    if not isinstance(acq, dict):
        return False
    # Copy acquisition values up to schedule, preserving existing keys
    for key in ("enabled", "schedule_time_mins", "run_on_start"):
        if key not in schedule and key in acq:
            schedule[key] = acq[key]
    del schedule["acquisition"]
    logger.info("Config: flattened schedule.acquisition into schedule")
    return True


def _migrate_add_post_process_path_case(raw: dict[str, Any]) -> bool:
    """Add path_case field to post_process if missing.

    Returns True if the field was added.
    """
    pp = raw.get("post_process")
    if isinstance(pp, dict) and "path_case" not in pp:
        pp["path_case"] = "pretty"
        logger.info("Config: added post_process.path_case = 'pretty'")
        return True
    return False


def _migrate_add_freegog_to_download_sites(raw: dict[str, Any]) -> bool:
    """Ensure freegog is present in download_sites with full defaults.

    Adds freegog if missing, or upgrades an existing sparse entry.
    Returns True if freegog was added or upgraded.
    """
    ds = raw.get("download_sites")
    if not isinstance(ds, list):
        return False

    # No source_matching_days here: an unset per-source window inherits the
    # top-level default, so injecting one would override the operator's value.
    _freegog_defaults = {
        "enabled": True,
        "platform": "pc",
        "cache_hours": 6,
        "reject_keywords": [],
    }

    for entry in ds:
        if isinstance(entry, dict) and entry:
            name = list(entry.keys())[0]
            if name.casefold() == "freegog":
                return _upgrade_freegog_entry(entry[name], _freegog_defaults)

    # Not present at all — append to preserve existing source order
    # (typically FitGirl first, matching the Config class default).
    ds.append({"freegog": dict(_freegog_defaults)})
    logger.info("Config: added freegog to download_sites (appended)")
    return True


def _upgrade_freegog_entry(fg: dict[str, Any], defaults: dict[str, Any]) -> bool:
    """Upgrade a sparse freegog entry with missing default keys.

    Returns True if any keys were added.
    """
    if not isinstance(fg, dict):
        return False
    missing = [k for k in defaults if k not in fg]
    if not missing:
        return False
    for k in missing:
        fg[k] = defaults[k]
    logger.info("Config: upgraded freegog entry with {} missing fields", len(missing))
    return True


def _migrate_remove_search_mode(raw: dict[str, Any]) -> bool:
    """Remove deprecated search_mode from metacritic platform overrides.

    search_mode is no longer needed — the browse loop is unified.
    Returns True if any search_mode keys were removed.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, platform_config in overrides.items():
        if isinstance(platform_config, dict) and "search_mode" in platform_config:
            del platform_config["search_mode"]
            logger.info(
                "Config: removed deprecated 'search_mode' from platform '{}' — browse loop is unified",
                platform_key,
            )
            changed = True
    return changed


def _migrate_sort_order_critic_names(raw: dict[str, Any]) -> bool:
    """Rename critic-score keys/values to critic-style names.

    Renames ``min_metascore`` -> ``min_criticscore``,
    ``min_metascore_reviews`` -> ``min_criticscore_reviews``, and the
    ``sort_order`` value ``"metascore"`` -> ``"criticscore"`` for every
    platform override. Returns True if any migration was applied; the
    caller (load_config) then rewrites gamarr.yml in place, so the
    rename is naturally one-time.
    """
    changed = False
    review_sites = raw.get("review_sites")
    if not isinstance(review_sites, dict):
        return False
    metacritic = review_sites.get("metacritic")
    if not isinstance(metacritic, dict):
        return False
    overrides = metacritic.get("platform_overrides")
    if not isinstance(overrides, dict):
        return False
    for platform_key, mc_pc in overrides.items():
        if not isinstance(mc_pc, dict):
            continue
        changed |= _rename_config_key(mc_pc, "min_metascore", "min_criticscore", platform_key)
        changed |= _rename_config_key(mc_pc, "min_metascore_reviews", "min_criticscore_reviews", platform_key)
        if mc_pc.get("sort_order") == "metascore":
            mc_pc["sort_order"] = "criticscore"
            logger.info("Config: migrated sort_order 'metascore'->'criticscore' for platform '{}'", platform_key)
            changed = True
    return changed


def _rename_metacritic_cache_hours(raw: dict[str, Any]) -> bool:
    """Rename ``cache_pages_hours`` on every Metacritic platform override.

    Args:
        raw: The raw YAML config dict, mutated in place.

    Returns:
        True when at least one override key was renamed or cleaned up.
    """
    review_sites = raw.get("review_sites")
    if not isinstance(review_sites, dict):
        return False
    metacritic = review_sites.get("metacritic")
    if not isinstance(metacritic, dict):
        return False
    overrides = metacritic.get("platform_overrides")
    if not isinstance(overrides, dict):
        return False
    changed = False
    for platform_key, platform_cfg in overrides.items():
        if isinstance(platform_cfg, dict):
            changed |= _rename_config_key(platform_cfg, "cache_pages_hours", "cache_hours", str(platform_key))
    return changed


def _rename_source_cache_hours(raw: dict[str, Any]) -> bool:
    """Rename ``cache_pages_hours`` on every download-source entry.

    Args:
        raw: The raw YAML config dict, mutated in place.

    Returns:
        True when at least one source key was renamed or cleaned up.
    """
    download_sites = raw.get("download_sites")
    if not isinstance(download_sites, list):
        return False
    changed = False
    for entry in download_sites:
        if not isinstance(entry, dict):
            continue
        # An entry may carry several sources ({fitgirl: {...}, freegog: {...}}),
        # so rename every mapping body, not only the first key.
        for source_name, body in entry.items():
            if isinstance(body, dict):
                changed |= _rename_config_key(body, "cache_pages_hours", "cache_hours", str(source_name), "source")
    return changed


def _migrate_cache_pages_hours_to_cache_hours(raw: dict[str, Any]) -> bool:
    """Rename ``cache_pages_hours`` to ``cache_hours`` for every owner.

    The key exists on two different models — the Metacritic platform config
    (browse-page cache) and each download source (sitemap cache) — so both
    shapes are migrated here. Values are preserved unless both keys already
    exist, in which case ``cache_hours`` wins and the legacy key is dropped.

    Args:
        raw: The raw YAML config dict, mutated in place.

    Returns:
        True when at least one key was renamed or cleaned up.
    """
    changed = _rename_metacritic_cache_hours(raw)
    changed |= _rename_source_cache_hours(raw)
    return changed


_DISCOVERY_LEGACY_KEYS = ("mode", "max_pages", "max_cycle_pages", "max_date", "max_cycle_months", "sort_order")


def _coerce_int(value: int) -> int | None:
    """Return a non-negative int, or ``None`` for a negative one."""
    return value if value >= 0 else None


def _coerce_float(value: float) -> int | None:
    """Return an integral non-negative float as an int, else ``None``."""
    return int(value) if value >= 0 and value.is_integer() else None


def _coerce_digit_string(value: Any) -> int | None:
    """Return a sign-free decimal string as an int, else ``None``."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    negative = text.startswith("-")
    digits = text[1:] if negative else text
    if not digits.isdecimal():
        return None
    number = int(digits)
    return None if negative else number


def _coerce_count(value: Any) -> int | None:
    """Return *value* as a non-negative int, or ``None`` when it is not usable.

    Mirrors the coercion the old pydantic fields applied: an integral float or a
    numeric string was accepted before this change, so it is accepted here too.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return _coerce_int(value)
    if isinstance(value, float):
        return _coerce_float(value)
    return _coerce_digit_string(value)


def _pop_count(
    mapping: dict[str, Any],
    key: str,
    platform_key: str,
    *,
    keep_zero: bool = False,
) -> int | None:
    """Pop a legacy numeric key, reporting a discarded or disabled value.

    ``0`` means "unset" for the date keys, so it is dropped unless *keep_zero* is
    set.  The page keys keep it because ``0`` is a real budget there.

    Args:
        mapping: The raw platform override, mutated in place.
        key: The legacy key to pop.
        platform_key: The platform name, for log lines.
        keep_zero: True when ``0`` is a real value (the score page budget).

    Returns:
        The value when it is a usable count, otherwise ``None``.
    """
    if key not in mapping:
        return None
    value = _coerce_count(mapping.pop(key))
    if value is None:
        logger.warning("Config: '{}' on platform '{}' is not a usable count - ignored", key, platform_key)
        return None
    if value == 0 and not keep_zero:
        logger.info("Config: '{}' on platform '{}' is 0 (unset) - dropped", key, platform_key)
        return None
    return value


def _pop_floor(mapping: dict[str, Any], key: str, platform_key: str) -> int:
    """Pop a legacy month key, ignoring a value that is not a ``YYYYMM`` month.

    The date keys replaced page counters, so a config can carry a page-shaped
    number here.  Copying it through would build a floor the model rejects and
    abort the load, so the value is treated as unset instead.

    Args:
        mapping: The raw platform override, mutated in place.
        key: The legacy key to pop.
        platform_key: The platform name, for log lines.

    Returns:
        The month, or ``0`` when the key is absent or holds a non-month value.
    """
    value = _pop_count(mapping, key, platform_key)
    if value is None:
        return 0
    if not is_valid_year_month(value):
        logger.warning(
            "Config: '{}' on platform '{}' is {} - not a usable YYYYMM month ({} or later), ignored",
            key,
            platform_key,
            value,
            EARLIEST_BROWSE_YEAR,
        )
        return 0
    return value


def _clamp_pace(pace: int, platform_key: str) -> int:
    """Cap a converted month pace at the model's maximum."""
    if pace <= _MAX_MONTHS_PER_CYCLE:
        return pace
    logger.warning(
        "Config: converted pace {} exceeds the {} month cap for platform '{}' - clamped",
        pace,
        _MAX_MONTHS_PER_CYCLE,
        platform_key,
    )
    return _MAX_MONTHS_PER_CYCLE


def _clamp_floor(floor: int, platform_key: str) -> int:
    """Raise a converted floor to the earliest month the catalogue carries."""
    if floor >= _EARLIEST_MONTH:
        return floor
    logger.warning(
        "Config: converted floor {} is before the catalogue for platform '{}' - clamped to {}",
        floor,
        platform_key,
        _EARLIEST_MONTH,
    )
    return _EARLIEST_MONTH


def _place(
    dest: dict[str, Any],
    key: str,
    value: Any,
    legacy: str,
    platform_key: str,
) -> bool:
    """Set *dest[key]* unless the file already holds it, reporting the loser.

    Presence is what counts: the destination is the raw file, so a ``0`` there was
    written by the operator and means "no floor" / "no pace", not "unset".

    Args:
        dest: The block to fill.
        key: The destination key.
        value: The converted value.
        legacy: The legacy key name, for the report.
        platform_key: The platform name, for log lines.

    Returns:
        True when the value was written.
    """
    if key in dest:
        logger.info("Config: legacy '{}' ignored for platform '{}' - '{}' is already set", legacy, platform_key, key)
        return False
    dest[key] = value
    return True


def _resolve_selector(
    block: dict[str, Any],
    key: str,
    legacy: str,
    platform_key: str,
    *,
    report: bool = True,
) -> str:
    """Return the block's selector, writing the legacy one only when the block is unset.

    Args:
        block: The block that holds the selector.
        key: ``mode`` or ``sort_order``.
        legacy: The legacy value.
        platform_key: The platform name, for log lines.
        report: False when the legacy key was absent, so nothing was discarded.

    Returns:
        The selector the cycle will actually run.
    """
    existing = block.get(key)
    if existing is None:
        block[key] = legacy
        return legacy
    if existing != legacy and report:
        logger.info(
            "Config: legacy '{}' value '{}' ignored for platform '{}' - '{}' is already '{}'",
            key,
            legacy,
            platform_key,
            key,
            existing,
        )
    return str(existing)


def _place_dates(dest: dict[str, Any], max_date: int, months: int, platform_key: str) -> bool:
    """Write the two date keys into *dest*, skipping the disabled values.

    Args:
        dest: The block to fill.
        max_date: The legacy floor month, ``0`` when disabled.
        months: The legacy month pace, ``0`` when disabled.
        platform_key: The platform name, for log lines.

    Returns:
        True when a legacy floor was written.
    """
    landed = False
    if max_date:
        landed = _place(dest, "oldest_month", max_date, "max_date", platform_key)
    if months:
        _place(dest, "months_per_cycle", months, "max_cycle_months", platform_key)
    return landed


def _depth_driven_bounds(max_date: int, months: int) -> bool:
    """Return True when a legacy latest cycle had no date bound at all."""
    return not max_date and not months


def _migrate_latest_block(
    latest: dict[str, Any],
    max_date: int,
    months: int,
    pages: int | None,
    cycle_pages: int | None,
    platform_key: str,
) -> None:
    """Move a legacy latest cycle's bounds into the latest block (ADR 0003).

    A page depth converts to a month pace only when it was the cycle's only bound;
    with a floor or a pace set it was inert and is dropped.

    Args:
        latest: The ``discovery.latest`` block, mutated in place.
        max_date: The legacy floor month, ``0`` when disabled.
        months: The legacy month pace, ``0`` when disabled.
        pages: The legacy page depth, or ``None``.
        cycle_pages: The legacy per-cycle page pace, or ``None``.
        platform_key: The platform name, for log lines.
    """
    floor_landed = _place_dates(latest, max_date, months, platform_key)
    if floor_landed and not months and "months_per_cycle" not in latest:
        # The old floored latest cycle read its whole window; keep that meaning
        # rather than letting the 1-month model default silently shorten it.
        latest["months_per_cycle"] = 0
    if pages is not None:
        if _depth_driven_bounds(max_date, months):
            pace = _clamp_pace(pages_to_months(pages), platform_key)
            if _place(latest, "months_per_cycle", pace, "max_pages", platform_key):
                logger.info(
                    "Config: converted max_pages {} to months_per_cycle {} for platform '{}' (approximate)",
                    pages,
                    pace,
                    platform_key,
                )
        else:
            logger.info("Config: max_pages {} is not read in latest mode for platform '{}'", pages, platform_key)
    if cycle_pages is not None:
        logger.info(
            "Config: max_cycle_pages {} is not read in latest mode for platform '{}'",
            cycle_pages,
            platform_key,
        )


def _migrate_backlog_new_pages(
    new_block: dict[str, Any],
    pages: int | None,
    cycle_pages: int | None,
    platform_key: str,
) -> None:
    """Convert a legacy page budget and pace into dated bounds (ADR 0007).

    Args:
        new_block: The ``discovery.backlog.new`` block, mutated in place.
        pages: The legacy total page budget, or ``None``.
        cycle_pages: The legacy per-cycle page pace, or ``None``.
        platform_key: The platform name, for log lines.
    """
    if pages is not None:
        floor = _clamp_floor(subtract_months(current_year_month(), pages_to_months(pages)), platform_key)
        if _place(new_block, "oldest_month", floor, "max_pages", platform_key):
            logger.info(
                "Config: converted max_pages {} to oldest_month {} for platform '{}' (approximate)",
                pages,
                floor,
                platform_key,
            )
    if cycle_pages is not None:
        pace = _clamp_pace(pages_to_months(cycle_pages), platform_key)
        if _place(new_block, "months_per_cycle", pace, "max_cycle_pages", platform_key):
            logger.info(
                "Config: converted max_cycle_pages {} to months_per_cycle {} for platform '{}' (approximate)",
                cycle_pages,
                pace,
                platform_key,
            )


def _land_score_floor(
    score: dict[str, Any],
    pages: int,
    platform_key: str,
    *,
    legacy: str = "page_depth",
) -> bool:
    """Convert a legacy page budget into the score block's floor month.

    A score sweep states its reach as a month floor, so a page budget becomes the
    month that many pages reach back to.  ``0`` means "no budget", not "a floor of
    now", so it writes nothing.

    Args:
        score: The ``discovery.backlog.score`` block, mutated in place.
        pages: The legacy total page budget.
        platform_key: The platform name, for log lines.
        legacy: The key the budget came from, for the refusal report.

    Returns:
        True when the floor was written.
    """
    if pages <= 0:
        return False
    floor = _clamp_floor(subtract_months(current_year_month(), pages_to_months(pages)), platform_key)
    if not _place(score, "oldest_month", floor, legacy, platform_key):
        return False
    logger.info(
        "Config: converted page budget {} to oldest_month {} for platform '{}' (approximate)",
        pages,
        floor,
        platform_key,
    )
    return True


def _migrate_backlog_score_block(
    score: dict[str, Any],
    pages: int | None,
    cycle_pages: int | None,
    platform_key: str,
) -> None:
    """Convert the legacy page keys into the score block's floor and pace (ADR 0005).

    The total page budget becomes the sweep's floor month; ``0`` is a real value
    here ("no budget"), so it writes no floor.  The per-cycle page pace crosses
    unchanged, because it stays a workload budget.

    Args:
        score: The ``discovery.backlog.score`` block, mutated in place.
        pages: The legacy total page budget, or ``None``.
        cycle_pages: The legacy per-cycle page pace, or ``None``.
        platform_key: The platform name, for log lines.
    """
    if pages is not None:
        _land_score_floor(score, pages, platform_key, legacy="max_pages")
    if cycle_pages is not None:
        _place(score, "pages_per_cycle", cycle_pages, "max_cycle_pages", platform_key)


def _ensure_block(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``parent[key]`` as a mapping, replacing a non-mapping value."""
    block = parent.get(key)
    if not isinstance(block, dict):
        block = {}
        parent[key] = block
    return block


def _pop_legacy_bounds(plat: dict[str, Any], platform_key: str) -> dict[str, Any]:
    """Pop the flat discovery keys and record which selectors were present.

    ``mode_present`` and ``sort_present`` are read before the pops, because the
    presence of a legacy selector decides whether an existing block's value
    discarded something the operator wrote.
    """
    existing_block = isinstance(plat.get("discovery"), dict)
    return {
        "mode_present": "mode" in plat,
        "sort_present": "sort_order" in plat,
        "mode": plat.pop("mode", "latest" if existing_block else "backlog"),
        "sort_order": plat.pop("sort_order", "new"),
        "max_date": _pop_floor(plat, "max_date", platform_key),
        "months": _pop_count(plat, "max_cycle_months", platform_key) or 0,
        "pages": _pop_count(plat, "max_pages", platform_key, keep_zero=True),
        "cycle_pages": _pop_count(plat, "max_cycle_pages", platform_key, keep_zero=True),
    }


def _migrate_add_discovery_blocks(raw: dict[str, Any]) -> bool:
    """Give every platform override the discovery block when it has none.

    ``_default_config_dict`` dumps the model, whose overrides are ``{"pc"}`` only, so
    a second platform (``ps5``) would never gain the keys from the merge.  Adding them
    here makes the rewrite write the block for every configured platform.

    Args:
        raw: The raw config, mutated in place.

    Returns:
        True when any block was added.
    """
    overrides, _ = _discovery_overrides(raw)
    added = False
    for platform_key, plat in overrides.items():
        if not isinstance(plat, dict) or ("discovery" in plat and plat["discovery"] != {}):
            continue
        plat["discovery"] = DiscoveryConfig().model_dump()
        logger.info("Config: added the discovery block to platform '{}'", platform_key)
        added = True
    return added


def _migrate_pending_order(metacritic: dict[str, Any], sort_order: str, platform_key: str) -> None:
    """Carry a legacy ``sort_order`` into the shared pending order."""
    existing = metacritic.get("pending_order")
    if existing is None:
        metacritic["pending_order"] = sort_order
    elif existing != sort_order:
        logger.info(
            "Config: platform '{}' sort_order '{}' ignored - pending_order is '{}'",
            platform_key,
            sort_order,
            existing,
        )


def _migrate_latest_target(discovery: dict[str, Any], values: dict[str, Any], platform_key: str) -> None:
    """Move a legacy latest cycle into ``discovery.latest`` (ADR 0003)."""
    latest = _ensure_block(discovery, "latest")
    _migrate_latest_block(
        latest,
        values["max_date"],
        values["months"],
        values["pages"],
        values["cycle_pages"] or None,
        platform_key,
    )
    if "oldest_month" in latest or "months_per_cycle" in latest:
        return
    # The legacy cycle was bounded by the old default page depth, so keep that
    # reach instead of letting the 1-month model default shrink it.
    reach = pages_to_months(_DEFAULT_PAGE_BUDGET)
    _place(latest, "months_per_cycle", reach, "max_pages", platform_key)
    logger.info(
        "Config: no legacy bound on platform '{}' - kept the old default reach of {} month(s)",
        platform_key,
        reach,
    )


def _migrate_backlog_target(discovery: dict[str, Any], values: dict[str, Any], platform_key: str) -> None:
    """Move a legacy backlog cycle into its per-sort blocks (ADR 0005, 0007)."""
    backlog = _ensure_block(discovery, "backlog")
    target_sort = _resolve_selector(
        backlog,
        "sort_order",
        values["sort_order"],
        platform_key,
        report=values["sort_present"],
    )
    new_block = _ensure_block(backlog, "new")
    floor_present = "oldest_month" in new_block
    _place_dates(new_block, values["max_date"], values["months"], platform_key)
    if target_sort != "new":
        # Page keys convert into the score block on this path, so no pace can land in
        # `new` after this point.
        _keep_migrated_floor_unpaced(new_block, platform_key, floor_present)
        score = _ensure_block(backlog, "score")
        _migrate_backlog_score_block(score, values["pages"], values["cycle_pages"], platform_key)
        if values["pages"] is None and "oldest_month" not in score:
            # No bound was expressed, so the old runtime ran on the model's default page
            # budget.  Keep that reach instead of leaving the sweep floorless, exactly as
            # the `new` target below does (ADR 0007: an upgrade neither narrows nor widens).
            floor = _default_floor()
            score["oldest_month"] = floor
            logger.info(
                "Config: no legacy page bound for platform '{}' - kept the old default reach (oldest_month {})",
                platform_key,
                floor,
            )
        return
    _migrate_backlog_new_pages(
        new_block,
        values["pages"] or None,
        values["cycle_pages"] or None,
        platform_key,
    )
    # Guard last, after the page conversion had its chance to write the legacy pace.
    if not values["max_date"] and "oldest_month" not in new_block:
        # No floor was expressed.  An absent default (or an explicit 0, which the old
        # runtime read as an uncapped ten-year window) keeps the documented default
        # reach, instead of leaving the floor unset (a catalogue-wide sweep).
        floor = _default_floor()
        new_block["oldest_month"] = floor
        logger.info(
            "Config: no legacy floor on platform '{}' - kept the old default reach (oldest_month {})",
            platform_key,
            floor,
        )
    _keep_migrated_floor_unpaced(new_block, platform_key, floor_present)


def _keep_migrated_floor_unpaced(
    new_block: dict[str, Any],
    platform_key: str,
    floor_present: bool,
) -> None:
    """Write ``months_per_cycle: 0`` when the migration landed a floor but no pace.

    The legacy bounds came from a cycle that read to its floor in one sweep, so the key
    is written explicitly: leaving it absent would inherit the 1-month model default and
    narrow the reach the migration promises to keep.  A floor the operator already set
    is left alone - it keeps the documented 1-month default.

    Args:
        new_block: The ``discovery.backlog.new`` block, mutated in place.
        platform_key: The platform name, for log lines.
        floor_present: Whether the block already carried ``oldest_month`` before the
            migration ran.
    """
    if floor_present or "oldest_month" not in new_block or "months_per_cycle" in new_block:
        return
    new_block["months_per_cycle"] = 0
    logger.info("Config: kept platform '{}' unpaced to its floor (months_per_cycle 0)", platform_key)


def _migrate_platform_discovery(
    plat: dict[str, Any],
    metacritic: dict[str, Any],
    platform_key: str,
) -> None:
    """Move one platform override's flat discovery keys into blocks.

    ``0`` means "disabled" for the date keys, but it is a real value for the score
    page budget, so the page keys are carried through and each target decides.

    Args:
        plat: The raw platform override, mutated in place.
        metacritic: The raw ``review_sites.metacritic`` mapping.
        platform_key: The platform name, for log lines.
    """
    values = _pop_legacy_bounds(plat, platform_key)
    discovery = _ensure_block(plat, "discovery")
    # A block that already exists means the new shape was written: the selector then
    # falls back to the model default rather than to the legacy one, so a leftover
    # flat key cannot flip the mode the block shows.
    target_mode = _resolve_selector(
        discovery,
        "mode",
        values["mode"],
        platform_key,
        report=values["mode_present"],
    )
    logger.info(
        "Config: platform '{}' migrated to discovery (mode '{}', floor {}, pace {}, page budget {})",
        platform_key,
        target_mode,
        values["max_date"] or "none",
        values["months"] or "none",
        values["pages"] if values["pages"] is not None else "none",
    )
    _migrate_pending_order(metacritic, values["sort_order"], platform_key)
    if target_mode == "latest":
        _migrate_latest_target(discovery, values, platform_key)
    else:
        _migrate_backlog_target(discovery, values, platform_key)
    _prune_empty_blocks(discovery)


def _prune_empty_blocks(discovery: dict[str, Any]) -> None:
    """Drop the sub-blocks a migration created but never filled."""
    for key in ("latest", "backlog"):
        block = discovery.get(key)
        if not isinstance(block, dict):
            continue
        for sub in ("new", "score"):
            if block.get(sub) == {}:
                del block[sub]
        if not block:
            del discovery[key]


def _discovery_overrides(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the raw ``(platform_overrides, metacritic)``, or empty mappings.

    Args:
        raw: The raw config.

    Returns:
        The overrides table and the metacritic mapping; both empty when the shape
        is absent, so callers need no further guards.
    """
    review_sites = raw.get("review_sites")
    metacritic = review_sites.get("metacritic") if isinstance(review_sites, dict) else None
    if not isinstance(metacritic, dict):
        return {}, {}
    overrides = metacritic.get("platform_overrides")
    if not isinstance(overrides, dict):
        return {}, metacritic
    return overrides, metacritic


def _has_legacy_discovery_keys(plat: Any) -> bool:
    """Return True when *plat* still carries a flat discovery key."""
    if not isinstance(plat, dict):
        return False
    return any(key in plat for key in _DISCOVERY_LEGACY_KEYS)


def _migrate_discovery_blocks(raw: dict[str, Any]) -> bool:
    """Move the flat discovery keys into per-mode blocks.

    Every key is migrated per key: a key whose destination already holds a value is
    reported and dropped, and the rest still land.  A platform that already has a
    block therefore keeps it while its leftover legacy keys are consumed.

    Args:
        raw: The raw config, mutated in place.

    Returns:
        True when at least one platform was migrated.
    """
    overrides, metacritic = _discovery_overrides(raw)
    preferred = _fitgirl_platform(raw)
    ordered = sorted(overrides.items(), key=lambda item: str(item[0]).casefold() != preferred.casefold())
    changed = False
    for platform_key, plat in ordered:
        if not _has_legacy_discovery_keys(plat):
            continue
        _migrate_platform_discovery(plat, metacritic, platform_key)
        changed = True
    return changed


def _migrate_score_page_depth(raw: dict[str, Any]) -> bool:
    """Convert a ``page_depth`` an earlier release wrote inside the score block.

    The released model wrote the page budget next to ``pages_per_cycle``, so a live
    config carries it inside an already-block-shaped score block.  It converts to
    the floor like any other page budget and is removed, so no file keeps it.

    Args:
        raw: The raw config, mutated in place.

    Returns:
        True when at least one platform's score block carried the key.
    """
    overrides, _ = _discovery_overrides(raw)
    changed = False
    for platform_key, plat in overrides.items():
        discovery = plat.get("discovery") if isinstance(plat, dict) else None
        backlog = discovery.get("backlog") if isinstance(discovery, dict) else None
        score = backlog.get("score") if isinstance(backlog, dict) else None
        if not isinstance(score, dict):
            continue
        if "page_depth" not in score:
            # A block that states no reach at all is a pre-floor file: the old runtime
            # ran it on the model's default page budget, so keep that reach instead of
            # leaving the sweep floorless (ADR 0007).  A block this release writes always
            # carries ``oldest_month``, so this cannot capture a new config.
            #
            # A file that still carries flat legacy keys is skipped here: its own budget
            # must convert first (``_migrate_backlog_target`` runs after this step and
            # writes the same default only when the file stated no bound).
            if "oldest_month" in score or _has_legacy_discovery_keys(plat):
                continue
            floor = _default_floor()
            score["oldest_month"] = floor
            logger.info(
                "Config: no floor in the score block for platform '{}' - kept the old default reach (oldest_month {})",
                platform_key,
                floor,
            )
            changed = True
            continue
        # The released model's ``int`` field accepted an integral float or a numeric
        # string, so this path uses the same coercion as ``_pop_count``; a value it
        # cannot use is reported and dropped instead of aborting the load.
        value = _coerce_count(score.pop("page_depth"))
        if value is None:
            floor = _default_floor()
            score["oldest_month"] = floor
            logger.warning(
                "Config: legacy 'page_depth' for platform '{}' is not a usable page count - ignored, "
                "keeping the old default reach (oldest_month {})",
                platform_key,
                floor,
            )
        elif value == 0:
            # An explicit 0 said "no budget": record it, so the flat-key default below
            # cannot later read the missing floor as "nothing was expressed".
            _place(score, "oldest_month", 0, "page_depth", platform_key)
        else:
            _land_score_floor(score, value, platform_key)
        changed = True
    return changed


def _default_floor() -> int:
    """Return the floor month the documented default page budget covers."""
    return _clamp_floor(subtract_months(current_year_month(), pages_to_months(_DEFAULT_PAGE_BUDGET)), "default")


_LEGACY_SOURCE_WINDOW_DEFAULT = 60


def _rename_source_window(settings: dict[str, Any], label: str) -> bool:
    """Rename one source entry's ``max_queue_days`` in place."""
    if "max_queue_days" not in settings:
        return False
    if _is_negative_count(settings["max_queue_days"]) and "source_matching_days" not in settings:
        settings.pop("max_queue_days")
        settings["source_matching_days"] = 0
        logger.info("Config: '{}' max_queue_days is negative - migrated to 0 (never expire)", label)
        return True
    if settings["max_queue_days"] == _LEGACY_SOURCE_WINDOW_DEFAULT and "source_matching_days" not in settings:
        settings.pop("max_queue_days")
        logger.info(
            "Config: '{}' max_queue_days was the old default {} - dropped, so it inherits the top-level window",
            label,
            _LEGACY_SOURCE_WINDOW_DEFAULT,
        )
        return True
    if "source_matching_days" in settings:
        settings.pop("max_queue_days")
        logger.info("Config: '{}' max_queue_days ignored — source_matching_days already set", label)
    else:
        settings["source_matching_days"] = settings.pop("max_queue_days")
        logger.info("Config: renamed '{}' max_queue_days to source_matching_days", label)
    return True


def _migrate_source_entry(entry: Any) -> bool:
    """Rename the window key on one ``download_sites`` entry.

    Args:
        entry: One raw list element, in keyed or flat legacy form.

    Returns:
        True when a legacy key was found.
    """
    if not isinstance(entry, dict):
        return False
    changed = False
    for name, settings in entry.items():
        if isinstance(settings, dict):
            changed |= _rename_source_window(settings, str(name))
    return changed


def _migrate_source_entries(raw: dict[str, Any]) -> bool:
    """Rename the window key on every ``download_sites`` entry.

    Args:
        raw: The raw config, mutated in place.

    Returns:
        True when any entry carried a legacy key.
    """
    entries = raw.get("download_sites")
    if not isinstance(entries, list):
        return False
    changed = False
    for entry in entries:
        changed |= _migrate_source_entry(entry)
    return changed


def _fitgirl_platform(raw: dict[str, Any]) -> str:
    """Return the platform the FitGirl source targets, defaulting to ``pc``."""
    entries = raw.get("download_sites")
    if not isinstance(entries, list):
        return "pc"
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for name, settings in entry.items():
            if str(name).casefold() == "fitgirl" and isinstance(settings, dict):
                return str(settings.get("platform", "pc"))
    return "pc"


_LEGACY_PLATFORM_WINDOW_DEFAULT = 30


def _is_negative_count(value: Any) -> bool:
    """Return True when *value* is a negative number in any accepted spelling."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value < 0
    if isinstance(value, str):
        text = value.strip()
        return text.startswith("-") and text[1:].isdecimal()
    return False


def _window_from(value: Any, platform_key: str) -> int | None:
    """Coerce a legacy queue window, mapping a negative to "never expire" (0)."""
    coerced = _coerce_count(value)
    if coerced is None and _is_negative_count(value):
        logger.info("Config: platform '{}' max_queue_days {} means never expire - migrated to 0", platform_key, value)
        return 0
    if coerced is None:
        logger.warning("Config: platform '{}' max_queue_days is not a usable count - ignored", platform_key)
        return None
    if coerced == _LEGACY_PLATFORM_WINDOW_DEFAULT:
        logger.info(
            "Config: platform '{}' max_queue_days {} was the old default - dropped, so the top-level window applies",
            platform_key,
            value,
        )
        return None
    return coerced


def _entry_states_a_window(entry: Any) -> bool:
    """Return True when one download-site entry sets its own queue window."""
    if not isinstance(entry, dict):
        return False
    settings_list = [entry] if "name" in entry else list(entry.values())
    for settings in settings_list:
        if not isinstance(settings, dict):
            continue
        if "max_queue_days" in settings or "source_matching_days" in settings:
            return True
    return False


def _source_states_a_window(raw: dict[str, Any]) -> bool:
    """Return True when any download source sets its own queue window."""
    entries = raw.get("download_sites")
    if not isinstance(entries, list):
        return False
    return any(_entry_states_a_window(entry) for entry in entries)


def _migrate_platform_windows(raw: dict[str, Any], source_has_window: bool) -> bool:
    """Hoist a platform-level ``max_queue_days`` to the top level (ADR 0004).

    The old model declared the window on every platform override, so that is where
    an operator's value really lived; the metacritic-level branch below is kept for
    the shape the spec named.

    Args:
        raw: The raw config, mutated in place.

    Returns:
        True when any platform carried the legacy key.
    """
    overrides, _ = _discovery_overrides(raw)
    preferred = _fitgirl_platform(raw)
    ordered = sorted(overrides.items(), key=lambda item: str(item[0]).casefold() != preferred.casefold())
    changed = False
    for platform_key, plat in ordered:
        if not isinstance(plat, dict) or "max_queue_days" not in plat:
            continue
        value = _window_from(plat.pop("max_queue_days"), platform_key)
        if value is None:
            changed = True
            continue
        if source_has_window:
            logger.info("Config: platform '{}' max_queue_days ignored - a source sets its own window", platform_key)
            changed = True
            continue
        if "source_matching_days" in raw:
            logger.info("Config: platform '{}' max_queue_days ignored - source_matching_days already set", platform_key)
        else:
            raw["source_matching_days"] = value
            logger.info("Config: hoisted platform '{}' max_queue_days to top-level source_matching_days", platform_key)
        changed = True
    return changed


def _migrate_metacritic_window(raw: dict[str, Any], source_has_window: bool) -> bool:
    """Hoist the metacritic-side window to the top level (ADR 0004).

    Args:
        raw: The raw config, mutated in place.

    Returns:
        True when the legacy key was found.
    """
    review_sites = raw.get("review_sites")
    metacritic = review_sites.get("metacritic") if isinstance(review_sites, dict) else None
    if not isinstance(metacritic, dict) or "max_queue_days" not in metacritic:
        return False
    fallback = _window_from(metacritic.pop("max_queue_days"), "metacritic")
    if fallback is None:
        return True
    if source_has_window:
        logger.info("Config: metacritic max_queue_days ignored - a source sets its own window")
        return True
    if "source_matching_days" in raw:
        logger.info("Config: legacy metacritic max_queue_days ignored — source_matching_days already set")
        return True
    raw["source_matching_days"] = fallback
    logger.info("Config: hoisted metacritic max_queue_days to top-level source_matching_days")
    return True


def _migrate_source_matching_days(raw: dict[str, Any]) -> bool:
    """Collapse the three queue-window keys into one (ADR 0004).

    Args:
        raw: The raw config, mutated in place.

    Returns:
        True when any legacy key was found.
    """
    source_has_window = _source_states_a_window(raw)
    sources_changed = _migrate_source_entries(raw)
    platforms_changed = _migrate_platform_windows(raw, source_has_window)
    metacritic_changed = _migrate_metacritic_window(raw, source_has_window)
    return sources_changed or platforms_changed or metacritic_changed


def _migrate_config(raw: dict[str, Any]) -> bool:
    """Migrate renamed config keys in-place for all platforms.

    Returns True if any migration was applied.
    """
    original = copy.deepcopy(raw)
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
            _migrate_cutoff_weeks_to_max_pages,
            _migrate_days_since_release,
            _migrate_pending_days_to_max_queue_days,
            _migrate_recheck_days_to_max_queue_days,
            _migrate_metacritic_exclude_keywords,
            _migrate_remove_max_games,
            _migrate_download_sites_to_ordered,
            _migrate_download_sites_to_keyed_list,
            _migrate_remove_dodi,
            _migrate_remove_fitgirl_feed_url,
            # _migrate_flatten_schedule_acquisition must run BEFORE
            # _migrate_daemon_mode.  If daemon_mode runs first and sets
            # schedule.enabled=True, flatten would skip copying an
            # existing acquisition.enabled=False (key already present),
            # silently overriding the user's explicit setting.
            _migrate_flatten_schedule_acquisition,
            _migrate_daemon_mode,
            _migrate_add_post_process_path_case,
            # _migrate_cache_pages_hours_to_cache_hours must run BEFORE
            # _migrate_add_freegog_to_download_sites: the freegog defaults
            # injection now writes cache_hours, so a still-unrenamed legacy
            # key would look "already migrated" and its value would be
            # discarded in favour of the default.
            _migrate_cache_pages_hours_to_cache_hours,
            _migrate_add_freegog_to_download_sites,
            _migrate_remove_search_mode,
            _migrate_sort_order_critic_names,
            # Before the block migration: a score block that still carries the released
            # `page_depth` must convert its own budget first, so the flat-key path and the
            # no-bound default cannot pre-empt it with a different reach.
            _migrate_score_page_depth,
            _migrate_discovery_blocks,
            _migrate_source_matching_days,
            _migrate_add_discovery_blocks,
        ]
        for fn in _migrations:
            if fn(raw):
                changed = True
        return changed
    except Exception as exc:
        raw.clear()
        raw.update(original)
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
    # Re-include name in download_sites entries (excluded by Field(exclude=True)).
    d["download_sites"] = [
        {entry.name: entry.model_dump(exclude={"name"}, exclude_none=True)} for entry in cfg.download_sites
    ]
    return d


def _config_file_path(config_path: str | Path) -> Path:
    """Resolve a config file path from either a file or directory input."""
    path = Path(config_path)
    if path.exists():
        return path / _CONFIG_FILENAME if path.is_dir() else path
    if not path.suffix:
        return path / _CONFIG_FILENAME
    return path


def create_default_config(config_path: str | Path) -> None:
    """Write a default YAML config file if one does not already exist."""
    path = _config_file_path(config_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create config directory '{}': {}", path.parent, exc)
        return
    if path.exists():
        return
    try:
        with path.open("w", encoding="utf-8") as fh:
            yaml.dump(_default_config_dict(), fh, default_flow_style=False, sort_keys=False)
    except OSError as exc:
        logger.warning("Could not write default config '{}': {}", path, exc)


def _resolve_config_path(config_path: str | Path) -> Path:
    """Resolve *config_path*, create a default, and require a regular file."""
    path = _config_file_path(config_path)
    if not path.exists():
        create_default_config(path)
    if not path.is_file():
        raise ValueError(f"Config path '{path}' is not a file.")
    return path


def _load_raw_config(path: Path) -> dict[str, Any]:
    """Load YAML from *path* and require a mapping at its root."""
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Config file '{path}' must be a YAML mapping (got {type(loaded).__name__}).")
    return loaded


_CONFIG_MAPPING_SECTIONS = (
    "general",
    "schedule",
    "review_sites",
    "torrent_client",
    "notification",
    "database",
    "library",
    "post_process",
)


def _mapping_sections_are_rewritable(raw: dict[str, Any]) -> bool:
    """Return whether optional top-level mapping sections have safe shapes."""
    for key in _CONFIG_MAPPING_SECTIONS:
        if key not in raw:
            continue
        value = raw[key]
        if value is not None and not isinstance(value, dict):
            return False
    return True


def _review_sites_shape_is_rewritable(review_sites: Any) -> bool:
    """Return whether nested Metacritic sections have safe mapping shapes."""
    if not isinstance(review_sites, dict):
        return True
    metacritic = review_sites.get("metacritic")
    if metacritic is None:
        return True
    if not isinstance(metacritic, dict):
        return False
    overrides = metacritic.get("platform_overrides")
    return overrides is None or isinstance(overrides, dict)


def _config_shape_is_rewritable(raw: dict[str, Any]) -> bool:
    """Return whether raw config sections have safe shapes for persistence."""
    if not _mapping_sections_are_rewritable(raw):
        return False
    download_sites = raw.get("download_sites")
    if download_sites is not None and not isinstance(download_sites, (dict, list)):
        return False
    return _review_sites_shape_is_rewritable(raw.get("review_sites"))


def _write_config_atomically(path: Path, config: dict[str, Any]) -> None:
    """Write *config* atomically while preserving an existing file mode."""
    temp_path: Path | None = None
    try:
        original_mode: int | None
        try:
            original_mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            original_mode = None
        file_descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temp_path = Path(temp_name)
        os.close(file_descriptor)
        with temp_path.open("w", encoding="utf-8") as fh:
            yaml.dump(config, fh, default_flow_style=False, sort_keys=False)
            fh.flush()
        if original_mode is not None:
            os.chmod(temp_path, original_mode)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)


def _rewrite_config_if_needed(
    path: Path,
    raw: dict[str, Any],
    merged: dict[str, Any],
    migrated: bool,
) -> str | None:
    """Persist *merged* when migration or missing default keys require an update.

    Args:
        path: The config file to rewrite.
        raw: The file exactly as it was read, used to decide whether anything changed.
        merged: The validated config to write back.
        migrated: Whether a migration step changed *raw*.

    Returns:
        The config version written to the file, or ``None`` when nothing was written.
    """
    if not migrated and not _needs_config_update(raw):
        return None
    if not _config_shape_is_rewritable(raw):
        return None

    merged_general = merged.get("general")
    if not isinstance(merged_general, dict):
        return None
    raw_general = raw.get("general")
    old_version = (
        raw_general.get("config_version", _CONFIG_VERSION) if isinstance(raw_general, dict) else _CONFIG_VERSION
    )
    merged_general["config_version"] = _next_version(old_version)
    try:
        _write_config_atomically(path, merged)
    except OSError as exc:
        # A read-only config (mount/permissions) must not abort startup —
        # continue with the migrated in-memory config; the rewrite is
        # retried on the next load.
        logger.warning("Could not rewrite config file '{}': {} — continuing with migrated in-memory config", path, exc)
        return None
    logger.info(
        "Config updated to version {} — added {} new fields",
        merged_general["config_version"],
        len(_config_keys(merged) - _config_keys(raw)),
    )
    return str(merged_general["config_version"])


def _inert_discovery_keys(raw: dict[str, Any]) -> list[str]:
    """Return the unread discovery keys that are not at their model default.

    Computed from the raw file rather than the merged model, then compared with the
    model defaults: the generated file carries every default, so a key still holding
    one is not operator intent and is not reported.

    Args:
        raw: The raw config, after migration.

    Returns:
        Dotted key paths, in a stable order.
    """
    overrides, _ = _discovery_overrides(raw)
    names: list[str] = []
    for platform_key, plat in overrides.items():
        names.extend(_inert_keys_for_platform(platform_key, plat))
    return names


def _unread_backlog_block(backlog: Any) -> tuple[str, Any]:
    """Return the name and contents of the backlog block the sort does not read."""
    sort_order = backlog.get("sort_order", "new") if isinstance(backlog, dict) else "new"
    block = "score" if sort_order == "new" else "new"
    return block, backlog.get(block) if isinstance(backlog, dict) else None


def _discovery_defaults() -> dict[str, Any]:
    """Return the model defaults of one discovery block, flattened by dotted path."""
    return dict(_leaf_items(DiscoveryConfig().model_dump(), ""))


def _unread_items(discovery: dict[str, Any]) -> list[tuple[str, Any]]:
    """Return the ``(path, value)`` leaves of the blocks the selected mode ignores."""
    if discovery.get("mode", "latest") == "latest":
        return _leaf_items(discovery.get("backlog"), "backlog")
    items = _leaf_items(discovery.get("latest"), "latest")
    block, unused = _unread_backlog_block(discovery.get("backlog"))
    items.extend(_leaf_items(unused, f"backlog.{block}"))
    return items


def _leaf_items(block: Any, prefix: str) -> list[tuple[str, Any]]:
    """Return the leaf ``(path, value)`` pairs of a raw block."""
    if not isinstance(block, dict):
        return []
    items: list[tuple[str, Any]] = []
    for key, value in sorted(block.items()):
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            items.extend(_leaf_items(value, path))
        else:
            items.append((path, value))
    return items


def _inert_keys_for_platform(platform_key: str, plat: Any) -> list[str]:
    """Return one platform's unread discovery keys that differ from the defaults.

    A key still holding its model default is not operator intent: the generated file
    carries every default so there is something to edit, and reporting those would
    make the warning permanent noise.
    """
    discovery = plat.get("discovery") if isinstance(plat, dict) else None
    if not isinstance(discovery, dict):
        return []
    defaults = _discovery_defaults()
    return [
        f"{platform_key}: discovery.{path}" for path, value in _unread_items(discovery) if value != defaults.get(path)
    ]


def _stray_discovery_keys(raw: dict[str, Any]) -> list[str]:
    """Return new-style discovery keys written outside the ``discovery`` block."""
    overrides, _ = _discovery_overrides(raw)
    names = _stray_parent_keys(raw)
    for platform_key, plat in overrides.items():
        names.extend(_stray_keys_for_platform(platform_key, plat))
    return names


def _stray_parent_keys(raw: dict[str, Any]) -> list[str]:
    """Return ``discovery`` blocks written one level too high."""
    names = [
        f"{label}: discovery"
        for parent, label in ((raw, "top-level"), (raw.get("review_sites"), "review_sites"))
        if isinstance(parent, dict) and "discovery" in parent
    ]
    _, metacritic = _discovery_overrides(raw)
    if "discovery" in metacritic:
        names.append("metacritic: discovery")
    return names


def _stray_keys_for_platform(platform_key: str, plat: Any) -> list[str]:
    """Return one platform's misplaced discovery key names."""
    if not isinstance(plat, dict):
        return []
    names = [f"{platform_key}: {key}" for key in _STRAY_DISCOVERY_KEYS if key in plat]
    if "pending_order" in plat:
        names.append(f"{platform_key}: pending_order")
    names.extend(f"{platform_key}: {key}" for key in plat if str(key).startswith("discovery."))
    return names


def _report_inert_discovery_keys(raw: dict[str, Any]) -> None:
    """Log one line per unread discovery key that differs from its model default."""
    for name in _inert_discovery_keys(raw):
        logger.info("Config: {} is not read by the selected mode - ignored", name)
    for name in _stray_discovery_keys(raw):
        if name.endswith("pending_order"):
            logger.warning("Config: '{}' belongs at review_sites.metacritic.pending_order - ignored", name)
        else:
            logger.warning("Config: '{}' is outside the discovery block - ignored", name)


def _first_validation_error(exc: ValidationError) -> str:
    """Return one readable line naming the offending key, its value and the reason.

    Args:
        exc: The validation failure raised by ``Config.model_validate``.

    Returns:
        A single line naming the dotted key, the input value and the error text,
        or ``str(exc)`` when the error carries no entries.
    """
    errors = exc.errors()
    if not errors:
        return str(exc)
    first = errors[0]
    key = ".".join(str(part) for part in first.get("loc", ()))
    return f"{key} = {first.get('input')!r} ({first.get('msg')})"


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
    path = _resolve_config_path(config_path)
    raw = _load_raw_config(path)

    # Migrate renamed fields (e.g. exclude_keywords → reject_keywords)
    migrated = _migrate_config(raw)
    # Belt-and-braces: the critic-name rename is Literal-sensitive (a
    # legacy "metascore" value would hard-fail validation), so re-apply it
    # here even if an earlier migration step swallowed an exception.
    migrated = _migrate_sort_order_critic_names(raw) or migrated
    _report_inert_discovery_keys(raw)

    merged = _deep_merge(_default_config_dict(), raw)

    # Convert any datetime.date objects back to ISO strings
    # (PyYAML parses ``2025-01-01`` as a date, but the model expects str)
    merged = _normalize_date_values(merged)

    # Validate first: a start that dies here must leave the operator's file exactly
    # as it was, so the failure is visible and fixable instead of silently persisted.
    try:
        # Validate a copy: keyed-list parsing injects ``name`` into the nested source
        # dicts, and the rewrite below must persist the operator's own shape.
        config = Config.model_validate(copy.deepcopy(merged))
    except ValidationError as exc:
        logger.error("Config file '{}' is invalid: {}", path, _first_validation_error(exc))
        raise

    # If migration made changes or the config is missing keys that the
    # current model defines, write the merged config back to the file
    # and bump the config version.  This keeps existing configs up-to-date
    # automatically when fields are renamed or added.
    written_version = _rewrite_config_if_needed(path, raw, merged, migrated)
    if written_version is not None:
        config.general.config_version = written_version
    return config
