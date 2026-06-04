# gamarr Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the gamarr daemon — FitGirl RSS monitoring, Metacritic scoring, qBittorrent delivery.

**Architecture:** Config-driven APScheduler daemon with source abstraction. FitGirl RSS source → Metacritic score lookup → qBittorrent delivery, with SQLite history database and Apprise notifications. Adapted from movarr patterns and gamecritic scraping logic.

**Tech Stack:** Python 3.12+, Click, Pydantic, SQLAlchemy, APScheduler, Loguru, requests, BeautifulSoup4, qbittorrent-api, Apprise, xmltodict, backoff.

---

### Task 1: Add new dependencies and create models / source protocol

**Files:**
- Modify: `pyproject.toml` (dependencies list)
- Create: `src/gamarr/models.py`
- Create: `src/gamarr/sources/__init__.py`
- Create: `tests/unit/test_models.py`

- [ ] **Step 1: Add new dependencies to pyproject.toml**

Edit `pyproject.toml` to add these to the `dependencies` list (after `"urllib3",`):

```toml
    "pydantic>=2.0.0",
    "qbittorrent-api",
    "xmltodict",
    "beautifulsoup4",
    "lxml",
```

Then add mypy overrides for untyped packages (before `[tool.pytest.ini_options]`):

```toml
[[tool.mypy.overrides]]
module = ["backoff"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["qbittorrentapi", "qbittorrentapi.*"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["apprise", "apprise.*"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["xmltodict"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["yaml"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["apscheduler", "apscheduler.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Run uv sync to install**

Run: `cd /data/gamarr && uv sync`
Expected: all packages install successfully.

- [ ] **Step 3: Write failing tests for models**

Create `tests/unit/test_models.py`:

```python
"""Tests for gamarr models and source protocol."""

from __future__ import annotations

from gamarr.models import GameEntry
from gamarr.sources import BaseSource


class TestGameEntry:
    """GameEntry dataclass construction and defaults."""

    def test_minimal_construction(self) -> None:
        """A GameEntry can be built with just the required fields."""
        entry = GameEntry(
            title="Elden Ring",
            source_title="Elden Ring (v1.12) [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:abc",
            source_url="https://fitgirl-repacks.site/elden-ring/",
        )
        assert entry.title == "Elden Ring"
        assert entry.source == "fitgirl"
        assert entry.platform == "pc"

    def test_all_fields(self) -> None:
        """All GameEntry fields are accessible."""
        entry = GameEntry(
            title="Test Game",
            source_title="Test Game [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:xyz",
            source_url="https://example.com/test-game/",
        )
        assert entry.title == "Test Game"
        assert entry.source_title == "Test Game [Repack]"


class TestBaseSource:
    """BaseSource protocol contract."""

    def test_protocol_has_fetch_new(self) -> None:
        """BaseSource requires a fetch_new method returning list[GameEntry]."""
        assert hasattr(BaseSource, "fetch_new")
        method = BaseSource.fetch_new
        # Protocol methods should not be callable directly (they're abstract)
        # but the attribute must exist
        assert callable(method)

    def test_protocol_has_source_name(self) -> None:
        """BaseSource requires a source_name property."""
        assert hasattr(BaseSource, "source_name")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_models.py -v`
Expected: ImportError for `gamarr.models` and `gamarr.sources`

- [ ] **Step 5: Create models.py**

Create `src/gamarr/models.py`:

```python
"""Shared data types for gamarr."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


__all__ = ["GameEntry", "HistoryRecord"]


@dataclass(frozen=True)
class GameEntry:
    """A single game discovered by a source (e.g. FitGirl RSS entry).

    Attributes:
        title: Cleaned canonical game name.
        source_title: Original title as returned by the source.
        source: Source identifier (e.g. "fitgirl", "dodi").
        platform: Platform slug (e.g. "pc", "nintendo-switch").
        magnet_url: Magnet URI for the torrent.
        source_url: Original article or release URL.
    """

    title: str
    source_title: str
    source: str
    platform: str
    magnet_url: str
    source_url: str


class HistoryRecord(TypedDict, total=False):
    """Shape of a history database row for pipeline results."""

    id: int
    source: str
    source_title: str
    game_title: str | None
    platform: str
    metascore: float | None
    user_score: float | None
    result: str  # "Passed" | "Failed" | "Error"
    result_details: str  # JSON list or human-readable string
    magnet_url: str | None
    torrent_tag: str | None
    processed_at: str  # ISO 8601
```

- [ ] **Step 6: Create sources/__init__.py (BaseSource protocol)**

Create `src/gamarr/sources/__init__.py`:

```python
"""Source abstraction for gamarr.

Each source type (FitGirl RSS, Jackett, Dodi, etc.) implements the
:class:`BaseSource` protocol so the acquisition pipeline can treat
them uniformly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from gamarr.models import GameEntry


@runtime_checkable
class BaseSource(Protocol):
    """Protocol every source must implement.

    Attributes:
        source_name: Human-readable identifier for the source
            (e.g. ``"fitgirl"``). Used for logging and DB attribution.
    """

    source_name: str

    def fetch_new(self) -> list[GameEntry]:
        """Return newly discovered games not previously processed.

        The implementation is responsible for deduplication against the
        history database when available.  Returns an empty list when
        there are no new entries.
        """
        ...
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_models.py -v`
Expected: 4 PASS

- [ ] **Step 8: Run full lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean.

- [ ] **Step 9: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add models, source protocol, and dependencies

- Add pydantic, qbittorrent-api, xmltodict, beautifulsoup4, lxml
- Create GameEntry dataclass and HistoryRecord TypedDict
- Create BaseSource protocol for source abstraction
- Add mypy overrides for untyped third-party packages"
```

---

### Task 2: Config module

