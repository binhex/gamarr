# Search Mode — Split Backlog and Latest Scanning

**Date:** 2026-07-08
**Status:** Draft

## Problem

Backlog scanning (deep traversal through `max_pages` of Metacritic content) and
latest-game scanning (the first N pages for steady-state monitoring) are currently
conflated in a single pipeline path.  Both behaviors use the same config fields
(`max_pages`, `max_cycle_pages`) and the mode switch from "backlog" to "latest"
happens implicitly when the backlog is exhausted — the code resets progress and
silently becomes a latest-page scanner.

This conflation causes:

- **Confusion:** the same config fields control two different behaviors, and the
  implicit switch is invisible to the user.
- **Coding issues:** the pipeline must handle both modes in one code path with
  conditional branches scattered throughout.
- **Unwanted mode switches:** a user who wants only latest-page monitoring may
  inadvertently trigger backlog traversal when `max_pages` is set.

The user wants **explicit, user-controlled separation** between these two scanning
modes so each behaves consistently and predictably.

## Design

### Config

New field `search_mode` on `MetacriticPlatformConfig` (`src/gamarr/config.py`):

```python
search_mode: Literal["backlog", "latest"] = "latest"
```

Default is `"latest"` — new users start with steady-state monitoring.

**Config migration:** No explicit migration function is needed.  The existing
infrastructure handles both cases automatically:

- **Existing configs without `search_mode`:** `_deep_merge()` merges the
  default `"latest"` value in.  `_needs_config_update()` detects the new key
  and writes the updated config file with a bumped `config_version`.
- **New installs:** `create_default_config()` writes the full default config
  (via `Config.model_dump()`) which includes `search_mode: latest`.

**YAML example:**

```yaml
review_sites:
  metacritic:
    platform_overrides:
      pc:
        search_mode: backlog       # "latest" (default) or "backlog"
        max_pages: 13              # total backlog depth (backlog mode only)
        max_cycle_pages: 4         # per-cycle pace / latest-page window
        sort_order: new
```

Validation:

- `search_mode == "latest"` and `max_cycle_pages == 0` → runs unbounded (all pages
  in one cycle).
- `search_mode == "backlog"` and `max_cycle_pages > max_pages` → cap
  `max_cycle_pages` to `max_pages` (no reason to pace beyond the backlog).
- `max_queue_days == 0` (indefinite) works identically in both modes.

The field flows through `scheduler.py` → `run_acquisition()` → `AcquisitionConfig`
dataclass, matching the pattern every other per-platform field uses.

### Database

Two new pending-game tables mirroring the existing `pending_games` schema.  All
other tables (history, backlog progress, scan state, cache) remain shared.

| Table | Used by | Purpose |
|---|---|---|
| `pending_games_backlog` | Backlog mode | Independently managed pending queue |
| `pending_games_latest` | Latest mode | Independently managed pending queue |
| `backlog_progress` | Backlog mode only | Per-year page tracking (unchanged) |
| `history` | Both (shared) | Cross-mode dedup via `mc:{slug}` |
| `scan_state` | Both (shared) | `last_sort_order` (unchanged) |
| cache tables | Both (shared) | Metacritic browse/detail page cache |

Both new tables have the same column set as the existing `pending_games`:
`slug` (PK), `game_title`, `platform`, `metascore`, `metascore_reviews`,
`user_score`, `user_reviews`, `release_date`, `score_checks_passed`,
`last_checked_at`, `created_at`, `expires_at`.

**Migration:** On first startup after the change, `Database.__init__()` creates
both tables via `Base.metadata.create_all()`.  If the legacy `pending_games`
table contains rows, all are copied into `pending_games_backlog` (since old data
logically belongs to backlog mode).  The original table is preserved as a safety
net, not dropped.

**API surface:** The `Database` class gains mode-specific methods:

- `get_backlog_pending()` / `get_latest_pending()` — query mode-specific table
- `record_backlog_pending(...)` / `record_latest_pending(...)`
- `remove_backlog_pending(slug)` / `remove_latest_pending(slug)`
- `touch_backlog_pending(slug)` / `touch_latest_pending(slug)`
- `update_backlog_pending_scores(slug, ...)` / `update_latest_pending_scores(slug, ...)`
- `update_backlog_pending_expiry(slug, ...)` / `update_latest_pending_expiry(slug, ...)`
- `get_expired_backlog_pending()` / `get_expired_latest_pending()`
- `is_backlog_pending(slug)` / `is_latest_pending(slug)`
- `has_verified_backlog_pending()` / `has_verified_latest_pending()`
- `get_known_backlog_slugs(...)` / `get_known_latest_slugs(...)`

