# DODI Repacks Source Support

**Date:** 2026-06-17
**Status:** Approved design

## Overview

Add DODI repacks as a second torrent source alongside FitGirl. DODI
uploads to 1337x.to, so instead of an RSS/sitemap, we scrape the
user's upload page, extract magnet links from each torrent's detail
page, and index them in the same `source_titles` table used by FitGirl.
An ordered config list lets users control source priority.

---

## 1. Config — Ordered Source List

The current flat `download_sites` dict becomes an ordered list where
position = priority. An auto-migration converts existing configs.

```yaml
download_sites:
  - name: fitgirl
    enabled: true
    rss_url: "https://fitgirl-repacks.site/feed/"
    platform: pc
    cache_pages_hours: 6
    reject_keywords: []
    max_queue_days: 60
  - name: dodi
    enabled: true
    platform: pc
    cache_pages_hours: 6
    reject_keywords: []
    max_queue_days: 60
```

The `name` field dispatches to the correct source class
(`FitGirlSource` or `DODISource`). Name-specific fields (like
`rss_url`) coexist with shared fields.

**Migration:** On startup, detect the old flat `download_sites.fitgirl`
structure and convert it to a 1-element ordered list, preserving all
existing field values. The migration functions in `config.py` already
handle similar renames — this follows the same pattern.

---

## 2. DODISource — Scraping Logic

**File:** `src/gamarr/sources/dodi.py`

Implements the same `BaseSource` protocol as `FitGirlSource`.

### How 1337x.to works for this use case

- User page: `https://1337x.to/user/DODI/` — paginated HTML (`/1/`, `/2/`, ...)
- Each page lists ~50 torrents with title, seeds, leeches, date
- Torrent title links to detail page: `https://1337x.to/torrent/<ID>/<slug>/`
- Detail page contains the `magnet:` URI in the page HTML

### Backfill flow (first run)

```
1. Fetch user page 1 → parse torrent entries + extract page count
2. Fetch remaining pages → collect all {title, detail_url} pairs
3. For each detail_url → fetch page → extract magnet link
4. Store all entries via rebuild_source_titles("dodi", ...)
```

### Incremental flow (subsequent cycles)

```
1. Fetch user page 1 → get newest torrent's slug
2. Compare against most recent slug stored in DB
3. If no new entries → skip (cache is valid)
4. If new entries exist → fetch only the new pages needed, fetch their
   detail pages for magnets, append to source_titles
```

### Anti-bot protection

Use `cloudscraper` (drop-in replacement for `requests`) to handle
Cloudflare challenges:

```python
import cloudscraper
session = cloudscraper.create_scraper()
resp = session.get("https://1337x.to/user/DODI/")
```

### Title cleaning

Strip DODI repack metadata for matching against Metacritic titles:

- Remove `.DODI` suffix (case-insensitive)
- Remove version/bracket metadata: `(v1.0.3)`, `[MULTi]`, etc.
- Normalize separators (dots → spaces, etc.)

### Magnet storage

A new nullable `magnet` column is added to the `SourceTitle` ORM model.
Magnets are fetched eagerly during the scrape (not on-demand at match
time) and stored in this column alongside the title and URL. This means
delivery is instant once a match is found — no extra HTTP at match time.

Database changes:
- `SourceTitle.magnet: Mapped[str | None]` — nullable string column
- `rebuild_source_titles()` signature updated to accept
  `list[dict[str, str | None]]` where dicts may include `"magnet"` key
- `get_all_source_titles()` and `match_source_title()` include the
  `magnet` value in returned dicts

---

## 3. Pipeline Integration — Ordered Source Matching

### Current flow

```
1. Verify pending games against Metacritic
2. Fetch FitGirl sitemap (only if verified pendings exist)
3. Match ALL pending against FitGirl → deliver matched, remove from pending
4. Age out old pendings
```

### New flow

```
1. Verify pending games against Metacritic
2. For each source in config order [fitgirl, dodi, ...]:
     a. Fetch/refresh that source's index (if verified pendings exist)
     b. Match REMAINING pending against that source
        → deliver matched games via qBittorrent
        → unmatched games stay in pending for next source
3. Age out old pendings (games unmatched by ANY source)
```

Delivered games are removed from `pending` by the existing
`_record_result` + `db.remove_pending()` path. Games that don't match
source A remain in pending and get a chance with source B. This
naturally implements priority without reconciliation logic.

### Changes to `_match_pending_games`

The function currently hardcodes `"fitgirl"` in calls to
`db.match_source_title("fitgirl", ...)`. This becomes a parameter
passed from the caller so the same logic handles any source.

The source-level config (reject_keywords, max_queue_days) is also
parameterized per source.

---

## 4. Testing

### New test file: `tests/unit/test_dodi.py`

| Test | What it verifies |
|------|-----------------|
| `test_parse_torrent_list_page` | Parse sample 1337x user page HTML → correct title/URL/page count |
| `test_extract_magnet_from_page` | Parse sample detail page → correct magnet URI |
| `test_clean_title` | Strip DODI suffixes, version strings, brackets |
| `test_pagination_urls` | Generate correct page URLs from page number |

### Additions to `tests/unit/test_pipeline.py`

| Test | What it verifies |
|------|-----------------|
| `test_ordered_sources_matching` | Two sources; Game1 on source A, Game2 on source B → each game delivered by correct source |
| `test_source_priority_ordering` | Game on both A and B, A first in config → A delivers, B skips |
| `test_source_fallback` | Game only on B → A fails to match, B picks it up |
| `test_config_migration_ordered_list` | Old flat config auto-migrates to new ordered list format |

Sample 1337x HTML pages are stored as inline strings in
`tests/fixtures/dodi/` or defined as module-level constants in the test
file.

---

## 5. Error Handling & Anti-Scraping

| Concern | Strategy |
|---------|----------|
| **Rate limiting** | 1-2s `time.sleep()` between detail page fetches during backfill |
| **Partial failure** | Failed detail page → log warning, skip that torrent, continue |
| **Missing magnets** | Dead torrents without magnet → stored as `magnet: null`, excluded from matching |
| **Cloudflare** | `cloudscraper` handles JS challenges automatically; 2 retries with 5s backoff as safety net |
| **Config migration** | Automatic migration from flat `download_sites.fitgirl.*` to ordered list on first load with new binary |
| **Empty index** | If scrape returns 0 entries, use cached index from previous cycle |
| **Incremental skip** | No new entries on page 1 → cache is valid → skip all fetches |

---

## 6. Files Changed

| File | Change |
|------|--------|
| `src/gamarr/sources/dodi.py` | **New** — DODISource implementation |
| `src/gamarr/sources/__init__.py` | Add `DODISource` to exports (optional — protocol-based) |
| `src/gamarr/config.py` | `DownloadSitesConfig` becomes ordered list v1 of `SourceConfigEntry`; add migration for old flat format |
| `src/gamarr/pipeline.py` | Iterate sources in config order; parameterize `_match_pending_games` by source name |
| `src/gamarr/database.py` | Add nullable `magnet` column to `SourceTitle`; update `rebuild_source_titles` to accept optional `magnet` field; update `get_all_source_titles` and `match_source_title` to return `magnet` in dicts |
| `pyproject.toml` | Add `cloudscraper` dependency |
| `tests/unit/test_dodi.py` | **New** — unit tests for DODI parsing |
| `tests/unit/test_pipeline.py` | Add ordered-source matching tests |

---

## 7. Open Questions

None — the design has been reviewed and approved.
