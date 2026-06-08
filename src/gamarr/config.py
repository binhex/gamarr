"""Configuration loading, validation, and default creation for gamarr."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import BaseModel, Field

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


class ScheduleTaskConfig(BaseModel):
    """Settings for a single scheduled task (acquisition, scoring, etc.)."""

    enabled: bool = True
    schedule_time_mins: int = Field(default=60, gt=0)
    run_on_start: bool = True


class ScheduleConfig(BaseModel):
    """Scheduling configuration for periodic tasks."""

    acquisition: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=60))


class FitGirlSourceConfig(BaseModel):
    """FitGirl Repacks source settings."""

    enabled: bool = True
    rss_url: str = "https://fitgirl-repacks.site/feed/"
    platform: str = "pc"
    cache_ttl_hours: int = Field(default=6, gt=0, le=168)
    exclude_keywords: list[str] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    """Aggregated source configurations."""

    fitgirl: FitGirlSourceConfig = Field(default_factory=FitGirlSourceConfig)


class MetacriticPlatformConfig(BaseModel):
    """Metacritic scoring thresholds for a single platform."""

    min_metascore: int = 75
    min_metascore_reviews: int = 5
    min_user_score: float = 7.5
    min_user_reviews: int = 10
    days_since_release: int = 90
    cache_ttl_days: int = 7
    cache_ttl_hours: int = 4
    pending_days: int = 30
    enabled: bool = True
    max_games: int = Field(default=1000, ge=0, le=20000)
    max_verify_attempts: int = Field(default=6, ge=0)
    cutoff_weeks: int | None = None
    exclude_keywords: list[str] = Field(default_factory=list)


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


class DatabaseConfig(BaseModel):
    """Database retention and housekeeping settings."""

    processed_expiry_days: int = 365


class LibraryConfig(BaseModel):
    """Game library scanning settings.

    When ``paths`` is non-empty, library scanning is enabled.
    """

    paths: list[str] = Field(default_factory=list)


class Config(BaseModel):
    """Root configuration model that aggregates all sub-configs."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    metacritic: MetacriticConfig = Field(default_factory=MetacriticConfig)
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


def _rename_config_key(mc_pc: dict[str, Any], old_key: str, new_key: str | None, platform_key: str) -> None:
    """Rename *old_key* to *new_key* in *mc_pc* if it exists, logging the migration."""
    if old_key in mc_pc:
        if new_key is None:
            del mc_pc[old_key]
            logger.info("Config: removed old key '{}' for platform '{}'", old_key, platform_key)
        else:
            mc_pc[new_key] = mc_pc.pop(old_key)
            logger.info("Config: migrated '{}'\u2192'{}' for platform '{}'", old_key, new_key, platform_key)


def _migrate_config(raw: dict[str, Any]) -> None:
    """Migrate renamed config keys in-place for all platforms."""
    try:
        overrides = raw.get("metacritic", {}).get("platform_overrides", {})
        for platform_key, mc_pc in overrides.items():
            if not isinstance(mc_pc, dict):
                continue

            _rename_config_key(mc_pc, "browse_enabled", "enabled", platform_key)
            _rename_config_key(mc_pc, "browse_cache_ttl_hours", "cache_ttl_hours", platform_key)
            _rename_config_key(mc_pc, "metacritic_enabled", "enabled", platform_key)
            _rename_config_key(mc_pc, "metacritic_max_games", "max_games", platform_key)
            # Deprecated: browse_max_pages, max_score_checks — warn and drop
            for old_key in ("browse_max_pages", "max_score_checks"):
                if old_key in mc_pc:
                    logger.warning(
                        "Config: '{}' is deprecated for platform '{}'; use 'max_games' instead. Ignoring value.",
                        old_key,
                        platform_key,
                    )
                    mc_pc.pop(old_key)
            # Deprecated: cutoff_date — warn and drop
            for old_key in ("browse_cutoff_date", "metacritic_cutoff_date", "cutoff_date"):
                if old_key in mc_pc:
                    logger.warning(
                        "Config: '{}' is deprecated for platform '{}'; "
                        "set 'cutoff_weeks' instead (e.g. cutoff_weeks: 52 for ~1 year). "
                        "Ignoring value.",
                        old_key,
                        platform_key,
                    )
                    mc_pc.pop(old_key)
            _rename_config_key(mc_pc, "metacritic_cache_ttl_hours", "cache_ttl_hours", platform_key)

    except Exception as exc:
        logger.warning("Config migration failed: {}", exc)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base* (shallow copy of base)."""
    result = dict(base)
    for key, value in override.items():
        if value is None:
            continue
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
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
    return Config().model_dump()


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

    # Migrate renamed fields
    _migrate_config(raw)

    merged = _deep_merge(_default_config_dict(), raw)

    # If the user's config file is missing any keys that the current
    # model defines, write the merged config back to the file and bump
    # the config version.  This keeps existing configs up-to-date
    # automatically when new fields are added to the model.
    if raw and _needs_config_update(raw):
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
