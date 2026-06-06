# gamarr

Metadata game downloader — browses Metacritic for newly released games that
pass configured score thresholds, matches them against the FitGirl repacks
sitemap, and sends qualifying games to qBittorrent.

## Description

gamarr is an automated daemon that harvests torrent metadata for PC games. It
follows a **Metacritic-first** acquisition flow: it browses Metacritic for
newly released titles, filters them by critic and user score thresholds, then
matches surviving games against the FitGirl repacks sitemap. Matched games
are added to qBittorrent with a `gamarr-*` tag.

The name "gamarr" follows the *arr convention — a focused automation daemon
for game-related torrent metadata.

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

### Configuration

Edit `configs/gamarr.yml` to set your qBittorrent connection details and
Metacritic score thresholds:

```yaml
torrent_client:
  qbittorrent:
    host: localhost
    port: 8080
    username: admin
    password: your-password-here

metacritic:
  platform_overrides:
    pc:
      min_metascore: 75
      min_user_score: 7.5
```

### Usage

```bash
# Validate configuration
gamarr --test

# Run a single acquisition cycle
gamarr

# Run as a continuous daemon (polling every 60 minutes)
gamarr --daemon
```

## CLI Options

| Option | Description |
|--------|-------------|
| `--config-path <dir>` | Directory containing `gamarr.yml` (default: `configs`) |
| `--log-level <level>` | Override console log level (DEBUG, INFO, SUCCESS, WARNING, ERROR) |
| `--log-path <path>` | Override log file path |
| `--daemon` | Run in continuous scheduling mode |
| `--test` | Validate configuration and exit |
| `--version` | Show version and exit |
| `--help` | Show help message and exit |

## How It Works

1. **Metacritic browse** — Scans Metacritic's browse pages for newly released
   games and pulls critic and user scores
2. **Score filtering** — Keeps games that meet both Metascore and User Score
   thresholds (default: 75 / 7.5) and the release-date window
3. **Pending queue** — Surviving games enter a short-lived `pending_games`
   queue (default expiry: 30 days)
4. **FitGirl sitemap indexing** — Fetches the FitGirl repacks sitemap into a
   local index of titles and source URLs (only when there are pending games
   to match against)
5. **Source matching** — Pending games are matched against the FitGirl
   sitemap; the magnet link is fetched from the matched source URL
6. **qBittorrent** — Matched games are added to qBittorrent with a `gamarr-*`
   tag
7. **History** — All processed titles are recorded in a SQLite database to
   avoid re-processing
8. **Notifications** — Optional Apprise notifications on new downloads

## Architecture

The codebase is structured as a **Metacritic-first** pipeline:

```text
metacritic.py  →  pipeline.py  →  sources/fitgirl.py  →  qbittorrent.py
       ↓              ↓                   ↓                    ↓
   Browse new    Score filter +      Sitemap match         Add torrent
   releases      pending queue       + magnet fetch
```

All configuration is driven by a YAML file (`configs/gamarr.yml`) validated
with Pydantic. The scheduler (`scheduler.py`) supports both single-pass and
continuous daemon modes via APScheduler.

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

**Q: What sources are supported?**
A: Currently FitGirl repacks RSS. The architecture supports adding more
sources (Jackett/Prowlarr, Dodi, Ankergames) via the `BaseSource` protocol.

**Q: Can I add Nintendo Switch games?**
A: Planned for a future release via Jackett/Prowlarr integration.

**Q: What if qBittorrent is not running?**
A: gamarr will log a warning and skip the acquisition cycle.

___
If you appreciate my work, then please consider buying me a beer  :D

[![PayPal donation](https://www.paypal.com/en_US/i/btn/btn_donate_SM.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=MM5E27UX6AUU4)
