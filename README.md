# gamarr

Metadata game downloader — browses Metacritic for newly released games that
pass configured score thresholds, matches them against the FitGirl repacks
sitemap, and sends qualifying games to qBittorrent.

## Features

- **Two-phase score verification** — browse pages use internal Metacritic
  metrics (not real 0–100 scores) to build a candidate pool quickly.
  Browse scores are on a completely different scale (e.g. 1478, 1931) and
  **always pass** the configured thresholds — the real 0–100 score
  filtering only happens during the detail-page verification step.
  This avoids hundreds of slow HTTP requests while keeping score accuracy.
- **Genre-based rejection** — exclude games by genre using case-insensitive
  substring matching (e.g. `["RPG"]` rejects "Action RPG", "JRPG", etc.).
- **Keyword-based title filtering** — reject games whose titles contain
  specific keywords.
- **Relative date cutoff** — set a look-back window in weeks instead of
  maintaining a static date (e.g. `cutoff_weeks: 52` for roughly one year).
- **Re-verification with retry limit** — games whose real Metacritic detail
  page scores don't match the browse page scores are kept in a pending queue
  for re-verification, up to a configurable maximum number of attempts.
- **Two-phase pending expiry** — games awaiting score review expire after
  `pending_days` (under metacritic thresholds). Once scores pass, a fresh
  expiry window (`download_sites.fitgirl.pending_days`) starts for the
  FitGirl-matching phase. Set either value to `0` for indefinite pending.
- **FitGirl sitemap matching** — fetches the FitGirl repacks sitemap only
  when there are verified games to match against.
- **Database deduplication** — every processed game is recorded in SQLite;
  previously processed titles are never re-processed.
- **Configurable cache TTLs** — Metacritic scores and browse pages are cached
  with independent TTLs to reduce HTTP requests.
- **Notifications** — sends alerts via any
  [apprise](https://github.com/caronc/apprise)-compatible service (ntfy,
  Discord, Telegram, email, and more).
- **Schedule mode** — runs as a scheduled background process, or in foreground mode for
  single-pass execution.
- **Automatic config migration** — upgrades the YAML config schema
  automatically on startup when new fields are added.

## Prerequisites

