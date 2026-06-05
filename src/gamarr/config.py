"""Configuration loading, validation, and default creation for gamarr."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
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
    browse_cache_ttl_hours: int = 4


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
    """Game library scanning settings."""

    enabled: bool = True
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

    merged = _deep_merge(_default_config_dict(), raw)
    return Config.model_validate(merged)
