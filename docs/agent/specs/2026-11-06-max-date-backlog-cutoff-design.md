# Design: date-based backlog cutoff (`max_date`) alongside the page budget

Date: 2026-11-06
Status: Approved (user reviewed design in session; spec written for implementation)

## Goal

Let the Metacritic backlog cutoff be expressed as a **date** instead of a page
count, because pages are meaningless to a human and do not correspond to games
reviewed: one page of zero-score games costs a page and yields nothing.

Constraints that shaped the design:

1. Score-ordered browsing (`sort_order: criticscore` / `userscore`) cannot use a
   date cutoff. Its browse list is score-ordered across all years, so a single
   page mixes 2015 and 2025 releases. Only `sort_order: new` browses per year,
   newest first.
2. Therefore pages cannot be removed. `max_pages` / `max_cycle_pages` remain the
   bound and pace for score modes.
3. The change is therefore **additive**: a new `max_date` key takes precedence
   over `max_pages` where dates are meaningful, and is inert where they are not.

## Decisions (user-approved)

- **Add, don't replace.** `max_date` is a new key; `max_pages` and
  `max_cycle_pages` keep working exactly as today in page mode.
- **`max_date: YYYYMM`, integer, `0` = disabled.** `0` (not `null`) is the
  disabled value, matching the project's `0 = unlimited/off` convention used by
  `max_cycle_pages` and `max_queue_days`.
- **`max_date` is a precise month floor.** In date mode the sweep walks months
  newest first, back to and including `max_date`, and inside the boundary year
  stops as soon as a page's games are all older than the floor. This reuses the
  existing `cutoff_date` early-stop in `metacritic.py`, which `pipeline.py`
  currently never passes (always `None`).
- **`max_date` is absolute.** Nothing may scan past it, whatever the pace
  setting says.
- **`max_cycle_months` is the pace in date mode.** A cycle keeps scanning until
  the release dates it has walked span `max_cycle_months`, clamped so it never
  crosses `max_date`. `0` = no month pacing (sweep to the floor, watchdog
  permitting).
- **No page guard in date mode.** A cycle is not capped by a page number; the
  `max_cycle_months` target (or the floor) is the only stop. `max_pages` and
  `max_cycle_pages` are inert in date mode.
- **No new database state.** Progress is derived from the cycle's own pages plus
  the existing per-year `backlog_progress` cursors. Nothing can go stale when
  `max_date` or `max_cycle_months` changes.
- **`cache_pages_hours` is renamed to `cache_hours` in every location** it
  exists, values unchanged, migrated in place.

## Current mechanics (verified)

- `MetacriticPlatformConfig` (`config.py:109-123`) holds `max_pages: int = 500`,
  `max_cycle_pages: int | None = 0`, `cache_pages_hours: int = 6`.
  `SourceConfigEntry` (`config.py:50-60`) holds its own
  `cache_pages_hours: int = 6` for the FitGirl/FreeGOG sitemap cache.
- `pipeline.run_acquisition` derives the year range from the page budget:
  `years_back = ceil(max_pages / 52)` (`pipeline.py:273`), then loops
  `range(current_year, cutoff_year - 1, -1)` newest-first (`pipeline.py:305`).
- A shared budget `total_scanned = db.sum_scanned_pages(...)` (`pipeline.py:281`)
  drives a "budget exhausted -> `db.reset_progress()` -> restart from page 1"
  branch (`pipeline.py:284-292`) and a percentage progress log
  (`pipeline.py:578-587`).
- Per-year resume cursors live in `backlog_progress.last_scanned_page`
  (`database.py:160-176`), read at `pipeline.py:313` and written only for pages
  really fetched (`pipeline.py:341-342`).
- A drained sweep resets progress: `_recent_games_exhausted` probes
  (`pipeline.py:348-370`).
- `MetacriticClient.scan_recent_games(..., cutoff_date=...)` already supports a
  date stop via `_should_stop_scan` / `_page_is_before_cutoff`
  (`metacritic.py:471-484`, `922-948`), but every pipeline call passes
  `cutoff_date=None` (`pipeline.py:326`).
- `scan_state.last_cutoff_date` exists (`database.py:150`) but no pipeline code
  reads or writes it; only its own DB methods and tests reference it.
- `scan_state` already has additive `ALTER TABLE` migration precedent
  (`database.py:265-270`) — not used by this design.
- Config migrations are key-presence driven and run in a fixed order
  (`config.py:912-940`), with `load_config` writing the migrated dict back and
  bumping `config_version` when any migration reports `changed=True`.
- `tests/unit/test_readme_accuracy.py` parses README config tables as exactly
  three columns and asserts README defaults against code defaults.

## Design

### 1. Mode resolution

One predicate decides everything, evaluated per cycle from the resolved
per-platform config:

