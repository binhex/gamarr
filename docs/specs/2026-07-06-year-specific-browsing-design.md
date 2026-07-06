# Year-Specific Metacritic Browsing

**Date:** 2026-07-06
**Status:** Approved

## Motivation

gamarr currently uses the all-time Metacritic browse list to discover games:

```
https://www.metacritic.com/browse/game/pc/all/all-time/new/
```

This URL covers only the most recent ~1,656 pages (~40,000 games), which bottoms out around the year 2021. Games from 2020, 2019, 2018 and earlier are never discovered, regardless of how far back `max_weeks` is set. The catalogue simply runs out of pages.

Metacritic provides year-specific browse URLs that go back to 1998:

```
https://www.metacritic.com/browse/game/pc/all/2024/metascore/?page=0
```

Switching to year-specific URLs expands the discoverable catalogue from ~40,000 games to the entire Metacritic PC back-catalogue (potentially 100,000+ games across 28 years).

## Design

### URL Construction

The browse URL changes from `all-time` to year-specific:

**Before:**
```
/browse/game/{platform}/all/all-time/new/?page={N}
```

**After:**
```
/browse/game/{platform}/all/{year}/{sort_order}/?page={N}
```

The `year` is determined from the current `cutoff_date` in the pipeline. The `sort_order` is a new config key (see below).

### New Config Key

A new `sort_order` field is added to `MetacriticPlatformConfig`:

```yaml
review_sites:
  metacritic:
    platform_overrides:
      pc:
        sort_order: newest   # or "metascore"
```

- **`sort_order`** (`str`, default `"newest"`): Controls the Metacritic browse sort order. Valid values: `"newest"` (sorts by release date, newest first) or `"metascore"` (sorts by Metacritic score). Applied to the year-specific browse URL.
- Schema default is `"newest"` to match the current all-time sort behaviour.
- Config migration is handled automatically by existing schema machinery.

### Backlog Scanning

Each backlog cycle scans pages for the year derived from `cutoff_date`:

```python
# In run_acquisition, after computing cutoff_date:
scan_year = datetime.strptime(cutoff_date, "%Y-%m-%d").year
browse_games = mc.scan_recent_games(
    platform, cutoff_date=cutoff_date, year=scan_year, ...
)
```

The `scan_recent_games()` function gains a new `year` parameter:
- When `year` is provided, fetches pages for that year only
- When `year` is `None`, uses the legacy all-time URL (backward compat for the direct-slug fallback path in `_scan_browse_pages`)
- Each year's page list starts from page 0 and is self-contained

Cutoff filtering works exactly as before: each game has a `release_date`, and games with dates before the cutoff (or newer than `cutoff + max_cycle_weeks`) are excluded. The pipeline has a new upper-bound check that limits collected games to `[cutoff_date, cutoff_date + max_cycle_weeks]` so each cycle only processes a `max_cycle_weeks`-sized window.

The cycle flow for a typical backlog setup (e.g., `max_weeks=104, max_cycle_weeks=4`):

```
Cycle 1:  cutoff = 2026-06-08 → year=2026, scan 2026 pages, collect 4-week window
Cycle 2:  cutoff = 2026-05-11 → year=2026, re-scan 2026 pages (cached), collect next 4 weeks
...
Cycle ~13: cutoff = 2025-12-01 → year=2025, scan 2025 pages (first cache miss)
...
Cycle ~26: cutoff = 2024-12-01 → year=2024, scan 2024 pages (first cache miss)
...
Continues stepping back through years until max_weeks boundary is reached
```

Re-scanning the same year's pages across multiple cycles (e.g., 13 cycles for 2026 at 4 weeks each) is fast because pages are cached in SQLite after first fetch.

### Cache Key Change

The browse page cache key changes from `(platform, page_number)` to `(platform, year, page_number)`. This ensures each year's pages are cached independently. The cache TTL (`cache_pages_hours`) is unchanged.

**Database migration:** The `browse_page_cache` table schema is updated to add a `year` column. Existing cache entries (all-time, pre-migration) are unaffected — their `year` column will be `NULL`.

### Removal: browse_start_page

The `browse_start_page` / `last_browse_page` mechanism is removed from both `ScanState` and the pipeline. It was needed for the all-time list because that was one continuous page catalogue — the backlog needed to know which page to resume from. With individual year pages, each year is a self-contained list starting from page 0. No resume point is needed.

### Backward Compatibility

- **Direct slug scan:** `_scan_browse_pages()` continues to use `scan_recent_games(year=None)`, which uses the legacy all-time URL. This path is only used when looking up a specific game by title and is unaffected.
- **Config migration:** The `sort_order` key has a default value of `"newest"`, so existing config files work without modification.
- **Page cache:** Existing all-time cache entries (with `year=NULL`) coexist with new year-specific entries via the `year` column.

## Implementation

### Files Changed

| File | Change |
|------|--------|
| `src/gamarr/config.py` | Add `sort_order: str = "newest"` to `MetacriticPlatformConfig` |
| `src/gamarr/metacritic.py` | URL construction, `year` parameter on `scan_recent_games`, cache key change, `_fetch_browse_page` update |
| `src/gamarr/pipeline.py` | Pass `year` from cutoff to `scan_recent_games`; remove `browse_start_page` / `last_browse_page` logic |
| `src/gamarr/database.py` | Remove `last_browse_page` column from `ScanState`; add `year` column to browse page cache; migration |

### Removed

- `ScanState.last_browse_page` column (with migration to drop)
- `_migrate_scan_state` — simplified to only manage `last_max_weeks` column
- All `browse_start_page`, `get_last_browse_page`, `set_last_browse_page` references in `pipeline.py`

## Testing

### Test Impact

| File | Changes |
|------|---------|
| `tests/unit/test_metacritic.py` | Update URL format assertions to match `/{year}/{sort}/` pattern. Test `year` parameter, year=None backward compat. Test per-year page iteration. |
| `tests/unit/test_config.py` | Test `sort_order` default ("newest") and validation (rejects invalid values). |
| `tests/unit/test_pipeline.py` | Update or remove tests referencing `browse_start_page` / `last_browse_page`. Test year derived from cutoff. |
| `tests/unit/test_database.py` | Test migration for `year` column in browse_page_cache. Remove `last_browse_page` tests. |

## What Stays the Same

- `max_weeks` and `max_cycle_weeks` — unchanged semantics
- Game parsing (`_resolve_browse_game_list`, `_parse_browse_page`) — unchanged
- Score filtering, genre rejection, keyword matching — unchanged
- Database dedup (`_is_game_known`, `record_pending`) — unchanged
- `cache_pages_hours` — unchanged TTL control
- `reject_genre`, `reject_title`, `reject_keywords` — unchanged
- All downloadable/source code — unchanged
- Notifications — unchanged
