# Remove search_mode, unify browse loop

**Date:** 2026-07-31
**Status:** Approved

## Motivation

gamarr currently has two browse modes (`search_mode: backlog` and `search_mode: latest`) with separate config, separate database tables (`pending_games_backlog` / `pending_games_latest`), and separate code paths. The modes have converged to the point where the only real difference is `sort_order` handling — backlog allows `new` or `metascore`, latest forces `new`. The latest mode also has a hard-coded `max_cycle_pages` cap with no `max_pages` budget, meaning it only ever scans the first N pages per cycle and never reaches deeper backlog.

This design eliminates `search_mode` entirely, consolidates to a single pending table, and unifies the browse loop with an infinite `max_pages`-budgeted cycle that resets to page 1 when exhausted — the user controls depth via `max_pages`, pace via `max_cycle_pages`, and sort direction via `sort_order`.

## Design

### 1. Config

- **Remove:** `search_mode` key from `review_sites.metacritic.platform_overrides.<platform>`
- **Keep unchanged:** `sort_order` (`"new"` or `"metascore"`), `max_pages`, `max_cycle_pages`, all score thresholds, `reject_genre`, `reject_title`
- **Migration:** config version bump strips `search_mode` from existing config files on load

### 2. Database

- **Consolidate tables:** `pending_games_backlog` and `pending_games_latest` → single `pending_games` table (the legacy table already exists with all required columns including `score_checks_passed`)
- **Migration on startup:** `INSERT OR IGNORE` rows from both mode-specific tables into `pending_games`, then drop the mode-specific tables
- **Method consolidation (~20 methods):** all `_backlog`/`_latest` method pairs merge into single methods:

| Before | After |
|--------|-------|
| `get_backlog_pending` / `get_latest_pending` | `get_pending(platform)` |
| `record_backlog_pending` / `record_latest_pending` | `record_pending(...)` |
| `remove_backlog_pending` / `remove_latest_pending` | `remove_pending(slug)` |
| `touch_backlog_pending` / `touch_latest_pending` | `touch_pending(slug)` |
| `update_backlog_pending_scores` / `update_latest_pending_scores` | `update_pending_scores(slug, result, max_queue_days)` |
| `get_known_backlog_slugs` / `get_known_latest_slugs` | `get_known_slugs(source, platform)` |
| `has_verified_backlog_pending` / `has_verified_latest_pending` | `has_verified_pending(platform)` |
| `is_backlog_pending` / `is_latest_pending` | `is_pending(slug)` or removed |
| `reset_backlog_progress` | `reset_progress(platform, sort_order)` |

### 3. Unified Browse Loop

The `search_mode` branch in `run_acquisition()` collapses into a single loop. Pseudo-logic:

```
sort_order = cfg.sort_order
mc.sort_order = sort_order

# Detect sort_order change and reset progress if needed
if previous_sort_order != sort_order:
    reset_progress(platform, sort_order)
    clear_cache("metacritic")

# Calculate year range
if sort_order == "new":
    years_back = ceil(max_pages / 52)
    cutoff_year = current_year - years_back
else:  # metascore — no year dimension
    cutoff_year = 0

total_scanned = sum_scanned_pages(platform, cutoff_year, current_year)
max_pages_cfg = max_pages or 0

# Budget check — auto-reset when exhausted
if max_pages_cfg > 0 and total_scanned >= max_pages_cfg:
    logger.info("Budget exhausted — {} of {} pages scanned, restarting from page 1", ...)
    reset_progress(platform, sort_order)
    total_scanned = 0

remaining = max_pages_cfg - total_scanned if max_pages_cfg > 0 else 0

# Year loop
for scan_year in range(cutoff_year, current_year + 1):
    if cancelled or remaining <= 0: break

    start_page = get_last_scanned_page(platform, scan_year) + 1
    per_call_max = min(max_cycle_pages, remaining) if max_pages_cfg > 0 else max_cycle_pages

    games = scan_recent_games(
        platform,
        start_page=start_page,
        max_pages=per_call_max,
        year=scan_year if sort_order == "new" else None,
    )
    browse_games.extend(games)

    last_page = mc._recent_games_last_page or 0
    set_last_scanned_page(platform, scan_year, last_page)

    if max_pages_cfg > 0 and last_page >= start_page:
        remaining -= (last_page - start_page + 1)

set_last_sort_order(platform, sort_order)
```

**Key properties:**
- Infinite loop — when `max_pages` budget is exhausted, progress resets to page 1 and the cycle repeats forever
- Per-cycle page count capped by both `max_cycle_pages` and remaining budget
- `sort_order` is never overridden — user has full control between `"new"` and `"metascore"`
- Year-loop only applies to `sort_order == "new"` (releases have years); `"metascore"` uses year=0 sentinel for all-time ranking

### 4. Pipeline Helpers — Removal

All `_by_mode` dispatch helpers in `pipeline.py` are removed. Callers switch to direct `db.*` calls:

- `_get_known_slugs_by_mode` → `db.get_known_slugs(source, platform)`
- `_record_pending_by_mode` → `db.record_pending(...)`
- `_remove_pending_by_mode` → `db.remove_pending(slug)`
- `_touch_pending_by_mode` → `db.touch_pending(slug)`
- `_update_pending_scores_by_mode` → `db.update_pending_scores(slug, result, max_queue_days)`

### 5. End-of-Cycle Logging

End-of-cycle summary log line is updated to remove `search_mode` references:

```
"{} games browsed, {} queued, {} matched, {} delivered"
```

The existing backlog progress log is replaced with a simpler budget-tracking log:

```
"Progress: {} of {} pages scanned ({}% — {} pages remaining this cycle)"
```

### 6. Migration

- **Config migration:** `_CONFIG_VERSION` bumped; migration step drops `search_mode` from loaded config dicts
- **DB migration:** On `Database.__init__`, if `pending_games_backlog` or `pending_games_latest` exist: `INSERT OR IGNORE` into `pending_games`, drop the source tables, log a one-time migration message
- **Data preservation:** All pending games, processed history (`processed_games`), and page progress (`scanned_pages`) survive unchanged

## Scope and non-goals

**In scope:**
- Remove `search_mode` config key with migration
- Consolidate `pending_games_backlog` + `pending_games_latest` → `pending_games`
- Merge all `_backlog`/`_latest` DB method pairs
- Unify the browse loop with `max_pages` budget, `max_cycle_pages` cap, progress tracking, and infinite auto-reset
- Remove five `_by_mode` pipeline helpers
- Update all tests referencing `search_mode`, mode-specific tables, or `_by_mode` helpers

**Out of scope:**
- Changes to score thresholds, genre/keyword rejection, download site matching, delivery, or post-processing
- Changes to the config schema beyond removing `search_mode`
- Changes to the `processed_games` or `scanned_pages` tables
- Performance optimizations beyond what the consolidation naturally provides