```text
date_mode = (max_date != 0) and (sort_order == "new")
```

| Mode | Bound | Pace | Inert |
| ------ | ------- | ------ | ------- |
| Date mode (`date_mode` true) | `max_date` | `max_cycle_months` | `max_pages`, `max_cycle_pages` |
| Page mode (`sort_order: new`, `max_date` 0) | `max_pages` (today) | `max_cycle_pages` (today) | none - date keys unset |
| Page mode (score mode, `max_date` 0) | `max_pages` (today) | `max_cycle_pages` (today) | none - date keys unset |
| Page mode (score mode, `max_date` set) | `max_pages` (today) | `max_cycle_pages` (today) | `max_date`, `max_cycle_months` |

An inert-key log is emitted once per cycle at INFO, but only when a key is
**set and cannot apply**: the date keys set in a score mode, or the page keys
set in date mode. Unset keys log nothing, e.g.:

```text
Config: max_date/max_cycle_months ignored because sort_order is 'criticscore'
Config: max_pages/max_cycle_pages ignored because max_date is set (date mode)
```

### 2. Config surface (`src/gamarr/config.py`)

- `MetacriticPlatformConfig` gains:
  - `max_date: int = Field(default=0, ge=0)` — `0` disables date mode,
    otherwise `YYYYMM`.
  - `max_cycle_months: int = Field(default=0, ge=0)` — `0` = no month pacing.
  - `cache_hours: int = 6` (renamed from `cache_pages_hours`).
- `SourceConfigEntry` gains `cache_hours: int = Field(default=6, gt=0, le=168)`
  (renamed from `cache_pages_hours`).
- A `max_date` validator rejects anything that is not `0` or a six-digit
  `YYYYMM` with month `01`-`12`:

  ```text
  max_date must be 0 (disabled) or YYYYMM with month 01-12, e.g. 202401
  ```

  `2024`, `202413`, `2024-01` and negative values all fail.
- A `max_date` later than the current calendar month is clamped to the current
  month with a WARNING. It is not an error: a future floor would otherwise sweep
  nothing and look like a broken install.
- Per-platform overrides inherit both keys automatically because they live on
  `MetacriticPlatformConfig`.
- The default config dict and the generated YAML show `max_date: 0` and
  `max_cycle_months: 0` with short comments, so opting in is a single edit.

### 3. Cache key rename and migration

- New migration `_migrate_cache_pages_hours_to_cache_hours(raw)` rewrites
  `cache_pages_hours` -> `cache_hours` in:
  1. every `review_sites.metacritic.platform_overrides.<platform>`,
  2. every `download_sites[].<source>` entry (FitGirl, FreeGOG, and any future
     source).
  Values are preserved, an INFO line names each migration, and it is registered
  in `_migrations` (ordered after `_migrate_download_sites_to_keyed_list` so the
  keyed-list shape already exists).
- The `config_version` bump is automatic via the existing
  `migrated=True` write-back.
- If both `cache_pages_hours` and `cache_hours` are present, `cache_hours` wins
  and the old key is removed (same rule as `_rename_config_key`).
- Historical migrations that resolve into `max_pages`
  (`_migrate_cutoff_weeks_to_max_pages`,
  `_migrate_days_since_release`, `_migrate_remove_max_games`) keep their current
  target. They describe upgrades from removed keys, not this change.

### 4. Pipeline (`src/gamarr/pipeline.py`)

`AcquisitionConfig` and `run_acquisition` gain `max_date: int = 0` and
`max_cycle_months: int = 0`; `cache_pages_hours` is renamed `cache_hours` and
`fitgirl_cache_pages_hours` is renamed `fitgirl_cache_hours`.

Date mode replaces the page-budget arithmetic for the browse phase:

```text
floor_month  = clamp(max_date)                    # absolute, never crossed
year_range   = current_year down to floor_month // 100
cycle_start  = month of the first page fetched in this cycle
target_month = max(floor_month, cycle_start - max_cycle_months)
               # max_cycle_months == 0 -> target_month = floor_month

for scan_year in year_range (newest first):
    start_page = backlog_progress[scan_year].last_scanned_page + 1
    scan_recent_games(
        ...,
        year = scan_year,
        cutoff_date = "YYYY-MM-01" of target_month,   # early stop inside a year
        max_pages = 0,                                 # no page guard
    )
    advance last_scanned_page for pages really fetched   # unchanged rules
    stop this cycle when a page's games are all older than target_month
```

Rules:

- **`cutoff_date` is finally used.** `pipeline.py` currently passes `None`; date
  mode passes the month-start of `target_month` (which is never older than
  `floor_month`). The stop condition itself already exists and already only
  applies when `sort_order == "new"`.
