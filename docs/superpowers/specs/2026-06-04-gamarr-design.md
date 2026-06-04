# gamarr — Design Specification

**Date:** 2026-06-04
**Status:** Draft
**Version:** 1.0.0

## 1. Overview

gamarr is an automated daemon that harvests game torrent metadata. It monitors
the FitGirl repacks RSS feed for new titles, checks each title against
Metacritic's critic and user scores, and automatically sends qualifying games
to qBittorrent for download.

The architecture is inspired by [movarr](https://github.com/binhex/movarr) and
uses the same patterns: Pydantic YAML config, APScheduler daemon, SQLAlchemy
history database, and Apprise notifications. The Metacritic scraping logic is
adapted from [gamecritic](https://github.com/binhex/gamecritic).

## 2. Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  gamarr daemon (APScheduler)                                  │
│                                                               │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────┐   │
│  │  FitGirl     │───►│  Metacritic  │───►│  qBittorrent   │   │
│  │  RSS Source  │    │  Score Check │    │  Client        │   │
│  └─────────────┘    └──────────────┘    └────────────────┘   │
│        │                   │                     │            │
│        ▼                   ▼                     ▼            │
│  ┌──────────────────────────────────────────────────────┐     │
│  │              SQLite Database (processed titles,       │     │
│  │              history, MC cache)                       │     │
│  └──────────────────────────────────────────────────────┘     │
│        ▲                                                      │
│  ┌─────┴──────────┐                                           │
│  │  config.yml     │  (Pydantic, YAML, versioned)              │
│  └────────────────┘                                           │
│                                                               │
│  Notifications: Apprise (on download, on error)               │
└───────────────────────────────────────────────────────────────┘
```

### 2.1 Pipeline Flow (per scheduler cycle)

1. Fetch FitGirl RSS feed → parse new article entries
2. Skip any titles already recorded in the history database
3. Clean the game title (strip `[Repack]`, version info, language tags)
4. Look up the game on Metacritic PC browse → get Metascore + User Score
5. If Metascore >= threshold AND User Score >= threshold → proceed
6. Send the magnet link to qBittorrent with tag + category
7. Record the result (passed/failed) in the history database
8. Send Apprise notification on successful download

### 2.2 Runtime Modes

- **Daemon mode** (`--daemon`): Long-running foreground process using
  APScheduler. Polls FitGirl on a configurable interval (default: 60 min).
- **Single-pass mode** (no flags): Runs acquisition once, then exits.
  Useful for testing and manual runs.
- **Test mode** (`--test`): Validates config, checks connectivity to
  qBittorrent and Metacritic, then exits without running acquisition.

### 2.3 Source Abstraction

gamarr uses a `BaseSource` protocol so that future sources (Jackett/Prowlarr
for Nintendo Switch, Dodi repacks, Ankergames) can be added without changing
the pipeline. Each source returns `GameEntry` objects with a consistent shape:

```python
@dataclass
class GameEntry:
    title: str          # cleaned game name
    source_title: str   # original title from source
    source: str         # "fitgirl", "dodi", etc
    platform: str       # "pc", "nintendo-switch"
    magnet_url: str     # magnet link for download
    source_url: str     # original article/release URL
```

For V1 only the FitGirl RSS source is implemented. The abstract base class
is created to prove the architecture without building unused implementations.

## 3. Config Model

File: `configs/gamarr.yml` — Pydantic-validated YAML with versioned migrations.

```yaml
general:
  config_version: "1.0.0"
  daemon_mode: foreground          # "foreground" or "background"
  log_level_console: INFO
  log_level_file: INFO
  log_path: logs
  db_path: db

schedule:
  acquisition:
    enabled: true
    schedule_time_mins: 60
    run_on_start: true             # check immediately on startup

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
  enabled_notifications:
    on_download: true
    on_failure: false
    on_error: false

database:
  processed_expiry_days: 365
```

### 3.1 Config Design Notes

- `metacritic.platform_overrides` is a dict keyed by platform string.
  Adding Nintendo Switch in a future release is a new key, not a schema
  change.
- Each source has `enabled: true/false` so sources can be toggled
  independently without removing their config.
- Config versioning follows movarr's pattern: each migration is a
  function that updates the raw dict, and the file is rewritten on disk.

## 4. FitGirl RSS Source

### 4.1 RSS Parsing

Fetch `https://fitgirl-repacks.site/feed/` using `requests` with
exponential backoff (reusing gamecritic's proven `http_client` pattern).
Parse with `xmltodict` (already used in movarr).

Each RSS `<item>` contains:
```xml
<item>
  <title>Game Name (v1.0 + DLCs, MULTi13) [Repack]</title>
  <link>https://fitgirl-repacks.site/game-name/</link>
  <pubDate>Mon, 01 Jun 2026 12:00:00 +0000</pubDate>
  <description>...</description>
</item>
```

### 4.2 Title Cleansing

Strip known suffixes from the RSS title to extract the canonical game name:
- `[Repack]` suffix
- Version strings: `(v1.0)`, `(v1.0 + DLCs, MULTi13)`, etc.
- Language tags: `MULTi5`, `MULTi13`, `Ru/En`, etc.
- Any parenthesised technical metadata

Examples:
| Raw Title | Cleaned Title |
|---|---|
| `Elden Ring (v1.12 + DLCs, MULTi13) [Repack]` | `Elden Ring` |
| `Baldur's Gate 3 (v4.1.1.4...) [Repack]` | `Baldur's Gate 3` |
| `Hades II [Repack]` | `Hades II` |

### 4.3 Magnet URL Extraction

The magnet link is typically embedded in the RSS `<description>` or on the
article page. The source will:
1. First attempt to extract any magnet link from the RSS description
2. Fall back to scraping the article page (the `<link>` URL) if no magnet
   is found in the RSS

### 4.4 Database Skip

Every entry's source_url is recorded in the history database. On each cycle,
entries whose `source_url` is already in the DB are skipped — no re-processing.

## 5. Metacritic Integration

### 5.1 Title Lookup Strategy

Two-step approach:

1. **Direct slug guess** — Clean the game title and construct a Metacritic
   slug (lowercase, hyphens, remove special characters). Try fetching
   `metacritic.com/game/{slug}/`. If the page exists, extract scores from
   the embedded Nuxt JSON state (proven in gamecritic's `_parse_game_details`).

2. **Browse fallback** — If direct guess fails (wrong slug), scan Metacritic
   PC browse pages (newest first) using gamecritic's paginated browser
   parser (`fetch_browse_page`) to find a fuzzy title match.

### 5.2 Score Extraction

Reuse gamecritic's Nuxt JSON parsing logic:
- `Metascore` from `criticScoreSummary.score` in browse data
- `User Score` from `userScore.score` in browse data, then refined from
  the individual game page via `_parse_game_details`
- Cache results in gamecritic-compatible SQLite cache tables
  (`game_detail_cache`, `browse_page_cache`)

### 5.3 Scoring Rules

A game passes if ALL of these are met (configurable per platform):
- `Metascore >= min_metascore` (default: 75)
- `Critic Reviews >= min_metascore_reviews` (default: 5)
- `User Score >= min_user_score` (default: 7.5)
- `User Reviews >= min_user_reviews` (default: 10)
- Game release is within `days_since_release` (default: 90)

### 5.4 Caching

Two separate caches in a single SQLite file (`db/gamarr-cache.db`):
- **Browse page cache**: TTL measured in hours (default: 4h)
- **Game detail cache**: TTL measured in days (default: 7d)

Both are purge-safe and separate from the history database.

## 6. Database

### 6.1 History Table

Tracks every title processed by any source:

```sql
CREATE TABLE history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_title TEXT NOT NULL,
    game_title TEXT,
    platform TEXT NOT NULL,
    metascore REAL,
    user_score REAL,
    result TEXT NOT NULL,           -- "Passed" | "Failed" | "Error"
    result_details TEXT,            -- human-readable chain of reasons
    magnet_url TEXT,
    torrent_tag TEXT,               -- set when added to qBittorrent
    processed_at TEXT NOT NULL      -- ISO 8601 timestamp
);

CREATE INDEX idx_history_source_url ON history(source);
CREATE INDEX idx_history_processed ON history(processed_at);
```

### 6.2 Schema Notes

- SQLAlchemy ORM (same as movarr), with a `Base` declarative base and
  `HistoryRecord` model class.
- The `torrent_tag` field links to qBittorrent for future queue management
  features (e.g., monitoring stalled downloads).
- Old records beyond `processed_expiry_days` can be pruned automatically
  on startup.

## 7. CLI

Minimal command-line interface. All runtime configuration lives in the
YAML config file; CLI flags exist only for the most common overrides.

```bash
gamarr                          # single-pass acquisition, then exit
gamarr --daemon                 # continuous scheduler mode
gamarr --test                   # validate config and exit
gamarr --config-path ./configs  # config directory override
gamarr --log-level DEBUG        # override console log level
```

### 7.1 CLI Design Notes

- `--version` via Click decorator (already in the skeleton)
- `--daemon` and `--test` are mutually exclusive
- No per-option overrides for V1 (unlike movarr's extensive override
  system). Config file is the single source of truth.

## 8. Scheduler

Uses `APScheduler` with a `BackgroundScheduler` (same as movarr):

- **daemon mode**: `--daemon` flag starts the scheduler, runs the
  acquisition task on the configured interval. PID file written to
  `config.general.pid_path`.
- **foreground / single-pass mode**: Runs `run_once()` — acquisition
  executes once, then the process exits.
- **Task**: One task for V1: `acquisition` — fetches FitGirl RSS,
  processes new titles. Future tasks: queue_management, post_processing
  (when those features are added).

## 9. qBittorrent Integration

Reuses movarr's `QBittorrentClient` pattern (adapted):

- **Connection**: `qbittorrent-api` library
- **Authentication**: Host, port, username, password from config
- **Adding torrents**: `client.torrents_add(urls=magnet_url, category=category, tags=tag)`
- **Tagging**: Each added torrent gets a `gamarr-{uuid}` tag for traceability
- **Health check**: `is_connected()` to verify qBittorrent is reachable
  before attempting to add torrents

## 10. Notifications

Uses `apprise` library (same as movarr):

- **on_download**: A new game passed all checks and was sent to qBittorrent
- **on_failure**: A game failed Metacritic checks (optional)
- **on_error**: Pipeline error (Metacritic unreachable, config invalid, etc.)

Notification payload includes: game title, platform, scores, and magnet link.

## 11. File Layout

```
src/gamarr/
├── __init__.py
├── cli.py                  # Click CLI entry point
├── config.py               # Pydantic YAML config + migrations
├── database.py             # SQLAlchemy history database
├── scheduler.py            # APScheduler daemon
├── metacritic.py           # Metacritic scrape + score lookup
├── qbittorrent.py          # qBittorrent client wrapper
├── notifications.py        # Apprise notification dispatcher
├── models.py               # GameEntry, HistoryRecord TypedDicts
├── sources/
│   ├── __init__.py         # BaseSource protocol
│   └── fitgirl.py          # FitGirl RSS fetcher + parser
├── logger.py               # existing
└── utils.py                # existing

tests/
├── unit/
│   ├── test_cli.py         # existing
│   ├── test_logger.py      # existing
│   ├── test_utils.py       # existing
│   ├── test_config.py
│   ├── test_fitgirl.py
│   ├── test_metacritic.py
│   └── test_qbittorrent.py
└── conftest.py
```

## 12. Dependencies (additions to existing pyproject.toml)

The skeleton already has most dependencies. New additions:

| Package | Purpose |
|---|---|
| `pydantic>=2.0.0` | Config validation (unlike skeleton's current no-Pydantic setup) |
| `qbittorrent-api` | qBittorrent WebUI client |
| `xmltodict` | RSS feed parsing |
| `beautifulsoup4` | HTML parsing for Metacritic + magnet extraction |
| `lxml` | BeautifulSoup parser backend |

Existing packages reused: `apscheduler`, `backoff`, `click`, `loguru`,
`apprise`, `pyyaml`, `requests`, `sqlalchemy`, `urllib3`.

## 13. Future Expansion Paths

The architecture is designed to support these planned additions without
major refactoring:

| Feature | Design Impact |
|---|---|
| **Nintendo Switch (Jackett/Prowlarr)** | New source implementing `BaseSource`. Platform=`nintendo-switch`. New `metacritic.platform_overrides` entry with potentially different thresholds. |
| **Dodi repacks** | New source implementing `BaseSource`. Platform=`pc`. Reuses existing PC Metacritic thresholds. |
| **Ankergames** | Same as Dodi — new source, existing platform. |
| **Queue management** | New scheduler task + reuse movarr's `identify_for_deletion` pattern. |
| **Post-processing** | New scheduler task when game metadata download is complete. |

## 14. Design Decisions Log

| Decision | Choice | Rationale |
|---|---|---|
| Config format | YAML + Pydantic | Matches movarr, validated, versioned |
| Daemon mode | APScheduler | Matches movarr, proven pattern |
| Database | SQLAlchemy + SQLite | Already a dependency, prevents re-processing |
| Source abstraction | Protocol/ABC | Future-proof for Jackett, Dodi, Ankergames |
| Score thresholds | Metascore + User Score | Double gate for quality |
| Title matching | Direct slug + browse fallback | Fast path for common case |
| Cache | SQLite (separate DB) | Reuses gamecritic pattern, purge-safe |
| CLI overrides | Minimal (no per-option overrides) | Config is the single source of truth in V1 |
| Metacritic scraping | BeautifulSoup + Nuxt JSON | Proven in gamecritic |