**Files:**
- Create: `src/gamarr/config.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests for config**

Create `tests/unit/test_config.py`:

```python
"""Tests for gamarr config module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gamarr.config import (
    Config,
    FitGirlSourceConfig,
    GeneralConfig,
    MetacriticPlatformConfig,
    NotificationConfig,
    QbittorrentConfig,
    ScheduleTaskConfig,
    TorrentClientConfig,
    create_default_config,
    load_config,
)


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
        # Empty file should merge with all defaults
        assert cfg.general.daemon_mode == "foreground"
        assert cfg.sources.fitgirl.enabled is True

    def test_missing_optional_key_uses_default(self, tmp_path: Path) -> None:
        """An unknown key should warn but not crash."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "gamarr.yml"
        config_file.write_text("general:\n  daemon_mode: foreground\n  unknown_key: true\n")
        cfg = load_config(str(config_file))
        assert cfg.general.daemon_mode == "foreground"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_config.py -v`
Expected: ImportError for `gamarr.config`

- [ ] **Step 3: Create config.py**

Create `src/gamarr/config.py`:

```python
"""Configuration loading, validation, and default creation for gamarr."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import IO, Any

import yaml
from loguru import logger
from pydantic import BaseModel, Field

_CONFIG_VERSION = "1.0.0"
_INITIAL_CONFIG_VERSION = "1.0.0"
_CONFIG_FILENAME = "gamarr.yml"

_VALID_LOG_LEVELS = frozenset({"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"})


# ---------------------------------------------------------------------------
# Nested Pydantic models
# ---------------------------------------------------------------------------


class GeneralConfig(BaseModel):
    """Top-level general settings."""

    config_version: str = _CONFIG_VERSION
    daemon_mode: str = "foreground"
    log_level_console: str = "INFO"
    log_level_file: str = "INFO"
    log_path: str = "logs"
    db_path: str = "db"


class ScheduleTaskConfig(BaseModel):
    """A single scheduled task configuration."""

    enabled: bool = True
    schedule_time_mins: int = Field(default=60, gt=0)
    run_on_start: bool = True


class ScheduleConfig(BaseModel):
    """Schedule intervals for background tasks."""

    acquisition: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=60))


class FitGirlSourceConfig(BaseModel):
    """FitGirl repacks source settings."""

    enabled: bool = True
    rss_url: str = "https://fitgirl-repacks.site/feed/"
    platform: str = "pc"


class SourcesConfig(BaseModel):
    """All configured game sources."""

    fitgirl: FitGirlSourceConfig = Field(default_factory=FitGirlSourceConfig)


class MetacriticPlatformConfig(BaseModel):
    """Score thresholds for a single platform."""

    min_metascore: int = 75
    min_metascore_reviews: int = 5
    min_user_score: float = 7.5
    min_user_reviews: int = 10
    days_since_release: int = 90
    cache_ttl_days: int = 7
    browse_cache_ttl_hours: int = 4


class MetacriticConfig(BaseModel):
    """Metacritic settings per platform."""

    platform_overrides: dict[str, MetacriticPlatformConfig] = Field(
        default_factory=lambda: {"pc": MetacriticPlatformConfig()}
    )


class QbittorrentConfig(BaseModel):
    """qBittorrent connection settings."""

    host: str = "localhost"
    port: int = 8080
    username: str = "admin"
    password: str = "adminadmin"
    add_paused: bool = False
    category: str = "games-gamarr"


class TorrentClientConfig(BaseModel):
    """Torrent client selection and settings."""

    selected: str = "qbittorrent"
    qbittorrent: QbittorrentConfig = Field(default_factory=QbittorrentConfig)


class NotificationConfig(BaseModel):
    """Notification settings using Apprise."""

    apprise_urls: list[str] = Field(default_factory=list)
    on_download: bool = True
    on_failure: bool = False
    on_error: bool = False


class DatabaseConfig(BaseModel):
    """Database retention settings."""

    processed_expiry_days: int = 365


class Config(BaseModel):
    """Root configuration model for gamarr."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    metacritic: MetacriticConfig = Field(default_factory=MetacriticConfig)
    torrent_client: TorrentClientConfig = Field(default_factory=TorrentClientConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict."""
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
    """Return the default config as a plain dict suitable for YAML serialisation."""
    return Config().model_dump()


def create_default_config(config_path: str | Path) -> None:
    """Write a default ``gamarr.yml`` to *config_path* if it does not already exist.

    If *config_path* has a file extension it is used as-is.  Otherwise it is
    treated as a directory and ``gamarr.yml`` is created inside.
    """
    path = Path(config_path)
    if not path.suffix:
        path = path / _CONFIG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(_default_config_dict(), fh, default_flow_style=False, sort_keys=False)


def load_config(config_path: str | Path) -> Config:
    """Load, validate, and return the application config.

    Args:
        config_path: Path to the config directory or file.  If a directory,
            ``gamarr.yml`` is used inside it.

    Returns:
        A fully validated :class:`Config` instance.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_config.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 6: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add Pydantic YAML config module

- General, schedule, sources, metacritic, torrent_client, notification, database config models
- Config file creation with defaults if missing
- Deep merge of on-disk config with defaults
- Full test coverage for all config models and loading"
```

---

### Task 3: Database module

**Files:**
- Create: `src/gamarr/database.py`
- Create: `tests/unit/test_database.py`

- [ ] **Step 1: Write failing tests for database**

Create `tests/unit/test_database.py`:

```python
"""Tests for gamarr database module."""

from __future__ import annotations

from gamarr.database import Database


class TestDatabase:
    """Database CRUD operations."""

    def test_create_db_creates_tables(self, tmp_path) -> None:
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.close()

    def test_is_processed_returns_false_for_new_entry(self, tmp_path) -> None:
        db = Database(str(tmp_path / "test.db"))
        assert db.is_processed("fitgirl", "http://example.com/game") is False
        db.close()

    def test_is_processed_returns_true_after_insert(self, tmp_path) -> None:
        db = Database(str(tmp_path / "test.db"))
        db.record_processed(
            source="fitgirl",
            source_title="Test Game [Repack]",
            game_title="Test Game",
            platform="pc",
            metascore=80.0,
            user_score=8.0,
            result="Passed",
            result_details="All checks passed",
            magnet_url="magnet:?xt=urn:btih:abc",
        )
        assert db.is_processed("fitgirl", "http://example.com/game") is True
        db.close()

    def test_record_failed_entry(self, tmp_path) -> None:
        db = Database(str(tmp_path / "test.db"))
        db.record_processed(
            source="fitgirl",
            source_title="Bad Game [Repack]",
            game_title="Bad Game",
            platform="pc",
            metascore=30.0,
            user_score=2.0,
            result="Failed",
            result_details="Score below threshold",
        )
        assert db.is_processed("fitgirl", "http://example.com/bad") is True
        db.close()

    def test_get_stats_returns_counts(self, tmp_path) -> None:
        db = Database(str(tmp_path / "test.db"))
        stats = db.get_stats()
        assert "total" in stats
        assert "passed" in stats
        assert "failed" in stats
        assert stats["total"] == 0
        assert stats["passed"] == 0
        db.close()

    def test_get_stats_counts_correctly(self, tmp_path) -> None:
        db = Database(str(tmp_path / "test.db"))
        db.record_processed(source="fitgirl", source_title="A", result="Passed")
        db.record_processed(source="fitgirl", source_title="B", result="Failed", metascore=50.0)
        stats = db.get_stats()
        assert stats["total"] == 2
        assert stats["passed"] == 1
        assert stats["failed"] == 1
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_database.py -v`
Expected: ImportError for `gamarr.database`

- [ ] **Step 3: Create database.py**

Create `src/gamarr/database.py`:

```python
"""SQLite history database using SQLAlchemy for gamarr."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import Column, Integer, String, Text, Float, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


class HistoryRow(Base):
    """ORM mapping for the ``history`` table."""

    __tablename__ = "history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False, index=True)
    source_title = Column(String, nullable=False)
    game_title = Column(String, nullable=True)
    platform = Column(String, nullable=False)
    metascore = Column(Float, nullable=True)
    user_score = Column(Float, nullable=True)
    result = Column(String, nullable=False)  # "Passed" | "Failed" | "Error"
    result_details = Column(Text, nullable=True)
    magnet_url = Column(String, nullable=True)
    torrent_tag = Column(String, nullable=True)
    processed_at = Column(String, nullable=False)


class Database:
    """SQLite history database for tracking processed titles.

    Args:
        db_path: Path to the SQLite database file (directory or full path).
            If a directory, ``gamarr.db`` is used inside it.
    """

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        if path.suffix:
            self._db_path = str(path)
        else:
            path.mkdir(parents=True, exist_ok=True)
            self._db_path = str(path / "gamarr.db")

        self._engine = create_engine(f"sqlite:///{self._db_path}", echo=False)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)
        logger.debug("Database opened at '{}'", self._db_path)

    def close(self) -> None:
        """Dispose of the database engine connection pool."""
        self._engine.dispose()

    # ------------------------------------------------------------------
    # Session helper
    # ------------------------------------------------------------------

    def _session(self) -> Session:
        return self._session_factory()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def is_processed(self, source: str, source_url: str) -> bool:
        """Return True if a game with *source_url* has already been processed.

        Uses the source_url for deduplication across scheduler cycles.
        For FitGirl the source_url is the RSS ``<link>`` value.
        """
        with self._session() as session:
            # source_title stores the source_url since we need to deduplicate on URL
            count = session.query(HistoryRow).filter(
                HistoryRow.source == source,
                HistoryRow.source_title == source_url,
            ).count()
            return count > 0

    def record_processed(
        self,
        *,
        source: str,
        source_title: str,
        game_title: str | None = None,
        platform: str = "pc",
        metascore: float | None = None,
        user_score: float | None = None,
        result: str = "Passed",
        result_details: str = "",
        magnet_url: str | None = None,
        torrent_tag: str | None = None,
    ) -> None:
        """Record a processed game entry in the history database."""
        with self._session() as session:
            row = HistoryRow(
                source=source,
                source_title=source_title,
                game_title=game_title,
                platform=platform,
                metascore=metascore,
                user_score=user_score,
                result=result,
                result_details=result_details,
                magnet_url=magnet_url,
                torrent_tag=torrent_tag,
                processed_at=datetime.datetime.now(tz=datetime.UTC).isoformat(),
            )
            session.add(row)
            session.commit()

    def get_stats(self) -> dict[str, Any]:
        """Return aggregate statistics about processed titles."""
        with self._session() as session:
            total = session.query(HistoryRow).count()
            passed = session.query(HistoryRow).filter(HistoryRow.result == "Passed").count()
            failed = session.query(HistoryRow).filter(HistoryRow.result == "Failed").count()
            return {
                "total": total,
                "passed": passed,
                "failed": failed,
            }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_database.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 6: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add SQLAlchemy history database

- HistoryRow ORM model with all pipeline result fields
- Database class with is_processed, record_processed, get_stats
- Full test coverage for CRUD and stats"
```

---

### Task 4: FitGirl RSS source

**Files:**
- Create: `src/gamarr/sources/fitgirl.py`
- Create: `tests/unit/test_fitgirl.py`

- [ ] **Step 1: Write failing tests for FitGirl source**

Create `tests/unit/test_fitgirl.py`:

```python
"""Tests for gamarr FitGirl RSS source."""

from __future__ import annotations

from pathlib import Path

import pytest

from gamarr.sources.fitgirl import FitGirlSource, _clean_title


class TestTitleCleaning:
    """RSS title cleansing logic."""

    def test_clean_simple_title(self) -> None:
        assert _clean_title("Hades II [Repack]") == "Hades II"

    def test_clean_title_with_version(self) -> None:
        result = _clean_title("Elden Ring (v1.12 + DLCs, MULTi13) [Repack]")
        assert result == "Elden Ring"

    def test_clean_title_with_complex_version(self) -> None:
        result = _clean_title("Baldur's Gate 3 (v4.1.1.4.64194 Hotfix 28, MULTi17) [Repack]")
        assert result == "Baldur's Gate 3"

    def test_clean_title_with_multi_only(self) -> None:
        result = _clean_title("Some Game (MULTi5) [Repack]")
        assert result == "Some Game"

    def test_clean_title_no_repack(self) -> None:
        result = _clean_title("Game Name (v1.0) [Repack]")
        assert result == "Game Name"

    def test_clean_title_repack_no_version(self) -> None:
        result = _clean_title("Cyberpunk 2077 [Repack]")
        assert result == "Cyberpunk 2077"

    def test_clean_title_preserves_ampersand(self) -> None:
        result = _clean_title("Crash & Spyro [Repack]")
        assert result == "Crash & Spyro"

    def test_clean_title_apostrophe(self) -> None:
        result = _clean_title("Assassin's Creed [Repack]")
        assert result == "Assassin's Creed"

    def test_clean_title_strips_selective_download(self) -> None:
        result = _clean_title("Game Name (Selective Download) [Repack]")
        assert result == "Game Name"


class TestFitGirlSource:
    """FitGirlSource construction and protocol conformance."""

    def test_implements_base_source(self) -> None:
        from gamarr.sources import BaseSource

        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        assert isinstance(source, BaseSource)

    def test_source_name(self) -> None:
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        assert source.source_name == "fitgirl"

    def test_platform(self) -> None:
        source = FitGirlSource("http://example.com/feed.xml", platform="pc", db_path=":memory:")
        assert source.platform == "pc"

    def test_fetch_new_returns_list(self) -> None:
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        entries = source.fetch_new()
        # With a fake URL and no mock, this should return empty list (request fails)
        # The actual HTTP path is tested via integration
        assert isinstance(entries, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_fitgirl.py -v`
Expected: ImportError for `gamarr.sources.fitgirl`

- [ ] **Step 3: Create sources/fitgirl.py**

Create `src/gamarr/sources/fitgirl.py`:

```python
"""FitGirl repacks RSS source for gamarr.

Fetches the FitGirl RSS feed, parses new entries, cleans game titles,
and extracts magnet links.
"""

from __future__ import annotations

import os
import re
from typing import Any

import requests
from loguru import logger
from xmltodict import parse as parse_xml

from gamarr.models import GameEntry

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Pattern to strip parenthesised technical metadata from FitGirl titles.
# Matches e.g. (v1.12 + DLCs, MULTi13) or (MULTi5) or (Selective Download)
_TECH_PAREN_PATTERN = re.compile(r"\s*\(.*?(?:v?\d[\d.]*|MULTi|Selective|Repack).*?\)", re.IGNORECASE)
_REPACK_TAG_PATTERN = re.compile(r"\s*\[Repack\]", re.IGNORECASE)

# Pattern to find a magnet link in HTML/XML content
_MAGNET_PATTERN = re.compile(r"(magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s\"'<>]*)")

# Timeout for HTTP requests (connect, read)
_CONNECT_TIMEOUT = 30.0
_READ_TIMEOUT = 60.0


def _clean_title(raw_title: str) -> str:
    """Strip FitGirl repack metadata from an RSS title, returning the canonical game name.

    Args:
        raw_title: Raw RSS title, e.g. ``"Elden Ring (v1.12 + DLCs, MULTi13) [Repack]"``.

    Returns:
        Cleaned game name, e.g. ``"Elden Ring"``.
    """
    title = raw_title.strip()
    # Remove [Repack] suffix
    title = _REPACK_TAG_PATTERN.sub("", title)
    # Remove parenthesised technical metadata (version, languages, etc.)
    title = _TECH_PAREN_PATTERN.sub("", title)
    return title.strip()


def _extract_magnet_from_html(html_content: str) -> str | None:
    """Extract the first magnet link found in *html_content*.

    Args:
        html_content: Raw HTML page content.

    Returns:
        The first magnet URI found, or ``None``.
    """
    match = _MAGNET_PATTERN.search(html_content)
    if match:
        return match.group(1).strip()
    return None


class FitGirlSource:
    """FitGirl RSS source implementation.

    Args:
        rss_url: URL of the FitGirl RSS feed.
        platform: Platform identifier (default ``"pc"``).
        db_path: Path for the deduplication database.
            ``":memory:"`` uses an in-memory SQLite DB.
    """

    def __init__(
        self,
        rss_url: str,
        platform: str = "pc",
        db_path: str = ":memory:",
    ) -> None:
        self._rss_url = rss_url
        self._platform = platform
        # Lazy import to avoid circular dependency
        from gamarr.database import Database

        self._db = Database(db_path)

    @property
    def source_name(self) -> str:
        return "fitgirl"

    @property
    def platform(self) -> str:
        return self._platform

    def fetch_new(self) -> list[GameEntry]:
        """Fetch the RSS feed and return entries not yet in the history DB.

        Returns:
            List of new :class:`GameEntry` objects.  Empty when the feed
            is unreachable or has no new entries.
        """
        logger.debug("Fetching FitGirl RSS feed from '{}'", self._rss_url)
        try:
            resp = requests.get(
                self._rss_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Failed to fetch FitGirl RSS feed: {}", exc)
            return []

        try:
            feed = parse_xml(resp.text)
        except Exception as exc:
            logger.warning("Failed to parse FitGirl RSS XML: {}", exc)
            return []

        items = _get_rss_items(feed)
        if items is None:
            logger.warning("No RSS items found in FitGirl feed response.")
            return []

        entries: list[GameEntry] = []
        for item in items:
            raw_title = item.get("title", "")
            link = item.get("link", "")
            if not raw_title or not link:
                continue

            # Skip already processed entries
            if self._db.is_processed(self.source_name, link):
                logger.debug("Skipping already processed entry: '{}'", raw_title)
                continue

            cleaned_title = _clean_title(raw_title)
            magnet_url = self._extract_magnet(item, link)

            entry = GameEntry(
                title=cleaned_title,
                source_title=raw_title,
                source=self.source_name,
                platform=self._platform,
                magnet_url=magnet_url or "",
                source_url=link,
            )
            entries.append(entry)

        logger.info("FitGirl RSS: found {} new entries", len(entries))
        return entries

    def _extract_magnet(self, item: dict[str, Any], link: str) -> str | None:
        """Extract a magnet link from the RSS item or its linked article page.

        Tries the RSS ``description`` field first, then falls back to
        fetching the article page HTML.
        """
        description = item.get("description", "")
        if isinstance(description, str):
            magnet = _extract_magnet_from_html(description)
            if magnet:
                return magnet

        # Fallback: scrape the article page for a magnet link
        try:
            resp = requests.get(
                link,
                headers={"User-Agent": _USER_AGENT},
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
            resp.raise_for_status()
            magnet = _extract_magnet_from_html(resp.text)
            if magnet:
                return magnet
        except requests.RequestException as exc:
            logger.warning("Failed to fetch FitGirl article page '{}': {}", link, exc)

        return None

    def close(self) -> None:
        """Close the internal database connection."""
        self._db.close()


def _get_rss_items(feed: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Safely extract RSS items from a parsed ``xmltodict`` feed.

    Handles the ``{"rss": {"channel": {"item": [...]}}}`` nesting.
    """
    try:
        channel = feed.get("rss", {}).get("channel", {})
        items = channel.get("item")
        if items is None:
            return None
        if isinstance(items, dict):
            return [items]
        if isinstance(items, list):
            return items
    except (AttributeError, TypeError):
        pass
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_fitgirl.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 6: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add FitGirl RSS source

- RSS feed fetching with xmltodict parsing
- Title cleansing (strip [Repack], version info, language tags)
- Magnet extraction from RSS description or article page
- History DB deduplication via source_url
- Full test coverage for title cleaning and source construction"
```

---

### Task 5: Metacritic module

**Files:**
- Create: `src/gamarr/metacritic.py`
- Create: `src/gamarr/metacritic_cache.py`
- Create: `tests/unit/test_metacritic.py`

- [ ] **Step 1: Write failing tests for Metacritic module**

Create `tests/unit/test_metacritic.py`:

```python
"""Tests for gamarr Metacritic integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gamarr.metacritic import MetacriticClient, ScoreResult
from gamarr.metacritic_cache import MetacriticCache


class TestScoreResult:
    """ScoreResult dataclass construction."""

    def test_passing_score(self) -> None:
        sr = ScoreResult(
            title="Elden Ring",
            slug="elden-ring",
            metascore=96.0,
            metascore_review_count=120,
            user_score=8.5,
            user_review_count=5000,
            passed=True,
        )
        assert sr.passed is True
        assert sr.metascore == 96.0

    def test_failing_score(self) -> None:
        sr = ScoreResult(
            title="Bad Game",
            slug="bad-game",
            metascore=30.0,
            metascore_review_count=5,
            user_score=2.0,
            user_review_count=10,
            passed=False,
        )
        assert sr.passed is False


class TestSlugGeneration:
    """Metacritic slug generation from game titles."""

    def test_simple_slug(self) -> None:
        from gamarr.metacritic import _make_slug

        assert _make_slug("Elden Ring") == "elden-ring"

    def test_slug_with_apostrophe(self) -> None:
        from gamarr.metacritic import _make_slug

        assert _make_slug("Baldur's Gate 3") == "baldurs-gate-3"

    def test_slug_with_colon(self) -> None:
        from gamarr.metacritic import _make_slug

        assert _make_slug("Hades II") == "hades-ii"

    def test_slug_with_ampersand(self) -> None:
        from gamarr.metacritic import _make_slug

        assert _make_slug("Crash & Spyro") == "crash-spyro"

    def test_slug_with_special_chars(self) -> None:
        from gamarr.metacritic import _make_slug

        assert _make_slug("Game: The Reckoning!") == "game-the-reckoning"


class TestMetacriticCache:
    """Metacritic cache operations."""

    def test_cache_miss(self, tmp_path: Path) -> None:
        cache = MetacriticCache(str(tmp_path / "cache.db"))
        result = cache.get_game_detail("elden-ring")
        assert result is None
        cache.close()

    def test_cache_set_and_get(self, tmp_path: Path) -> None:
        cache = MetacriticCache(str(tmp_path / "cache.db"))
        cache.set_game_detail("elden-ring", 96.0, 120, 8.5, 5000)
        result = cache.get_game_detail("elden-ring")
        assert result is not None
        assert result["user_score"] == 8.5  # type: ignore[index]
        cache.close()

    def test_cache_expiry(self, tmp_path: Path) -> None:
        import datetime

        cache = MetacriticCache(str(tmp_path / "cache.db"))
        cache.set_game_detail("old-game", 50.0, 10, 5.0, 100)
        # Manually set cached_at to a very old date
        cache._set_cached_at("old-game", (datetime.datetime.now() - datetime.timedelta(days=999)).isoformat())
        result = cache.get_game_detail("old-game", ttl_days=7)
        # Should be expired
        assert result is None
        cache.close()

    def test_browse_cache(self, tmp_path: Path) -> None:
        cache = MetacriticCache(str(tmp_path / "cache.db"))
        games = [{"title": "Test Game", "slug": "test-game"}]
        cache.set_browse_page("pc", 1, games)
        result = cache.get_browse_page("pc", 1, ttl_hours=4)
        assert result is not None
        assert result[0]["title"] == "Test Game"
        cache.close()


class TestMetacriticClient:
    """Metacritic client construction."""

    def test_client_defaults(self) -> None:
        client = MetacriticClient(cache_path=":memory:")
        assert client.user_agent is not None

    def test_score_for_title_returns_none_on_fetch_error(self) -> None:
        """With a non-existent slug, lookup returns None."""
        client = MetacriticClient(cache_path=":memory:")
        result = client.lookup_game("ThisGameDoesNotExistXYZ123")
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_metacritic.py -v`
Expected: ImportError for `gamarr.metacritic`

- [ ] **Step 3: Create metacritic_cache.py**

Create `src/gamarr/metacritic_cache.py`:

```python
"""SQLite cache for Metacritic browse pages and game details.

Adapted from gamecritic's caching layer.  All cache entries have
TTLs and can be safely purged without affecting the history DB.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

import sqlite3


class MetacriticCache:
    """SQLite-backed cache for Metacritic scraped data.

    Args:
        cache_path: Path to the cache SQLite database.  ``":memory:"``
            creates an in-memory database (useful for testing).
    """

    def __init__(self, cache_path: str) -> None:
        self._conn = sqlite3.connect(cache_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS game_detail_cache (
                slug              TEXT PRIMARY KEY,
                metascore         REAL,
                metascore_reviews INTEGER,
                user_score        REAL,
                user_reviews      INTEGER,
                cached_at         TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS browse_page_cache (
                platform    TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                games_json  TEXT NOT NULL,
                cached_at   TEXT NOT NULL,
                PRIMARY KEY (platform, page_number)
            );
        """)
        self._conn.commit()

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Game detail cache
    # ------------------------------------------------------------------

    def get_game_detail(
        self,
        slug: str,
        ttl_days: int = 7,
    ) -> dict[str, Any] | None:
        """Return cached game detail for *slug*, or ``None`` if expired/missing.

        Args:
            slug: Metacritic game slug.
            ttl_days: Maximum age in days before the entry is considered stale.

        Returns:
            Dict with keys ``metascore``, ``metascore_reviews``, ``user_score``,
            ``user_reviews``, or ``None``.
        """
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=ttl_days)).isoformat()
        row = self._conn.execute(
            "SELECT * FROM game_detail_cache WHERE slug = ? AND cached_at > ?",
            (slug, cutoff),
        ).fetchone()
        if row is None:
            return None
        return {
            "metascore": row["metascore"],
            "metascore_reviews": row["metascore_reviews"],
            "user_score": row["user_score"],
            "user_reviews": row["user_reviews"],
        }

    def set_game_detail(
        self,
        slug: str,
        metascore: float | None,
        metascore_reviews: int | None,
        user_score: float | None,
        user_reviews: int | None,
    ) -> None:
        """Insert or update the cached game detail for *slug*."""
        self._conn.execute(
            """INSERT OR REPLACE INTO game_detail_cache
               (slug, metascore, metascore_reviews, user_score, user_reviews, cached_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                slug,
                metascore,
                metascore_reviews,
                user_score,
                user_reviews,
                datetime.datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def _set_cached_at(self, slug: str, cached_at: str) -> None:
        """Override the cached_at timestamp (used for testing expiry)."""
        self._conn.execute(
            "UPDATE game_detail_cache SET cached_at = ? WHERE slug = ?",
            (cached_at, slug),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Browse page cache
    # ------------------------------------------------------------------

    def get_browse_page(
        self,
        platform: str,
        page_number: int,
        ttl_hours: int = 4,
    ) -> list[dict[str, Any]] | None:
        """Return cached browse page results, or ``None`` if expired/missing.

        Args:
            platform: Metacritic platform slug (e.g. ``"pc"``).
            page_number: 1-based page index.
            ttl_hours: Maximum age in hours.

        Returns:
            List of game dicts, or ``None``.
        """
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=ttl_hours)).isoformat()
        row = self._conn.execute(
            "SELECT games_json FROM browse_page_cache WHERE platform = ? AND page_number = ? AND cached_at > ?",
            (platform, page_number, cutoff),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["games_json"])

    def set_browse_page(
        self,
        platform: str,
        page_number: int,
        games: list[dict[str, Any]],
    ) -> None:
        """Insert or replace the cached browse page."""
        self._conn.execute(
            """INSERT OR REPLACE INTO browse_page_cache (platform, page_number, games_json, cached_at)
               VALUES (?, ?, ?, ?)""",
            (platform, page_number, json.dumps(games), datetime.datetime.now().isoformat()),
        )
        self._conn.commit()
```

- [ ] **Step 4: Create metacritic.py**

Create `src/gamarr/metacritic.py`:

```python
"""Metacritic score lookup for gamarr.

Adapted from gamecritic's Nuxt JSON scraping approach.  Looks up a game
title by first trying a direct slug URL, then falling back to browse
page scanning.
"""

from __future__ import annotations

import json
import os
import re
import string
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup
from loguru import logger

from gamarr.metacritic_cache import MetacriticCache

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_CONNECT_TIMEOUT = 30.0
_READ_TIMEOUT = 90.0

# Pattern to identify the Nuxt data root index in script text
_BROWSE_GAME_KEY_PATTERN = re.compile(r'"(browse-game-[^"]*)":\s*(\d+)')


@dataclass
class ScoreResult:
    """Result of a Metacritic score lookup for a single game.

    Attributes:
        title: Game title from Metacritic.
        slug: Metacritic game slug.
        metascore: Critic Metascore (0-100), or ``None``.
        metascore_review_count: Number of critic reviews.
        user_score: User score (0-10), or ``None``.
        user_review_count: Number of user reviews.
        passed: ``True`` if scores met the configured thresholds.
    """

    title: str
    slug: str
    metascore: float | None
    metascore_review_count: int | None
    user_score: float | None
    user_review_count: int | None
    passed: bool


def _make_slug(title: str) -> str:
    """Convert a game title into a Metacritic URL slug.

    Args:
        title: Cleaned game title (e.g. ``"Elden Ring"``).

    Returns:
        URL slug (e.g. ``"elden-ring"``).
    """
    # Lowercase
    slug = title.lower()
    # Remove apostrophes
    slug = slug.replace("'", "")
    # Replace ampersand with 'and'
    slug = slug.replace("&", "and")
    # Remove non-alphanumeric characters except spaces and hyphens
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    # Replace spaces with hyphens
    slug = re.sub(r"\s+", "-", slug)
    # Collapse multiple hyphens
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _nuxt_val(data: list[Any], ref: Any) -> Any:
    """Resolve a Nuxt JSON reference (int index or literal value)."""
    if isinstance(ref, int) and ref < len(data):
        return data[ref]
    return ref


def _parse_game_details(page_content: bytes) -> dict[str, Any] | None:
    """Parse Metascore, user score, and review counts from a game page.

    Scans all ``<script>`` tags for embedded Nuxt JSON state containing
    score data.  Adapted from gamecritic's ``_parse_game_details``.

    Args:
        page_content: Raw HTML bytes of the game page.

    Returns:
        Dict with keys ``metascore``, ``metascore_reviews``, ``user_score``,
        ``user_reviews``, or ``None`` if parsing fails.
    """
    try:
        soup = BeautifulSoup(page_content, features="html.parser")
    except TypeError:
        return None

    for script in soup.find_all("script"):
        stext = script.string or ""
        if len(stext) < 1000:
            continue
        try:
            page_data = json.loads(stext)
        except (json.JSONDecodeError, TypeError):
            continue

        metascore = metascore_reviews = user_score = user_reviews = None

        for item in page_data:
            if not isinstance(item, dict):
                continue

            # Critic score block
            if metascore is None and "score" in item and "reviewCount" in item:
                score_val = _nuxt_val(page_data, item["score"])
                if isinstance(score_val, (int, float)):
                    metascore = float(score_val)
                    metascore_reviews = _nuxt_val(page_data, item.get("reviewCount"))

            # User score block
            if user_score is None and "userScore" in item:
                us = _nuxt_val(page_data, item.get("userScore"))
                if isinstance(us, dict) and "score" in us:
                    us_score = _nuxt_val(page_data, us.get("score"))
                    if isinstance(us_score, (int, float)):
                        user_score = float(us_score)
                        user_reviews = _nuxt_val(page_data, us.get("reviewCount"))

        if metascore is not None or user_score is not None:
            return {
                "metascore": metascore,
                "metascore_reviews": metascore_reviews,
                "user_score": user_score,
                "user_reviews": user_reviews,
            }

    return None


def _parse_browse_page(content: bytes) -> list[dict[str, Any]] | None:
    """Parse Nuxt state from a Metacritic browse listing page.

    Adapted from gamecritic's ``_parse_browse_page``.

    Args:
        content: Raw HTML bytes of the browse page.

    Returns:
        List of game dicts with keys ``title``, ``slug``, ``score``,
        ``critic_review_count``, ``user_rating``, or ``None`` on failure.
    """
    try:
        soup = BeautifulSoup(content, features="html.parser")
    except TypeError:
        return None

    nuxt_data = None
    game_items = None

    for script in soup.find_all("script"):
        stext = script.string or ""
        if "browse-game" not in stext:
            continue
        try:
            parsed = json.loads(stext)
            m = _BROWSE_GAME_KEY_PATTERN.search(stext)
            if m:
                root_idx = int(m.group(2))
                root = parsed[root_idx]
                items_ref = root.get("items")
                if isinstance(items_ref, int) and items_ref < len(parsed):
                    nuxt_data = parsed
                    game_items = nuxt_data[items_ref]
                    break
        except (json.JSONDecodeError, TypeError, KeyError, IndexError):
            pass

    if game_items is None:
        return None
    if not isinstance(game_items, list):
        return None

    resolved_games = []
    for game_idx in game_items:
        try:
            game = nuxt_data[game_idx]  # type: ignore[index]
            cs = _nuxt_val(nuxt_data, game.get("criticScoreSummary"))  # type: ignore[index]
            us = _nuxt_val(nuxt_data, game.get("userScore"))  # type: ignore[index]
            resolved_games.append({
                "title": _nuxt_val(nuxt_data, game.get("title")),
                "slug": _nuxt_val(nuxt_data, game.get("slug")),
                "score": _nuxt_val(cs.get("score")) if isinstance(cs, dict) else None,  # type: ignore[union-attr]
                "critic_review_count": _nuxt_val(cs.get("reviewCount")) if isinstance(cs, dict) else None,  # type: ignore[union-attr]
                "user_rating": _nuxt_val(us.get("score")) if isinstance(us, dict) else None,  # type: ignore[union-attr]
                "user_review_count": _nuxt_val(us.get("reviewCount")) if isinstance(us, dict) else None,  # type: ignore[union-attr]
            })
        except (TypeError, KeyError, IndexError):
            pass

    return resolved_games


class MetacriticClient:
    """Client for looking up game scores on Metacritic.

    Uses a two-step strategy:
    1. Direct slug guess (fast path for common titles)
    2. Browse page scan (fallback for non-standard slugs)

    Results are cached in a local SQLite database.

    Args:
        cache_path: Path for the SQLite cache database.
            ``":memory:"`` creates an in-memory cache.
        user_agent: HTTP User-Agent header value.
    """

    def __init__(
        self,
        cache_path: str = "db/gamarr-cache.db",
        user_agent: str = _USER_AGENT,
    ) -> None:
        self.user_agent = user_agent
        self._cache = MetacriticCache(cache_path)

    def close(self) -> None:
        """Close the cache database connection."""
        self._cache.close()

    def lookup_game(
        self,
        title: str,
        platform: str = "pc",
        cache_ttl_days: int = 7,
        browse_cache_ttl_hours: int = 4,
    ) -> ScoreResult | None:
        """Look up a game by title on Metacritic and return its scores.

        Args:
            title: Cleaned game title.
            platform: Metacritic platform slug (default ``"pc"``).
            cache_ttl_days: TTL for game detail cache in days.
            browse_cache_ttl_hours: TTL for browse page cache in hours.

        Returns:
            A :class:`ScoreResult` if the game was found, or ``None`` if
            the game could not be located on Metacritic.
        """
        slug = _make_slug(title)

        # Step 1: Try direct slug URL
        result = self._try_direct_slug(slug, cache_ttl_days)
        if result is not None:
            return result

        # Step 2: Fall back to browsing PC games newest-first
        logger.debug("Direct slug '{}' failed for '{}', scanning browse pages...", slug, title)
        result = self._scan_browse_pages(title, platform, browse_cache_ttl_hours, cache_ttl_days)
        return result

    # ------------------------------------------------------------------
    # Direct slug lookup
    # ------------------------------------------------------------------

    def _try_direct_slug(
        self,
        slug: str,
        cache_ttl_days: int,
    ) -> ScoreResult | None:
        """Try fetching a Metacritic game page directly by slug.

        Checks cache first, then fetches from Metacritic if the cache
        is cold or the entry is stale.
        """
        # Check cache
        cached = self._cache.get_game_detail(slug, ttl_days=cache_ttl_days)
        if cached is not None:
            logger.debug("Cache hit for slug '{}'", slug)
            return ScoreResult(
                title=slug.replace("-", " ").title(),
                slug=slug,
                metascore=cached["metascore"],
                metascore_review_count=cached["metascore_reviews"],
                user_score=cached["user_score"],
                user_review_count=cached["user_reviews"],
                passed=False,  # Caller evaluates thresholds
            )

        # Fetch game page
        url = f"https://www.metacritic.com/game/{slug}/"
        logger.debug("Fetching game page '{}'", url)

        try:
            resp = requests.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                allow_redirects=True,
            )
            if resp.status_code != 200:
                logger.debug("Game page '{}' returned status {}", url, resp.status_code)
                return None

            parsed = _parse_game_details(resp.content)
            if parsed is None:
                return None

            # Cache the result
            self._cache.set_game_detail(
                slug=slug,
                metascore=parsed.get("metascore"),
                metascore_reviews=parsed.get("metascore_reviews"),
                user_score=parsed.get("user_score"),
                user_reviews=parsed.get("user_reviews"),
            )

            return ScoreResult(
                title=slug.replace("-", " ").title(),
                slug=slug,
                metascore=parsed.get("metascore"),
                metascore_review_count=parsed.get("metascore_reviews"),
                user_score=parsed.get("user_score"),
                user_review_count=parsed.get("user_reviews"),
                passed=False,
            )

        except requests.RequestException as exc:
            logger.warning("Failed to fetch game page '{}': {}", url, exc)
            return None

    # ------------------------------------------------------------------
    # Browse page scan (fallback)
    # ------------------------------------------------------------------

    def _scan_browse_pages(
        self,
        title: str,
        platform: str,
        browse_cache_ttl_hours: int,
        cache_ttl_days: int,
    ) -> ScoreResult | None:
        """Scan Metacritic browse pages newest-first to find a matching title.

        Uses fuzzy matching: normalized lowercase comparison after stripping
        punctuation.
        """
        normalized_title = _normalise_for_compare(title)
        page_number = 1
        max_pages = 10  # Limit scan depth

        while page_number <= max_pages:
            url = (
                f"https://www.metacritic.com/browse/game/{platform}/all/all-time/new/"
                f"?releaseYearMin=1958&releaseYearMax=2035"
                f"&platform={platform}&page={page_number}"
            )

            logger.debug("Scanning browse page {} for '{}'", page_number, title)

            cached_games = self._cache.get_browse_page(platform, page_number, ttl_hours=browse_cache_ttl_hours)
            if cached_games is not None:
                games = cached_games
                logger.debug("Browse cache hit for platform '{}' page {}", platform, page_number)
            else:
                try:
                    resp = requests.get(
                        url,
                        headers={"User-Agent": self.user_agent},
                        timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                        allow_redirects=True,
                    )
                    if resp.status_code != 200:
                        break

                    parsed_games = _parse_browse_page(resp.content)
                    if parsed_games is None:
                        break
                    games = parsed_games
                    self._cache.set_browse_page(platform, page_number, games)
                except requests.RequestException as exc:
                    logger.warning("Failed to fetch browse page '{}': {}", url, exc)
                    break

            if not games:
                break

            # Look for a title match
            for game in games:
                game_title = game.get("title")
                if not game_title:
                    continue
                if _normalise_for_compare(str(game_title)) == normalized_title:
                    logger.info("Found matching game '{}' (slug: {}) on browse page {}",
                                game_title, game.get("slug"), page_number)
                    slug = str(game.get("slug", ""))

                    # Fetch the game's detail page for accurate scores
                    return self._try_direct_slug(slug, cache_ttl_days)

            page_number += 1

        logger.info("Game '{}' not found on Metacritic browse pages", title)
        return None


def _normalise_for_compare(text: str) -> str:
    """Normalise a string for case-insensitive, punctuation-insensitive comparison."""
    text = text.lower().strip()
    # Remove punctuation (keep alphanumeric and spaces)
    text = text.translate(str.maketrans("", "", string.punctuation))
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_metacritic.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 7: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add Metacritic score lookup module

- ScoreResult dataclass for lookup outcomes
- Direct slug URL guess (fast path)
- Browse page fallback scan (fuzzy title match)
- SQLite cache for browse pages and game details
- Title normalisation for comparison
- Full test coverage for slugs, cache, and client construction"
```

---

### Task 6: qBittorrent client

**Files:**
- Create: `src/gamarr/qbittorrent.py`
- Create: `tests/unit/test_qbittorrent.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_qbittorrent.py`:

```python
"""Tests for gamarr qBittorrent client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gamarr.qbittorrent import QBittorrentClient


class TestQBittorrentConfig:
    """QBittorrentClient construction."""

    def test_client_constructs(self) -> None:
        client = QBittorrentClient(
            host="localhost",
            port=8080,
            username="admin",
            password="adminadmin",
            category="games-gamarr",
        )
        assert client._category == "games-gamarr"

    def test_client_constructs_defaults(self) -> None:
        client = QBittorrentClient()
        assert client._host == "localhost"
        assert client._port == 8080


class TestQBittorrentAdd:
    """Adding torrents to qBittorrent."""

    def test_add_no_url_returns_false(self) -> None:
        client = QBittorrentClient()
        # Without setting up a mock, calling add_torrent with no
        # magnet url should fail gracefully
        result = client.add_torrent(magnet_url="", title="Test Game")
        assert result is False

    def test_add_torrent_returns_tag_on_success(self) -> None:
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Ok."
            mock_client.torrents_info.return_value = [MagicMock(hash="abc123")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:abc",
                title="Test Game",
            )
            assert result is not False
            assert result.startswith("gamarr-")
            mock_client.torrents_add.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_qbittorrent.py -v`
Expected: ImportError for `gamarr.qbittorrent`

- [ ] **Step 3: Create qbittorrent.py**

Create `src/gamarr/qbittorrent.py`:

```python
"""qBittorrent WebUI client wrapper for gamarr.

Simplified version of movarr's QBittorrentClient — handles adding
torrents with tagging and health checking.
"""

from __future__ import annotations

import uuid

import qbittorrentapi
from loguru import logger

_TAG_PREFIX = "gamarr-"


class QBittorrentError(Exception):
    """Raised when a qBittorrent API call fails unrecoverably."""


class QBittorrentClient:
    """Wraps the qBittorrent WebUI API for gamarr operations.

    Args:
        host: qBittorrent hostname.
        port: qBittorrent WebUI port.
        username: WebUI username.
        password: WebUI password.
        category: Category to assign added torrents.
        add_paused: Whether to add torrents in paused state.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8080,
        username: str = "admin",
        password: str = "adminadmin",
        category: str = "games-gamarr",
        add_paused: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._category = category
        self._add_paused = add_paused
        self._client = qbittorrentapi.Client(
            host=host,
            port=port,
            username=username,
            password=password,
            VERIFY_WEBUI_CERTIFICATE=False,
        )

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        """Return True if qBittorrent is reachable and has internet access."""
        try:
            status = self._client.sync_maindata().server_state.connection_status
            return status in {"connected", "firewalled"}
        except qbittorrentapi.APIError as exc:
            logger.warning("qBittorrent connectivity check failed: {}", exc)
            return False

    # ------------------------------------------------------------------
    # Adding torrents
    # ------------------------------------------------------------------

    def add_torrent(
        self,
        magnet_url: str,
        title: str = "",
    ) -> str | bool:
        """Add a magnet link to qBittorrent and return a unique tag.

        Args:
            magnet_url: Magnet URI to add.
            title: Game title for logging purposes.

        Returns:
            A unique tag string (``"gamarr-{uuid}"``) on success,
            or ``False`` on failure.
        """
        if not magnet_url:
            logger.info("No magnet URL for '{}'; cannot add.", title)
            return False

        tag = f"{_TAG_PREFIX}{uuid.uuid4()}"
        try:
            self._client.torrents_add(
                urls=magnet_url,
                category=self._category,
                is_paused=self._add_paused,
                tags=tag,
            )
            logger.info("Added torrent '{}' with tag '{}'", title, tag)
        except qbittorrentapi.APIError as exc:
            logger.warning("Failed to add torrent '{}': {}", title, exc)
            return False

        # Reannounce the newly added torrent
        try:
            infos = self._client.torrents_info(tag=tag)
            if infos:
                self._client.torrents_reannounce(torrent_hashes=str(infos[0].hash))
        except qbittorrentapi.APIError as exc:
            logger.warning("Reannounce failed for '{}': {}; continuing.", title, exc)

        return tag
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_qbittorrent.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 6: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add qBittorrent client wrapper

- QBittorrentClient with connectivity check and torrent adding
- Gamarr-uuid tagging for traceability
- Reannounce support on newly added torrents
- Full test coverage with mocked API"
```

---

### Task 7: Notifications module

**Files:**
- Create: `src/gamarr/notifications.py`
- Create: `tests/unit/test_notifications.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_notifications.py`:

```python
"""Tests for gamarr notifications module."""

from __future__ import annotations

from gamarr.notifications import Notifier


class TestNotifier:
    """Notifier construction and basic dispatch."""

    def test_no_urls_no_error(self) -> None:
        notifier = Notifier(apprise_urls=[])
        notifier.send_download_notification(
            title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            magnet_url="magnet:?xt=urn:btih:abc",
        )
        # Should not raise

    def test_single_url(self) -> None:
        notifier = Notifier(apprise_urls=["json://localhost"])
        # Should construct without error
        assert notifier is not None

    def test_error_notification(self) -> None:
        notifier = Notifier(apprise_urls=[])
        notifier.send_error_notification(error_message="Test error")
        # Should not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_notifications.py -v`
Expected: ImportError for `gamarr.notifications`

- [ ] **Step 3: Create notifications.py**

Create `src/gamarr/notifications.py`:

```python
"""Notification dispatch for gamarr using Apprise."""

from __future__ import annotations

from loguru import logger


class Notifier:
    """Sends notifications for gamarr events via Apprise.

    Args:
        apprise_urls: List of Apprise service URLs.  An empty list
            disables all notifications.
        on_download: Notify when a new game is added to qBittorrent.
        on_failure: Notify when a game fails score checks.
        on_error: Notify on pipeline errors.
    """

    def __init__(
        self,
        apprise_urls: list[str] | None = None,
        on_download: bool = True,
        on_failure: bool = False,
        on_error: bool = False,
    ) -> None:
        self._urls = apprise_urls or []
        self._on_download = on_download
        self._on_failure = on_failure
        self._on_error = on_error
        self._apprise = self._init_apprise()

    def _init_apprise(self) -> object | None:
        """Lazily initialise the Apprise instance.

        Returns ``None`` when no URLs are configured.
        """
        if not self._urls:
            return None
        try:
            import apprise

            apobj = apprise.Apprise()
            for url in self._urls:
                apobj.add(url)
            return apobj
        except Exception as exc:
            logger.warning("Failed to initialise Apprise: {}", exc)
            return None

    # ------------------------------------------------------------------
    # Notification builders
    # ------------------------------------------------------------------

    def send_download_notification(
        self,
        title: str,
        platform: str,
        metascore: float | None,
        user_score: float | None,
        magnet_url: str,
    ) -> None:
        """Notify that a game was sent to qBittorrent."""
        if not self._on_download or not self._apprise:
            return
        body = (
            f"gamarr: {title} ({platform})\n"
            f"Metascore: {metascore or 'N/A'}\n"
            f"User Score: {user_score or 'N/A'}\n"
            f"Magnet: {magnet_url}"
        )
        self._send("gamarr - Download", body)

    def send_failure_notification(
        self,
        title: str,
        reason: str,
    ) -> None:
        """Notify that a game failed score checks."""
        if not self._on_failure or not self._apprise:
            return
        body = f"gamarr: {title} failed checks\nReason: {reason}"
        self._send("gamarr - Failed", body)

    def send_error_notification(self, error_message: str) -> None:
        """Notify about a pipeline error."""
        if not self._on_error or not self._apprise:
            return
        body = f"gamarr pipeline error:\n{error_message}"
        self._send("gamarr - Error", body)

    def _send(self, title: str, body: str) -> None:
        """Send a notification via Apprise, logging any failures."""
        if not self._apprise:
            return
        try:
            self._apprise.notify(title=title, body=body)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Failed to send notification '{}': {}", title, exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_notifications.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 6: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add Apprise notification module

- Notifier class with download, failure, and error notifications
- Lazy Apprise initialisation, graceful no-op when no URLs configured
- Full test coverage"
```

---

### Task 8: Scheduler and acquisition pipeline

**Files:**
- Create: `src/gamarr/scheduler.py`
- Create: `src/gamarr/pipeline.py`
- Create: `tests/unit/test_scheduler.py`
- Create: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_pipeline.py`:

```python
"""Tests for gamarr acquisition pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gamarr.pipeline import AcquisitionConfig, run_acquisition


class TestAcquisitionConfig:
    """AcquisitionConfig construction."""

    def test_defaults(self) -> None:
        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=90,
        )
        assert cfg.min_metascore == 75


class TestRunAcquisition:
    """End-to-end acquisition pipeline."""

    def test_no_entries_returns_early(self) -> None:
        with patch("gamarr.pipeline.FitGirlSource") as mock_source_cls:
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = []
            mock_source_cls.return_value = mock_source

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                db_path=":memory:",
                mc_cache_path=":memory:",
                qbt_host="localhost",
                qbt_port=8080,
                min_metascore=75,
                min_metascore_reviews=5,
                min_user_score=7.5,
                min_user_reviews=10,
                days_since_release=90,
                cache_ttl_days=7,
                browse_cache_ttl_hours=4,
            )
            assert results == []

    def test_entry_passes_all_checks(self) -> None:
        from gamarr.models import GameEntry

        entry = GameEntry(
            title="Test Game",
            source_title="Test Game [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:abc",
            source_url="http://example.com/test-game",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc_result = MagicMock()
            mock_mc_result.passed = True
            mock_mc_result.metascore = 85.0
            mock_mc_result.user_score = 8.0
            mock_mc_result.metascore_review_count = 50
            mock_mc_result.user_review_count = 200
            mock_mc.lookup_game.return_value = mock_mc_result
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.add_torrent.return_value = "gamarr-abc123"
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                db_path=":memory:",
                mc_cache_path=":memory:",
                qbt_host="localhost",
                qbt_port=8080,
                min_metascore=75,
                min_metascore_reviews=5,
                min_user_score=7.5,
                min_user_reviews=10,
                days_since_release=90,
                cache_ttl_days=7,
                browse_cache_ttl_hours=4,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Passed"
            assert results[0]["game_title"] == "Test Game"

    def test_entry_fails_low_score(self) -> None:
        from gamarr.models import GameEntry

        entry = GameEntry(
            title="Bad Game",
            source_title="Bad Game [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:xyz",
            source_url="http://example.com/bad-game",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc_result = MagicMock()
            mock_mc_result.passed = False
            mock_mc_result.metascore = 30.0
            mock_mc_result.user_score = 2.0
            mock_mc_result.metascore_review_count = 5
            mock_mc_result.user_review_count = 10
            mock_mc.lookup_game.return_value = mock_mc_result
            mock_mc_cls.return_value = mock_mc

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                db_path=":memory:",
                mc_cache_path=":memory:",
                qbt_host="localhost",
                qbt_port=8080,
                min_metascore=75,
                min_metascore_reviews=5,
                min_user_score=7.5,
                min_user_reviews=10,
                days_since_release=90,
                cache_ttl_days=7,
                browse_cache_ttl_hours=4,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Failed"

    def test_entry_not_found_on_metacritic(self) -> None:
        from gamarr.models import GameEntry

        entry = GameEntry(
            title="Unknown Game",
            source_title="Unknown Game [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="",
            source_url="http://example.com/unknown",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc.lookup_game.return_value = None
            mock_mc_cls.return_value = mock_mc

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                db_path=":memory:",
                mc_cache_path=":memory:",
                qbt_host="localhost",
                qbt_port=8080,
                min_metascore=75,
                min_metascore_reviews=5,
                min_user_score=7.5,
                min_user_reviews=10,
                days_since_release=90,
                cache_ttl_days=7,
                browse_cache_ttl_hours=4,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Failed"
```

Create `tests/unit/test_scheduler.py`:

```python
"""Tests for gamarr scheduler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gamarr.scheduler import run


class TestScheduler:
    """Scheduler run modes."""

    def test_run_once_calls_acquisition(self) -> None:
        with patch("gamarr.scheduler.run_acquisition") as mock_acq:
            mock_acq.return_value = []

            with patch("gamarr.scheduler.load_config") as mock_load:
                mock_config = MagicMock()
                mock_config.general.daemon_mode = "foreground"
                mock_config.schedule.acquisition.schedule_time_mins = 60
                mock_config.schedule.acquisition.run_on_start = True
                mock_config.sources.fitgirl.rss_url = "http://example.com/feed"
                mock_config.sources.fitgirl.platform = "pc"
                mock_config.general.db_path = ":memory:"
                mock_config.metacritic.platform_overrides = {"pc": MagicMock()}
                mock_config.metacritic.platform_overrides["pc"].min_metascore = 75
                mock_config.metacritic.platform_overrides["pc"].min_metascore_reviews = 5
                mock_config.metacritic.platform_overrides["pc"].min_user_score = 7.5
                mock_config.metacritic.platform_overrides["pc"].min_user_reviews = 10
                mock_config.metacritic.platform_overrides["pc"].days_since_release = 90
                mock_config.metacritic.platform_overrides["pc"].cache_ttl_days = 7
                mock_config.metacritic.platform_overrides["pc"].browse_cache_ttl_hours = 4
                mock_config.torrent_client.qbittorrent.host = "localhost"
                mock_config.torrent_client.qbittorrent.port = 8080
                mock_config.torrent_client.qbittorrent.username = "admin"
                mock_config.torrent_client.qbittorrent.password = "adminadmin"
                mock_config.torrent_client.qbittorrent.add_paused = False
                mock_config.torrent_client.qbittorrent.category = "games-gamarr"
                mock_config.notification.apprise_urls = []
                mock_config.notification.on_download = True
                mock_config.notification.on_failure = False
                mock_config.notification.on_error = False
                mock_load.return_value = mock_config

                run(config_path=":memory:")
                mock_acq.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py tests/unit/test_scheduler.py -v`
Expected: ImportError for `gamarr.pipeline` and `gamarr.scheduler`

- [ ] **Step 3: Create pipeline.py**

Create `src/gamarr/pipeline.py`:

```python
"""Acquisition pipeline for gamarr.

The pipeline is the core workflow: for each scheduler cycle it fetches
new entries from all configured sources, checks them against Metacritic
thresholds, and delivers passing entries to qBittorrent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from gamarr.database import Database
from gamarr.metacritic import MetacriticClient
from gamarr.models import GameEntry
from gamarr.notifications import Notifier
from gamarr.qbittorrent import QBittorrentClient
from gamarr.sources.fitgirl import FitGirlSource


@dataclass
class AcquisitionConfig:
    """Thresholds and settings for the acquisition run."""

    min_metascore: int
    min_metascore_reviews: int
    min_user_score: float
    min_user_reviews: int
    days_since_release: int
    cache_ttl_days: int = 7
    browse_cache_ttl_hours: int = 4


def _evaluate_scores(
    entry: GameEntry,
    mc_result: Any,
    cfg: AcquisitionConfig,
) -> str:
    """Evaluate Metacritic scores against thresholds.

    Returns ``"Passed"`` or ``"Failed"``.
    """
    if mc_result.metascore is None and mc_result.user_score is None:
        return "Failed"

    reasons: list[str] = []

    if mc_result.metascore is not None:
        if mc_result.metascore < cfg.min_metascore:
            reasons.append(f"Metascore {mc_result.metascore} < {cfg.min_metascore}")
        if mc_result.metascore_review_count is not None and mc_result.metascore_review_count < cfg.min_metascore_reviews:
            reasons.append(f"Critic reviews {mc_result.metascore_review_count} < {cfg.min_metascore_reviews}")

    if mc_result.user_score is not None:
        if mc_result.user_score < cfg.min_user_score:
            reasons.append(f"User score {mc_result.user_score} < {cfg.min_user_score}")
        if mc_result.user_review_count is not None and mc_result.user_review_count < cfg.min_user_reviews:
            reasons.append(f"User reviews {mc_result.user_review_count} < {cfg.min_user_reviews}")

    if not reasons:
        return "Passed"

    logger.info("Game '{}' failed checks: {}", entry.title, "; ".join(reasons))
    return "Failed"


def run_acquisition(
    *,
    fitgirl_rss_url: str,
    platform: str,
    db_path: str,
    mc_cache_path: str,
    qbt_host: str,
    qbt_port: int,
    qbt_username: str = "admin",
    qbt_password: str = "adminadmin",
    qbt_category: str = "games-gamarr",
    qbt_add_paused: bool = False,
    min_metascore: int = 75,
    min_metascore_reviews: int = 5,
    min_user_score: float = 7.5,
    min_user_reviews: int = 10,
    days_since_release: int = 90,
    cache_ttl_days: int = 7,
    browse_cache_ttl_hours: int = 4,
    apprise_urls: list[str] | None = None,
    notify_on_download: bool = True,
    notify_on_failure: bool = False,
    notify_on_error: bool = False,
) -> list[dict[str, Any]]:
    """Execute one acquisition cycle.

    Args:
        fitgirl_rss_url: FitGirl RSS feed URL.
        platform: Platform identifier (e.g. ``"pc"``).
        db_path: Path for the history database.
        mc_cache_path: Path for the Metacritic cache database.
        qbt_host: qBittorrent host.
        qbt_port: qBittorrent WebUI port.
        qbt_username: qBittorrent username.
        qbt_password: qBittorrent password.
        qbt_category: qBittorrent category.
        qbt_add_paused: Add torrents in paused state.
        min_metascore: Minimum Metascore threshold.
        min_metascore_reviews: Minimum critic review count.
        min_user_score: Minimum user score threshold.
        min_user_reviews: Minimum user review count.
        days_since_release: Max days since game release.
        cache_ttl_days: TTL for game detail cache.
        browse_cache_ttl_hours: TTL for browse page cache.
        apprise_urls: Apprise notification URLs.
        notify_on_download: Send notification on download.
        notify_on_failure: Send notification on failure.
        notify_on_error: Send notification on error.

    Returns:
        List of result dicts (one per processed entry).
    """
    cfg = AcquisitionConfig(
        min_metascore=min_metascore,
        min_metascore_reviews=min_metascore_reviews,
        min_user_score=min_user_score,
        min_user_reviews=min_user_reviews,
        days_since_release=days_since_release,
        cache_ttl_days=cache_ttl_days,
        browse_cache_ttl_hours=browse_cache_ttl_hours,
    )

    logger.info("Starting acquisition cycle (platform='{}')", platform)

    # Initialise dependencies
    source = FitGirlSource(rss_url=fitgirl_rss_url, platform=platform, db_path=db_path)
    mc = MetacriticClient(cache_path=mc_cache_path)
    db = Database(db_path)

    notifier = Notifier(
        apprise_urls=apprise_urls,
        on_download=notify_on_download,
        on_failure=notify_on_failure,
        on_error=notify_on_error,
    )

    # Check qBittorrent connectivity
    qbt = QBittorrentClient(
        host=qbt_host,
        port=qbt_port,
        username=qbt_username,
        password=qbt_password,
        category=qbt_category,
        add_paused=qbt_add_paused,
    )

    if not qbt.is_connected():
        logger.warning("qBittorrent is not reachable; skipping acquisition.")
        notifier.send_error_notification("qBittorrent is not reachable")
        source.close()
        mc.close()
        db.close()
        return []

    try:
        # Fetch new entries from FitGirl
        entries = source.fetch_new()
        if not entries:
            logger.info("No new entries found.")
            return []

        results: list[dict[str, Any]] = []

        for entry in entries:
            result = _process_entry(
                entry=entry,
                cfg=cfg,
                mc=mc,
                qbt=qbt,
                db=db,
                notifier=notifier,
            )
            results.append(result)

        return results

    finally:
        source.close()
        mc.close()
        db.close()


def _process_entry(
    entry: GameEntry,
    cfg: AcquisitionConfig,
    mc: MetacriticClient,
    qbt: QBittorrentClient,
    db: Database,
    notifier: Notifier,
) -> dict[str, Any]:
    """Process a single game entry through the pipeline."""
    logger.info("Processing entry: '{}'", entry.title)

    # Look up the game on Metacritic
    mc_result = mc.lookup_game(
        title=entry.title,
        platform=entry.platform,
        cache_ttl_days=cfg.cache_ttl_days,
        browse_cache_ttl_hours=cfg.browse_cache_ttl_hours,
    )

    game_title = mc_result.title if mc_result else entry.title
    metascore = mc_result.metascore if mc_result else None
    user_score = mc_result.user_score if mc_result else None

    if mc_result is None:
        # Game not found on Metacritic
        db.record_processed(
            source=entry.source,
            source_title=entry.source_url,
            game_title=entry.title,
            platform=entry.platform,
            metascore=None,
            user_score=None,
            result="Failed",
            result_details="Game not found on Metacritic",
        )
        return {
            "result": "Failed",
            "game_title": entry.title,
            "metascore": None,
            "user_score": None,
            "result_details": "Game not found on Metacritic",
        }

    # Evaluate scores
    score_result = _evaluate_scores(entry, mc_result, cfg)

    if score_result == "Failed":
        db.record_processed(
            source=entry.source,
            source_title=entry.source_url,
            game_title=game_title,
            platform=entry.platform,
            metascore=metascore,
            user_score=user_score,
            result="Failed",
            result_details=f"Metascore {metascore}, User score {user_score} below thresholds",
        )
        notifier.send_failure_notification(
            title=game_title,
            reason=f"Metascore {metascore}, User score {user_score} below thresholds",
        )
        return {
            "result": "Failed",
            "game_title": game_title,
            "metascore": metascore,
            "user_score": user_score,
            "result_details": "Score below thresholds",
        }

    # Game passed — send to qBittorrent
    magnet_url = entry.magnet_url or ""
    tag = qbt.add_torrent(magnet_url=magnet_url, title=game_title)

    if tag:
        db.record_processed(
            source=entry.source,
            source_title=entry.source_url,
            game_title=game_title,
            platform=entry.platform,
            metascore=metascore,
            user_score=user_score,
            result="Passed",
            result_details=f"Metascore {metascore}, User score {user_score}",
            magnet_url=magnet_url,
            torrent_tag=str(tag),
        )
        notifier.send_download_notification(
            title=game_title,
            platform=entry.platform,
            metascore=metascore,
            user_score=user_score,
            magnet_url=magnet_url,
        )
        logger.info("✓ Sent '{}' to qBittorrent (tag: {})", game_title, tag)
        return {
            "result": "Passed",
            "game_title": game_title,
            "metascore": metascore,
            "user_score": user_score,
            "torrent_tag": str(tag),
            "result_details": f"Metascore {metascore}, User score {user_score}",
        }
    else:
        db.record_processed(
            source=entry.source,
            source_title=entry.source_url,
            game_title=game_title,
            platform=entry.platform,
            metascore=metascore,
            user_score=user_score,
            result="Error",
            result_details="Failed to add torrent to qBittorrent",
        )
        return {
            "result": "Error",
            "game_title": game_title,
            "metascore": metascore,
            "user_score": user_score,
            "result_details": "Failed to add torrent to qBittorrent",
        }
```

- [ ] **Step 4: Create scheduler.py**

Create `src/gamarr/scheduler.py`:

```python
"""APScheduler-based daemon for gamarr.

The scheduler runs the acquisition pipeline on a configurable interval.
It supports two modes:

- ``foreground`` (default): runs one acquisition cycle then exits.
- ``--daemon``: long-running APScheduler process.
"""

from __future__ import annotations

import os
import signal
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from gamarr.config import Config, load_config
from gamarr.pipeline import run_acquisition


def run(config_path: str = "configs") -> None:
    """Start the acquisition scheduler in daemon or foreground mode.

    Args:
        config_path: Path to the config directory or file.
    """
    config = load_config(config_path)

    if config.general.daemon_mode == "background":
        _run_daemon(config)
    else:
        run_once(config)


def _build_kwargs(config: Config) -> dict[str, Any]:
    """Extract acquisition pipeline kwargs from the config."""
    mc_cfg = config.metacritic.platform_overrides.get(
        config.sources.fitgirl.platform,
    )
    if mc_cfg is None:
        # Fall back to PC defaults
        mc_cfg = config.metacritic.platform_overrides["pc"]

    return {
        "fitgirl_rss_url": config.sources.fitgirl.rss_url,
        "platform": config.sources.fitgirl.platform,
        "db_path": config.general.db_path,
        "mc_cache_path": f"{config.general.db_path}/gamarr-cache.db",
        "qbt_host": config.torrent_client.qbittorrent.host,
        "qbt_port": config.torrent_client.qbittorrent.port,
        "qbt_username": config.torrent_client.qbittorrent.username,
        "qbt_password": config.torrent_client.qbittorrent.password,
        "qbt_category": config.torrent_client.qbittorrent.category,
        "qbt_add_paused": config.torrent_client.qbittorrent.add_paused,
        "min_metascore": mc_cfg.min_metascore,
        "min_metascore_reviews": mc_cfg.min_metascore_reviews,
        "min_user_score": mc_cfg.min_user_score,
        "min_user_reviews": mc_cfg.min_user_reviews,
        "days_since_release": mc_cfg.days_since_release,
        "cache_ttl_days": mc_cfg.cache_ttl_days,
        "browse_cache_ttl_hours": mc_cfg.browse_cache_ttl_hours,
        "apprise_urls": config.notification.apprise_urls,
        "notify_on_download": config.notification.on_download,
        "notify_on_failure": config.notification.on_failure,
        "notify_on_error": config.notification.on_error,
    }


def run_once(config: Config) -> None:
    """Run a single acquisition cycle (foreground mode)."""
    logger.info("gamarr running in single-pass mode.")

    kwargs = _build_kwargs(config)
    results = run_acquisition(**kwargs)

    passed = sum(1 for r in results if r["result"] == "Passed")
    failed = sum(1 for r in results if r["result"] == "Failed")
    errors = sum(1 for r in results if r["result"] == "Error")

    logger.info("Acquisition complete: {} passed, {} failed, {} errors", passed, failed, errors)


def _run_daemon(config: Config) -> None:
    """Run the scheduler in continuous daemon mode."""
    logger.info("gamarr starting in daemon mode.")

    scheduler = BackgroundScheduler()
    acq_cfg = config.schedule.acquisition

    kwargs = _build_kwargs(config)

    scheduler.add_job(
        run_acquisition,
        trigger="interval",
        minutes=acq_cfg.schedule_time_mins,
        kwargs=kwargs,
        id="acquisition",
        name="Acquisition",
        next_run_time=None if not acq_cfg.run_on_start else None,  # APScheduler runs immediately when None
    )

    # If run_on_start is False, delay the first run
    if not acq_cfg.run_on_start:
        from datetime import datetime, timedelta
        from apscheduler.triggers.interval import IntervalTrigger

        first_run = datetime.now() + timedelta(minutes=acq_cfg.schedule_time_mins)
        scheduler.reschedule_job(
            "acquisition",
            trigger=IntervalTrigger(minutes=acq_cfg.schedule_time_mins),
            next_run_time=first_run,
        )

    scheduler.start()
    logger.info("Scheduler started (interval={} min)", acq_cfg.schedule_time_mins)

    # Handle graceful shutdown
    shutdown_event = _ShutdownEvent()
    signal.signal(signal.SIGINT, shutdown_event)
    signal.signal(signal.SIGTERM, shutdown_event)
    shutdown_event.wait()

    logger.info("Shutting down scheduler...")
    scheduler.shutdown(wait=False)


class _ShutdownEvent:
    """Simple event for waiting on shutdown signals."""

    def __init__(self) -> None:
        import threading
        self._event = threading.Event()

    def __call__(self, signum: int, _frame: object) -> None:
        logger.info("Received signal {}; shutting down...", signum)
        self._event.set()

    def wait(self) -> None:
        self._event.wait()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py tests/unit/test_scheduler.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 7: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add acquisition pipeline and scheduler

- run_acquisition: end-to-end pipeline (RSS → MC → qBT → DB)
- _process_entry: per-entry Metacritic evaluation + qBittorrent delivery
- _evaluate_scores: configurable Metascore + User Score threshold check
- Scheduler: single-pass (foreground) and APScheduler daemon modes
- Full test coverage with mocked dependencies"
```

---

### Task 9: CLI rewrite

**Files:**
- Modify: `src/gamarr/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Write failing tests for new CLI**

Replace `tests/unit/test_cli.py`:

```python
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

    def test_test_mode_validates_and_exits(self) -> None:
        """--test should run validation and exit with code 0."""
        with patch("gamarr.cli.run") as mock_run:
            result = self.runner.invoke(cli, ["--test"])
            # --test should NOT call run()
            mock_run.assert_not_called()
            assert result.exit_code == 0

    def test_daemon_flag_passed_to_run(self) -> None:
        """--daemon should result in daemon_mode=background."""
        with patch("gamarr.cli.run") as mock_run:
            self.runner.invoke(cli, ["--daemon", "--config-path", "/tmp"])
            mock_run.assert_called_once()
            # run() is called with config_path
            args, _ = mock_run.call_args
            assert "config_path" in mock_run.call_args.kwargs

    def test_default_invocation_succeeds(self) -> None:
        """Running with defaults should call run() in foreground mode."""
        with patch("gamarr.cli.run") as mock_run:
            result = self.runner.invoke(cli, [])
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_custom_log_level(self) -> None:
        """--log-level should be accepted."""
        with patch("gamarr.cli.run"):
            result = self.runner.invoke(cli, ["--log-level", "DEBUG"])
            assert result.exit_code == 0

    def test_custom_log_level_case_insensitive(self) -> None:
        """--log-level should accept lowercase values."""
        with patch("gamarr.cli.run"):
            result = self.runner.invoke(cli, ["--log-level", "debug"])
            assert result.exit_code == 0

    def test_invalid_log_level_fails(self) -> None:
        """--log-level should reject invalid values."""
        result = self.runner.invoke(cli, ["--log-level", "INVALID"])
        assert result.exit_code != 0
        assert "invalid choice" in result.output.lower()

    def test_resolve_version_returns_installed(self) -> None:
        from gamarr.cli import _resolve_version
        version = _resolve_version()
        assert version == "0.1.0"

    def test_resolve_version_fallback(self) -> None:
        with patch("gamarr.cli._pkg_version") as mock_version:
            mock_version.side_effect = PackageNotFoundError
            from gamarr.cli import _resolve_version
            assert _resolve_version() == "unknown"

    def test_config_path_option(self) -> None:
        """--config-path should be accepted."""
        with patch("gamarr.cli.run"):
            result = self.runner.invoke(cli, ["--config-path", "/tmp/configs"])
            assert result.exit_code == 0

    def test_daemon_and_test_mutually_exclusive(self) -> None:
        """--daemon and --test should be mutually exclusive."""
        result = self.runner.invoke(cli, ["--daemon", "--test"])
        assert result.exit_code != 0
```

- [ ] **Step 2: Run test to verify they fail**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_cli.py -v`
Expected: At least some tests fail because CLI doesn't have --daemon, --test, --config-path yet.

- [ ] **Step 3: Rewrite cli.py**

Replace `src/gamarr/cli.py`:

```python
"""Command-line interface for gamarr."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

import click

from gamarr.logger import create_logger
from gamarr.utils import get_project_root


def _resolve_version() -> str:
    """Return the installed package version, or 'unknown' if not installed."""
    try:
        return _pkg_version("gamarr")
    except PackageNotFoundError:
        return "unknown"


_VERSION = _resolve_version()
_PROJECT_ROOT = get_project_root()
_DEFAULT_LOGS_PATH = f"{_PROJECT_ROOT}/logs/gamarr.log"


@click.command()
@click.option(
    "--config-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default="configs",
    show_default=True,
    metavar="<dir>",
    help="Directory containing gamarr.yml configuration file.",
)
@click.option(
    "--log-level",
    default=None,
    type=click.Choice(["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"], case_sensitive=False),
    show_default=False,
    metavar="<level>",
    help="Override the console log level (default from config).",
)
@click.option(
    "--log-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<path>",
    help="Override the log file path from config.",
)
@click.option(
    "--daemon",
    is_flag=True,
    default=False,
    help="Run in continuous scheduling mode (use systemd or Docker for daemonization).",
)
@click.option(
    "--test",
    is_flag=True,
    default=False,
    help="Validate configuration and exit without running any tasks.",
)
@click.version_option(version=_VERSION, prog_name="gamarr")
def cli(
    config_path: str,
    log_level: str | None,
    log_path: str | None,
    daemon: bool,
    test: bool,
) -> None:
    """gamarr — Metadata game downloader.

    Monitors FitGirl repacks RSS feed for new game releases, checks
    them against Metacritic scores, and adds qualifying games to
    qBittorrent.

    All runtime configuration lives in the YAML config file inside
    --config-path. Use --log-level to override the console log level
    at runtime without editing the config.
    """
    # Logger setup
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<level>{message}</level>"
    )
    effective_log_path = log_path or _DEFAULT_LOGS_PATH
    effective_log_level = log_level.upper() if log_level else "INFO"
    create_logger(
        log_format=log_format,
        log_level=effective_log_level,
        log_path=effective_log_path,
    )

    from gamarr.scheduler import run  # noqa: PLC0415

    if test:
        # Validate config by loading it
        from gamarr.config import load_config  # noqa: PLC0415

        load_config(config_path)
        click.echo("Configuration loaded successfully. Test mode — exiting.")
        return

    # For daemon mode, update config so scheduler reads daemon_mode=background
    if daemon:
        from gamarr.config import Config, load_config  # noqa: PLC0415

        config = load_config(config_path)
        config.general.daemon_mode = "background"
        # Write the updated config back so the scheduler picks it up
        import yaml  # noqa: PLC0415
        import os  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        config_dir = Path(config_path)
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "gamarr.yml"
        with config_file.open("w", encoding="utf-8") as fh:
            yaml.dump(config.model_dump(), fh, default_flow_style=False, sort_keys=False)

    run(config_path=config_path)


if __name__ == "__main__":
    cli()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_cli.py -v`
Expected: All 13 tests PASS

- [ ] **Step 5: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 6: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: rewrite CLI with daemon/test/config-path options

- --test mode for config validation
- --daemon flag for continuous scheduling
- --config-path for config directory override
- --log-level / --log-path overrides
- All Click options with help text"
```

---

### Task 10: Default config YAML and conftest

**Files:**
- Create: `configs/gamarr.yml`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create default config YAML**

Create `configs/gamarr.yml`:

```yaml
general:
  config_version: "1.0.0"
  daemon_mode: foreground
  log_level_console: INFO
  log_level_file: INFO
  log_path: logs
  db_path: db

schedule:
  acquisition:
    enabled: true
    schedule_time_mins: 60
    run_on_start: true

sources:
  fitgirl:
    enabled: true
    rss_url: https://fitgirl-repacks.site/feed/
    platform: pc

metacritic:
  platform_overrides:
    pc:
      min_metascore: 75
      min_metascore_reviews: 5
      min_user_score: 7.5
      min_user_reviews: 10
      days_since_release: 90
      cache_ttl_days: 7
      browse_cache_ttl_hours: 4

torrent_client:
  selected: qbittorrent
  qbittorrent:
    host: localhost
    port: 8080
    username: admin
    password: adminadmin
    add_paused: false
    category: games-gamarr

notification:
  apprise_urls: []
  on_download: true
  on_failure: false
  on_error: false

database:
  processed_expiry_days: 365
```

- [ ] **Step 2: Create conftest.py**

Create `tests/conftest.py` with shared fixtures:

```python
"""Shared test fixtures for gamarr tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from gamarr.config import Config
from gamarr.models import GameEntry

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sample_game_entry() -> GameEntry:
    """Return a minimal valid GameEntry for use in pipeline tests."""
    return GameEntry(
        title="Test Game",
        source_title="Test Game (v1.0) [Repack]",
        source="fitgirl",
        platform="pc",
        magnet_url="magnet:?xt=urn:btih:abc123",
        source_url="https://fitgirl-repacks.site/test-game/",
    )


@pytest.fixture
def default_config() -> Config:
    """Return a default Config instance."""
    return Config()


@pytest.fixture
def tmp_db_path(tmp_path: "Path") -> str:
    """Return a temporary database directory path."""
    db_dir = tmp_path / "db"
    db_dir.mkdir(parents=True)
    return str(db_dir)
```

- [ ] **Step 3: Run full test suite + coverage**

Run: `cd /data/gamarr && uv run pytest --cov=src/gamarr --cov-report=term-missing -v`
Expected: All tests PASS with coverage >= 95%

- [ ] **Step 4: Run full QA gate**

Run:
```bash
cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && echo "=== ruff ok ==="
cd /data/gamarr && uv run mypy . && echo "=== mypy ok ==="
cd /data/gamarr && uv run pytest -v --cov=src/gamarr --cov-fail-under=95 && echo "=== pytest ok ==="
cd /data/gamarr && uv run pre-commit run --all-files && echo "=== pre-commit ok ==="
```

Expected: All gates pass.

- [ ] **Step 5: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add default config YAML and test conftest

- configs/gamarr.yml with all default settings
- Shared test fixtures (GameEntry, Config, temp DB path)"
```

---

## Spec Coverage Check

| Spec Section | Task Implementing It |
|---|---|
| 2.1 Pipeline Flow | Task 8 (pipeline.py) |
| 2.2 Runtime Modes | Task 9 (CLI --daemon/--test) + Task 8 (scheduler.py) |
| 2.3 Source Abstraction | Task 1 (BaseSource protocol) |
| 3 Config Model | Task 2 (config.py) + Task 10 (default YAML) |
| 4 FitGirl RSS Source | Task 4 (sources/fitgirl.py) |
| 5 Metacritic Integration | Task 5 (metacritic.py + metacritic_cache.py) |
| 6 Database | Task 3 (database.py) |
| 7 CLI | Task 9 (cli.py) |
| 8 Scheduler | Task 8 (scheduler.py) |
| 9 qBittorrent | Task 6 (qbittorrent.py) |
| 10 Notifications | Task 7 (notifications.py) |
| 12 Dependencies | Task 1 (pyproject.toml) |