- [Python 3.12+](https://www.python.org/downloads/)
- [Astral uv](https://github.com/astral-sh/uv#installation)
- [qBittorrent](https://www.qbittorrent.org/) with WebUI enabled

## Quick start

### Installation

```bash
git clone https://github.com/binhex/gamarr
cd gamarr
uv venv --quiet
uv sync
```

### Usage

```bash
# Validate configuration
gamarr --test

# Run a single acquisition cycle
gamarr
```

## CLI Options

| Option | Description |
| ------ | ----------- |
| `--config-path <dir>` | Directory containing `gamarr.yml` (default: `configs`) |
| `--log-level <level>` | Override console log level (DEBUG, INFO, SUCCESS, WARNING, ERROR) |
| `--log-path <path>` | Override log file path |
| `--db-path <dir>` | Override the database directory from config |
| `--pid-path <dir>` | Override the PID file directory from config |
| `--library-path <path>` | Override library paths (repeatable: `--library-path /a --library-path /b`) |
| `--qbt-host <host>` | Override qBittorrent host from config |
| `--qbt-port <port>` | Override qBittorrent WebUI port from config |
| `--qbt-username <user>` | Override qBittorrent username from config |
| `--qbt-password <pass>` | Override qBittorrent password from config |
| `--test` | Validate configuration and exit |
| `--version` | Show version and exit |
| `--help` | Show help message and exit |

## Configuration

All behaviour is controlled by a YAML file inside the config directory
(`configs/gamarr.yml` by default). A default config is created automatically
on first run. The file is divided into the sections below.

### `general`

| Key | Description | Default |
| --- | ----------- | ------- |
| `config_version` | Schema version — managed automatically; do not edit. | *(current)* |
| `daemon_mode` | `foreground` or `background`. Scheduling is controlled by `schedule.acquisition.enabled`. This field is deprecated. | `foreground` |
| `log_level_console` | Console logging level. | `INFO` |
| `log_level_file` | File logging level. | `INFO` |
| `log_path` | Directory for log files (`gamarr.log` is created inside). | `logs` |
| `db_path` | Directory for the SQLite history database (`gamarr.db` is created inside). | `db` |

### `schedule`

| Key | Description | Default |
| --- | ----------- | ------- |
| `acquisition.enabled` | Enable or disable the acquisition task. | `true` |
| `acquisition.schedule_time_mins` | Interval in minutes between acquisition cycles. | `60` |
| `acquisition.run_on_start` | Run acquisition immediately on start, before the first interval. | `true` |

### `download_sites`

| Key | Description | Default |
| --- | ----------- | ------- |
| `fitgirl.enabled` | Enable the FitGirl source. | `true` |
| `fitgirl.rss_url` | FitGirl repacks RSS feed URL. | `https://fitgirl-repacks.site/feed/` |
| `fitgirl.platform` | Target platform for matching. | `pc` |
| `fitgirl.cache_ttl_hours` | How long to cache the FitGirl sitemap before re-fetching. | `6` |
| `fitgirl.reject_keywords` | Reject FitGirl repack titles containing any of these keywords (case-insensitive). | `[]` |
| `fitgirl.pending_days` | How many days a game stays in the pending queue *after* its scores are verified (the FitGirl-matching phase). `0` = indefinite pending (no expiry). | `60` |

### `metacritic`

#### `metacritic.platform_overrides.<platform>`

| Key | Description | Default |
| --- | ----------- | ------- |
| `min_metascore` | Minimum Metacritic critic score (0–100). | `75` |
| `min_metascore_reviews` | Minimum number of critic reviews required. | `10` |
| `min_user_score` | Minimum Metacritic user score (0–10). | `7.5` |
| `min_user_reviews` | Minimum number of user reviews required. | `10` |
| `cutoff_weeks` | Look-back window in weeks from today. Games released before this are skipped. `null` or `0` = no cutoff. | `null` |
| `enabled` | Enable or disable the Metacritic browse step. Disabling skips game discovery entirely. | `true` |
| `max_games` | Maximum number of games to scan per cycle (0 = unlimited). | `1000` |
| `pending_days` | Days a game stays in the pending queue before expiring. `0` = indefinite pending (no expiry). | `30` |
| `cache_ttl_days` | Days to cache Metacritic detail-page results. | `7` |
| `cache_ttl_hours` | Hours to cache Metacritic browse-page results. | `6` |
| `reject_genre` | Reject games whose Metacritic genre contains any of these substrings (case-insensitive). E.g. `["RPG"]` matches "Action RPG", "JRPG", "Western RPG". | `[]` |
| `reject_title` | Reject games whose title contains any of these substrings (case-insensitive). E.g. `["Remake"]` matches "Resident Evil 4 Remake", "Remake Collection". | `[]` |

### `torrent_client`

| Key | Description | Default |
| --- | ----------- | ------- |
| `selected` | Torrent client to use. Currently only `qbittorrent` is supported. | `qbittorrent` |
| `qbittorrent.host` | qBittorrent Web UI hostname or IP address. | `localhost` |
| `qbittorrent.port` | qBittorrent Web UI port. | `8080` |
| `qbittorrent.username` | Web UI username. | `admin` |
| `qbittorrent.password` | Web UI password. | `adminadmin` |
| `qbittorrent.add_paused` | Add torrents in paused state. | `false` |
| `qbittorrent.category` | Category tag applied to all gamarr-managed torrents. | `games-gamarr` |

### `notification`

| Key | Description | Default |
| --- | ----------- | ------- |
| `apprise_urls` | List of [apprise](https://github.com/caronc/apprise) service URLs. Leave empty to disable. | `[]` |
| `on_download` | Send a notification when a game is successfully added to qBittorrent. | `true` |
| `on_failure` | Send a notification when a game fails verification after max attempts. | `false` |
| `on_error` | Send a notification when an error occurs during the acquisition cycle. | `false` |
| `on_scrape_failure` | Send a notification when Metacritic scraping appears to be broken. | `true` |

### `database`

| Key | Description | Default |
| --- | ----------- | ------- |
| `processed_expiry_days` | Delete processed history records older than this many days. | `365` |

### `library`

| Key | Description | Default |
| --- | ----------- | ------- |
| `paths` | List of library root paths to scan when checking whether a game already exists. | `[]` |

## How It Works

### Acquisition pipeline

```mermaid
flowchart TD
    A([Start cycle]) --> B{Platform\nenabled?}
    B -- No --> END([End])
    B -- Yes --> C[Browse Metacritic\nnewest-first]
    C --> D[Parse browse-page\nscores & titles]
    D --> E[For each game]
    E --> F{Exclude\nkeyword\nin title?}
    F -- Yes --> SKIP1([⛔ Skip])
    F -- No --> G{Meets score\nthresholds on\nbrowse page?}
    G -- No --> SKIP2([⛔ Skip])
    G -- Yes --> H{Within cutoff\nweeks?}
    H -- No --> SKIP3([⛔ Skip])
    H -- Yes --> I[Add to pending\nqueue]

    I --> J[For each pending game]
    J --> K[Look up real scores\non Metacritic\ndetail page]
    K --> L{Genre matches\nreject_genre?}
    L -- Yes --> FAIL1([❌ Remove -\nrejected genre])
    L -- No --> M{Real scores\npass thresholds?}
    M -- Yes --> N[Mark as verified\nwith real scores]
    M -- No --> O{Attempts <\nmax_verify\n_attempts?}
    O -- Yes --> P([Keep in queue\nfor re-check])
    O -- No --> FAIL2([❌ Remove -\nmax attempts])

    N --> Q[Match verified\ngames against\nFitGirl sitemap]
    Q --> R{FitGirl match\nfound?}
    R -- No --> STAY([Stay in queue])
    R -- Yes --> S[Fetch magnet link\nfrom FitGirl page]
    S --> T[Add to qBittorrent\nwith gamarr-* tag]
    T --> U([Record in\nhistory])

    END --> V{Daemon\nmode?}
    V -- Yes --> A
    V -- No --> W([Exit])
```

### Detailed phases

1. **Metacritic browse** — Scans Metacritic browse pages (newest-first) for
   games matching the target platform. **Important:** browse pages show
   *internal browse-only metrics*, not the real 0–100 critic scores or
   0–10 user scores. These rough scores are used only to build a candidate
   pool — the real filtering happens in step 4. Up to `max_games` entries
   are collected.
2. **Browse-page filtering** — Games whose titles match `reject_title` are
   skipped. Games outside the `cutoff_weeks` window
   are skipped. **Note:** browse scores are on a different scale and always
   exceed the configured thresholds — score filtering effectively starts
   at the verification step (phase 4), not here.
3. **Pending queue** — Surviving games enter a `pending_games` queue with a
   configurable expiry (`metacritic.platform_overrides.*.pending_days`,
   default 30, or `0` for indefinite). They wait for a detail-page
   verification pass.
4. **Score verification** — Each pending game's real Metacritic detail page
   is fetched. The real 0–100 critic score and 0–10 user score are compared
   against configured thresholds. Games whose real scores fail the checks
   are kept for re-verification.
   **When scores pass**, the game's expiry is recalculated to
   `now + download_sites.fitgirl.pending_days` (default 60, or `0` for
   indefinite), giving it a fresh window for the FitGirl-matching phase.
5. **Genre rejection** — Before score verification, the game's genres
   (extracted from the detail page) are checked against `reject_genre`.
   Matching games are removed from pending immediately — no score
   evaluation or re-verification (genres never change).
6. **FitGirl sitemap fetch** — Only when there are verified games in the
   queue. The FitGirl repacks sitemap is fetched and indexed.
7. **Source matching** — Verified games are matched against the FitGirl
   sitemap by title. The best-matching repack title gets its magnet link
   fetched.
8. **Delivery** — Matched games are added to qBittorrent with a `gamarr-*`
   tag. The result is recorded in the history database.
9. **Notifications** — Optional Apprise notifications on download, failure,
   or error.

## Architecture

The codebase is structured as a **Metacritic-first** pipeline:

```text
metacritic.py  →  pipeline.py  →  sources/fitgirl.py  →  qbittorrent.py  
_(Note: `sources/` is the Python package name for download site implementations — distinct from the config key.)
       ↓              ↓                   ↓                    ↓
   Browse new    Score filter +      Sitemap match         Add torrent
   releases      pending queue       + magnet fetch
```

All configuration is driven by a YAML file (`configs/gamarr.yml`) validated
with Pydantic. The scheduler (`scheduler.py`) runs in single-pass mode by
default, or in continuous scheduled mode when
``schedule.acquisition.enabled`` is ``true``.

## Development

```bash
git clone https://github.com/binhex/gamarr
cd gamarr
uv venv --quiet
uv sync --extra dev
```

Before committing, run the full lint suite:

```bash
pre-commit run --all-files
```

### Running tests

```bash
uv run pytest
```

## FAQ

**Q: What download sites are supported?**

**A:** Currently FitGirl repacks RSS. The architecture supports adding more
download sites (Jackett/Prowlarr, Dodi, Ankergames) via the `BaseSource` protocol.

**Q: Can I add Nintendo Switch games?**

**A:** Planned for a future release via Jackett/Prowlarr integration.

**Q: What if qBittorrent is not running?**

**A:** gamarr will log a warning and skip the acquisition cycle.

**Q: How do I reject specific game genres?**

**A:** Use the `reject_genre` option under `metacritic.platform_overrides.pc`.
It uses case-insensitive substring matching. For example:

- `["RPG"]` matches "Action RPG", "Western RPG", "JRPG", "RPG"
- `["Western RPG"]` only matches genres containing "Western RPG"
- `["Action"]` matches "Action", "Action RPG", "Open-World Action"

See the [genres section](#metacriticplatform_overridesplatform) for details.

___
If you appreciate my work, then please consider buying me a beer  :D

[![PayPal donation](https://www.paypal.com/en_US/i/btn/btn_donate_SM.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=MM5E27UX6AUU4)