- **The page budget does not run in date mode.** `total_scanned`,
  `sum_scanned_pages`, the `remaining_pages` bookkeeping and the
  "budget exhausted -> reset to page 1" branch are skipped entirely. The static
  floor replaces them. Page mode keeps all of it, unchanged.
- **Month stopping uses page granularity.** A page whose games span a month
  boundary is not split; the cycle stops on the first page where every game is
  older than `target_month`. This matches the existing `max_date` floor-stop
  semantics and avoids fractional pages.
- **Page mode is untouched,** including `years_back = ceil(max_pages / 52)`.
- **Progress logging:** date mode logs
  `Backlog: reached 2025-11 (floor 2024-01, year 2025 page 12)` instead of
  `Progress: N of 500 pages scanned (x%)`. The page-mode line is unchanged.
- **A cutoff stop needs its own signal.** `_should_stop_scan` returns a bare
  bool, so the pipeline cannot tell "stopped because the cutoff page was
  reached" from "stopped because pages ran out". Without that distinction the
  floor year would re-fetch the same page forever: a cutoff stop collects no
  games and reports zero pages fetched, so neither `_recent_games_exhausted`
  nor `_recent_games_failed` fires and no reset ever happens.
  `MetacriticClient` therefore gains one additive flag,
  `_recent_games_cutoff_reached`, set beside the existing
  `_recent_games_exhausted` / `_recent_games_failed` flags.
- **Drained reset:** the sweep is complete when the floor year finishes, either
  by listing exhaustion or by the cutoff stop (the cutoff only counts when
  `target_month` is clamped to the floor, so a *pacing* cutoff never ends a
  sweep), and no fetch failed. That calls `db.reset_progress()`, so the next
  sweep starts at the newest year and picks up new releases. This is today's
  drained-reset behaviour, retargeted.
- **Unparsable release dates:** if the pages collected so far this cycle yield
  no parsable `release_date`, month pacing cannot be evaluated, so the cycle
  runs to the floor or the end of the listing rather than stopping on absent
  evidence.
- The year loop's per-year `except` still marks `fetch_failed` and leaves the
  cursor untouched; a fetch failure is never read as a drain.

### 5. Helpers (`src/gamarr/utils.py`)

- `subtract_months(year_month: int, months: int) -> int` — `202601 - 2 = 202511`;
  months `<= 0` returns the input unchanged.
- `month_of(date_str: str) -> int | None` — `"2025-11-14"` -> `202511`; `None`
  for unparsable input.
- `year_month_to_date(year_month: int) -> str` — `202511` -> `"2025-11-01"` for
  `cutoff_date`.
- All three are pure and unit-tested; `pipeline.py` gains no inline date maths.

### 6. Scheduler (`src/gamarr/scheduler.py`)

`_build_kwargs` (`scheduler.py:313-355`) maps the new keys
(`max_date`, `max_cycle_months`) and the renamed cache keys (`cache_hours`,
`fitgirl_cache_hours`).

### 7. Docs (`README.md`)

- Config table rows: add `max_date` and `max_cycle_months` with their
  precedence rule stated in one sentence; rename `cache_pages_hours` rows to
  `cache_hours` (two rows: Metacritic and download source).
- Feature bullet "Relative date cutoff" is rewritten: the look-back is a date
  floor, not a page count.
- The narrative browse description (currently `max_pages`-only) gains the date
  mode paragraph and the score-mode caveat.
- Troubleshooting entry "Increase `max_pages` to catch more recently released
  games" gains the `max_date` alternative.
- No fourth table column is added; `test_readme_accuracy.py` keeps parsing three
  columns.

## Error handling

| Condition | Behaviour |
| ----------- | ----------- |
| `max_date` not `0` and not `YYYYMM` | Config error at load with the message above |
| `max_date` later than the current month | WARNING, clamped to the current month |
| `max_date` set, `sort_order` is a score mode | Date keys inert, one INFO line per cycle |
| `max_date` set, `max_pages` also set | `max_pages` inert in date mode, one INFO line per cycle |
| `max_cycle_months` 0 | No month pacing; sweep to the floor |
| `max_cycle_months` larger than the span to the floor | Stops at the floor |
| Cycle hits `acquisition_timeout_mins` | Watchdog aborts; cursors only advanced for pages really fetched, so the next cycle resumes without redoing work. Raising `acquisition_timeout_mins` is the documented remedy |
| Fetch failure in a year | `fetch_failed` set, cursor untouched, not counted as drained |
| Cancellation mid-cycle | Partial results returned, fetched-page cursors kept |
| YAML rewrite fails (read-only config) | WARNING, continue with migrated in-memory config; retried next start |

## Testing

