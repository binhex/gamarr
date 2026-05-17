# gamarr

Metadata game downloader — downloads torrent metadata (torrent files and magnet links) for games.

This software is under *HEAVY* development right now, expect lack of documentation, major bugs and missing functionality.

## Description

gamarr is an automated tool for harvesting torrent metadata for games. It searches configured indexers, collects torrent and magnet link metadata, and stores it for downstream consumption.

The name "gamarr" follows the *arr convention — a focused automation daemon for game-related torrent metadata.

## Prerequisites

- [Python 3.12+](https://www.python.org/downloads/)
- [Astral uv](https://github.com/astral-sh/uv#installation)

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
gamarr --help
```

## Options

WIP

## Development

```bash
git clone https://github.com/binhex/gamarr
cd gamarr
uv venv --quiet
uv sync --extra dev
```

If you wish to perform linting on all files before committing (PR will not be
accepted if it does not pass all linting) then run `pre-commit run --all-files`.

## FAQ

WIP

___
If you appreciate my work, then please consider buying me a beer  :D

[![PayPal donation](https://www.paypal.com/en_US/i/btn/btn_donate_SM.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=MM5E27UX6AUU4)