All methods are named explicitly (not parameter-routed) — the pipeline always
knows the mode when calling into the database, so separate methods prevent
accidental mode confusion.

### Pipeline Behavior

**File:** `src/gamarr/pipeline.py` — `_run_discovery_phases()`

The browse phase branches on `search_mode`:

#### Backlog mode (`search_mode == "backlog"`)

Current behavior preserved with one change to backlog exhaustion:

1. Detect sort_order change → clear cache + reset backlog progress if changed.
2. Calculate cutoff year from `max_pages`.
3. Year-loop: `range(cutoff_year, current_year + 1)`.
4. For each year: resume from `db.get_last_scanned_page() + 1`, scan up to
   `max_cycle_pages` pages.
5. Track progress: `set_last_scanned_page`, `sum_scanned_pages`,
   `_log_backlog_progress()`.
6. **When backlog is exhausted** (all pages scanned): log
   `"Backlog complete — X of X pages scanned. Switch to search_mode: latest for ongoing monitoring."`
   and **do NOT reset progress**.  The cycle produces no new pending games but
   still runs score verification and source matching for existing pending entries.
   This respects the explicit mode boundary — backlog mode stays backlog mode,
   it does not silently become a latest-page scanner.

#### Latest mode (`search_mode == "latest"`)

New simplified path:

1. No sort_order detection / backlog reset — not relevant.
2. No year-loop — scan only the current year (or `None` for metascore sort).
3. Always start from page 1 — no progress tracking, no resume.
4. Scan exactly pages 1 to `max_cycle_pages` (or unbounded if 0).
5. No `_log_backlog_progress()` — simple `"Scanning latest N pages"` log.
6. Games flow into `pending_games_latest`.

#### Converged phases

After browse, both modes proceed through the same post-discovery phases, each
targeting the mode-specific pending table:

| Phase | Backlog target | Latest target |
|---|---|---|
| Score verification | `_verify_pending_scores(..., search_mode="backlog")` | `...(search_mode="latest")` |
| Source matching | `_match_pending_games(..., search_mode="backlog")` | `...(search_mode="latest")` |
| JIT verify + deliver | `pending_games_backlog` | `pending_games_latest` |

### Mode Switching

| Transition | Behavior |
|---|---|
| `latest` → `backlog` | Resumes from last scanned page if progress exists; otherwise starts fresh from page 1.  Existing backlog pending queue preserved. |
| `backlog` → `latest` | Backlog progress and pending queue preserved untouched.  Latest mode scans page 1..`max_cycle_pages` fresh each cycle. |
| `backlog` → `backlog` (same mode, no change) | Resumes from last scanned page. |
| `latest` → `latest` (same mode, no change) | Starts from page 1 each cycle. |
| Backlog re-run after completion | Progress is at `max_pages` — cycles produce nothing.  User must manually reset backlog progress (future CLI feature) or keep `search_mode: latest`. |

### Affected Functions

| Function | Change |
|---|---|
| `AcquisitionConfig` dataclass | Add `search_mode` field |
| `run_acquisition()` | Add `search_mode` parameter |
| `_run_discovery_phases()` | Branch on `search_mode` for browse phase |
| `_process_browse_games()` | Accept `search_mode`; dispatch to correct `record_pending` |
| `_verify_pending_scores()` | Accept `search_mode`; query correct pending table |
| `_match_pending_games()` | Accept `search_mode`; query correct pending table |
| `_log_backlog_progress()` | Only called in backlog mode |
| `_jit_verify_and_update()` | Accept `search_mode`; update correct pending table |
| `_is_game_known()` | Accept `search_mode`; check correct pending table |
| `_build_kwargs()` (scheduler.py) | Read `search_mode` from `mc_cfg` and pass through |

### Testing

- **Unit tests:** `test_config.py` — validate `search_mode` defaults, literal
  constraint, and migration path for missing field.
- **Unit tests:** `test_pipeline.py` — cover backlog mode (progress tracking,
  year-loop, exhaustion log), latest mode (page-1 start, no progress tracking),
  and mode-aware DB dispatch.
- **Integration:** `test_database.py` — verify mode-specific pending table
  creation, migration from legacy `pending_games`, and CRUD dispatch.

### Non-Goals

- A CLI flag to override `search_mode` at runtime.  This is config-only for now.
- A backlog-progress reset feature (future CLI enhancement).
- Changing existing `backlog_progress` or `scan_state` table schemas.
- Per-mode score thresholds or `max_queue_days` — both modes share the same
  threshold config.
