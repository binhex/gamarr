# gamarr

Metadata game downloader — monitors FitGirl repacks RSS for new game releases,
checks them against Metacritic scores, and sends qualifying games to
qBittorrent.

## Description

gamarr is an automated daemon that harvests torrent metadata for PC games. It
checks the FitGirl repacks RSS feed for new releases, looks up each title on
Metacritic, and automatically adds games that meet the configured score
thresholds to qBittorrent.

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

1. **FitGirl RSS** — Fetches the FitGirl repacks RSS feed for new game releases
2. **Title cleaning** — Strips repack metadata (versions, language tags) from titles
3. **Metacritic lookup** — Looks up each game on Metacritic and fetches critic
   and user scores
4. **Score filtering** — Passes games that meet both Metascore and User Score
   thresholds (default: 75 / 7.5)
5. **qBittorrent** — Adds passing games to qBittorrent with a `gamarr-*` tag
6. **History** — Records all processed titles in a SQLite database to avoid
   re-processing
7. **Notifications** — Optional Apprise notifications on new downloads

## Architecture

The codebase is structured as a pipeline:

```
sources/fitgirl.py  →  metacritic.py  →  pipeline.py  →  qbittorrent.py
       ↓                   ↓                  ↓              ↓
   RSS fetch         Score lookup       Eval + DB       Add torrent
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