- `tests/unit/test_config.py`
  - `max_date` / `max_cycle_months` defaults are `0`.
  - Accept/reject table: `0`, `202401`, `202512` accepted; `2024`, `202413`,
    `202400`, `2024-01`, `-202401` rejected with the documented message.
  - Future `max_date` clamps to the current month and logs a WARNING.
  - `cache_pages_hours` -> `cache_hours` migration for a metacritic override and
    for both download sources; `cache_hours` wins when both are present;
    values preserved; second load does not rewrite.
  - Per-platform override carries both new keys.
- `tests/unit/test_pipeline.py`
  - Mode matrix: `max_date` set/unset x `new`/`criticscore` selects the expected
    bound and pace, and the inert keys are logged.
  - Date mode passes a real `cutoff_date` (floor-derived, `-01` day) and
    `max_pages=0` to `scan_recent_games`.
  - Month target respects `max_cycle_months` and never crosses `max_date`.
  - `max_cycle_months=0` sweeps to the floor.
  - Page mode is unchanged: `max_pages=500` still yields
    `years_back = ceil(500/52)`, `max_cycle_pages` still caps a cycle.
  - No budget-reset branch runs in date mode.
  - Reaching the floor resets progress so the next sweep starts at the newest
    year.
- `tests/unit/test_scheduler.py` — `_build_kwargs` carries `max_date`,
  `max_cycle_months`, `cache_hours`, `fitgirl_cache_hours`.
- `tests/unit/test_readme_accuracy.py` — renamed rows and new rows match code
  defaults; three-column parser untouched.
- `tests/unit/test_utils.py` — `subtract_months`, `month_of`,
  `year_month_to_date` including year boundaries and `0` inputs.
- `tests/unit/test_metacritic.py` — existing cutoff-stop tests cover the reused
  path; add one asserting a non-`"new"` sort order ignores `cutoff_date`, and
  one asserting `_recent_games_cutoff_reached` is set only on a cutoff stop
  (not on exhaustion or failure).
- `tests/unit/test_database.py` — unchanged (no schema change).

## Files

| File | Change |
| ------ | -------- |
| `src/gamarr/config.py` | add 2 fields to `MetacriticPlatformConfig`; rename 2 cache fields; `max_date` validator; `_migrate_cache_pages_hours_to_cache_hours` + registration; default-config dict |
| `src/gamarr/pipeline.py` | mode resolution; date-mode browse loop with `cutoff_date`; skip budget arithmetic in date mode; progress log; rename cache params |
| `src/gamarr/metacritic.py` | `_recent_games_cutoff_reached` flag plus `max_month_span` pace parameter |
| `src/gamarr/scheduler.py` | `_build_kwargs` key mapping |
| `src/gamarr/utils.py` | three month helpers |
| `README.md` | 2 new rows, 2 renamed rows, date-mode narrative, troubleshooting |
| `tests/unit/test_config.py`, `test_pipeline.py`, `test_scheduler.py`, `test_readme_accuracy.py`, `test_utils.py`, `test_metacritic.py` | see Testing |

`src/gamarr/database.py` is **unchanged**: no schema change, and the per-year
`backlog_progress` cursors remain the only persisted scan state. In
`metacritic.py` the cutoff logic already exists; the additions are the
`_recent_games_cutoff_reached` flag and the `max_month_span` parameter described
under Refinements below.

## Refinements found while planning

1. **The month pace needs a client-side span parameter.** The cycle's month
   target is derived from the month of the first page the cycle fetches, and
   only `MetacriticClient` sees per-page release dates before deciding whether to
   stop. Passing a precomputed `cutoff_date` for the pace is therefore
   impossible on the first call of a cycle (the origin is unknown), and handling
   it in the pipeline would leave the first call of every cycle unbounded within
   its year. `scan_recent_games` therefore gains `max_month_span: int = 0`; the
   client anchors the span on the newest month of the call's first page and stops
   when a page's games are all older than `origin - max_month_span`. The pipeline
   still shares the allowance across years exactly as it shares
   `max_cycle_pages` today: it passes the *remaining* span to each subsequent
   year and stops visiting older years once the allowance is spent, so a cycle
   cannot take a full allowance from every year.
2. **One flag still suffices.** The client folds the pace boundary and the
   absolute floor into a single effective cutoff, so a single
   `_recent_games_cutoff_reached` flag reports "stopped on a date boundary". The
   pipeline distinguishes a floor stop from a pace stop by comparing its own
   months-covered against the span to the floor, as specified.

## Out of scope

- Removing `max_pages` / `max_cycle_pages` (structurally required by score modes).
- Date-based limits for `criticscore` / `userscore` (impossible: their browse
  list has no year dimension).
- Retro-filtering already-pending games against `max_date`; pending expiry stays
  `max_queue_days`.
- Renaming `cache_details_days` (it is days, and stays days).
- Any `scan_state` schema change.
- CLI flags for the new keys (config-only, like `max_pages` today).
