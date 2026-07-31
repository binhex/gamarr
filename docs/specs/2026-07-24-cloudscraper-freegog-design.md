# cloudscraper for FreeGOG Cloudflare Bypass

**Date:** 2026-07-24
**Status:** approved

## Problem

`freegogpcgames.com` is behind Cloudflare Turnstile managed challenge. Plain `requests`
with a Chrome user-agent returns 403 with `cf-mitigated: challenge`. The FreeGOG source
in gamarr can no longer fetch the A-Z game list or individual game pages.

## Solution

Replace `requests` with `cloudscraper` in `src/gamarr/sources/freegog.py`.
cloudscraper is a drop-in replacement that impersonates browser TLS fingerprints
and solves Cloudflare JS challenges automatically. It exposes the same `.get()`,
`.text`, `.raise_for_status()` API as `requests`.

## Design

### `src/gamarr/sources/freegog.py`

Three edits:

1. **Import:** `import requests` → `import cloudscraper`

2. **Session:** Add a `cloudscraper.create_scraper()` instance as an attribute on
   `FreeGOGSource`, created once at `__init__`, reused across all requests.
   This avoids per-request JS engine initialization overhead.

3. **Replace call sites:** All three `requests.get()` calls become
   `self._scraper.get()`:

   | Method | Line | Change |
   |--------|------|--------|
   | `_fetch_and_store_game` | `game_resp = requests.get(...)` | `self._scraper.get(...)` |
   | `_index_az_page` | `resp = requests.get(...)` | `self._scraper.get(...)` |

   The `User-Agent` header is removed — cloudscraper handles browser
   impersonation internally.

### `pyproject.toml`

Add `cloudscraper` to `[project] dependencies`.

### What does NOT change

- `_parse_freegog_az_page()` — string in, list out, unchanged
- `_extract_magnet_from_freegog_page()` — string in, magnet or None out, unchanged
- `_clean_freegog_title()` — string processing, unchanged
- `Database` methods, caching logic, error handling patterns — unchanged
- All other sources (FitGirl) — no transitive dependency impact
- `arch-gamarr` Docker build — `uv sync` already runs at build time and picks up
  the new dependency automatically

### Error handling

cloudscraper's `.get()` raises `requests.RequestException` subclasses and
supports `.raise_for_status()`. The existing `except requests.RequestException`
blocks and HTTP error handling continue to work without modification.

## Trade-offs

| Approach | Pros | Cons |
|----------|------|------|
| **cloudscraper (chosen)** | Drop-in, lightweight, no new service | May need updates if Cloudflare changes challenge logic |
| flaresolverr/byparr sidecar | Real browser, most reliable | Adds container dependency, slower (seconds per page) |
| selenium/playwright | Real browser | Heavy, requires display deps in Docker |

cloudscraper is the right fit: FreeGOG is low-traffic periodic polling (not bulk
scraping), and the lightweight approach keeps gamarr self-contained.

## Test plan

cloudscraper is an external dependency — integration testing against the live
FreeGOG site is the only way to verify the Cloudflare bypass works. The existing
unit tests for `_parse_freegog_az_page`, `_extract_magnet_from_freegog_page`,
and `_clean_freegog_title` remain unchanged and pass with the new import.
