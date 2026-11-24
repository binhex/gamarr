# `max_weeks → max_pages` Config Rename

**Date:** 2026-07-07
**Status:** Approved

## Motivation

Following the pattern established by `max_cycle_weeks → max_cycle_pages`, rename
`max_weeks` to `max_pages` to use page-count semantics instead of time-based
weeks. A page count is independent of sort order, works equally well for `"new"`
and `"metascore"` browsing, and is simpler to reason about.

The default is **500 pages** (~12,000 games across ~21 months of Metacritic PC
releases at ~24 games/page). Users can set lower values for faster cycles or
higher values for deeper discovery.

## Design

### Config changes (`src/gamarr/config.py`)

| Current | New |
|---|---|
| `max_weeks: int \| None = Field(default=13, ge=0)` | `max_pages: int = Field(default=500, ge=1)` |

The minimum is `1` (at least one page per cycle). `None` is no longer supported
since a page count of `0` would be meaningless.

**Migration chain** — three additions:

1. `_migrate_days_since_release` is updated to write to `max_pages` instead of
   `max_weeks`:
   ```python
   mc_pc["max_pages"] = max(1, round(days / 7))
   ```

2. `_migrate_cutoff_weeks_to_max_weeks` is updated to write to `max_pages`
   instead of `max_weeks`:
   ```python
   mc_pc["max_pages"] = mc_pc.pop("cutoff_weeks")
   ```

3. A new rename rule is added to the migration chain, running **after** the
   two conversions above:
   ```python
   changed |= _rename_config_key(mc_pc, "max_weeks", "max_pages", platform_key)
   ```

   This ensures that any `max_weeks` value (whether original from the user's YAML
   or produced by `_migrate_days_since_release` / `_migrate_cutoff_weeks_to_max_weeks`)
   is renamed to `max_pages`.

Migration comment strings ("use max_weeks") are updated to reference `max_pages`.

### Pipeline changes (`src/gamarr/pipeline.py`)

**AcquisitionConfig** — field renamed from `max_weeks` to `max_pages`:

```python
max_pages: int | None = None
```

**AcquisitionConfig._age_days()** — removed entirely. The age filter served as
per-game date-based gating; with page-count semantics, the filter is not
meaningful and all games within the scanned pages are collected.

**run_acquisition()** — parameter renamed from `max_weeks` to `max_pages`.
The `days_since_release` parameter is removed (was `_age_days()` output).
The `_is_older_than()` call in `_process_browse_games` is removed — every game
found in the browse pages is collected regardless of release date.

**_run_discovery_phases()** — the `hard_cutoff` date computation
(`now - max_weeks`) is removed. The `cutoff_date` passed to `scan_recent_games`
is always `None` (no date-based early stop needed — `_should_stop_scan` already
handles the `max_pages` limit). The entire `# Apply hard cutoff` block is
removed.

**process_browse_games()** — the `days_since_release` parameter is removed.
The `_is_older_than()` call that filtered games by age is removed.

### Scheduler changes (`src/gamarr/scheduler.py`)

```python
"max_pages": mc_cfg.max_pages,
```

Replaces the old `"max_weeks": mc_cfg.max_weeks` entry.

### Database cleanup (`src/gamarr/database.py`)

Remove `get_last_max_weeks()` and `set_last_max_weeks()` methods — already dead
code, never called since the backlog retreat system was simplified.

### Config behavior summary

| Scenario | Result |
|---|---|
| Fresh install (no config) | Pydantic generates default: `max_pages: 500` |
| Old config with `max_weeks: 13` | Migration renames → `max_pages: 13` |
| Old config with `max_weeks: 104` | Migration renames → `max_pages: 104` |
| Old config with `cutoff_weeks: 52` | `_migrate_cutoff_weeks_to_max_weeks` sets `max_weeks: 52`, then rename → `max_pages: 52` |
| Old config with `days_since_release: 365` | `_migrate_days_since_release` sets `max_weeks: 52`, then rename → `max_pages: 52` |
| New config with `max_pages: 200` | Used directly, no migration touches it |

### Test changes

All ~58 test references to `max_weeks` are renamed to `max_pages`. Tests
covering removed features (age filtering, hard_cutoff computation,
`_age_days()`) are removed or replaced with simpler equivalents.

## Scope boundary

- **Replace**: `max_weeks` → `max_pages` in config, pipeline, scheduler, database, README
- **Remove**: age filter (`_age_days`, `_is_older_than` call, `days_since_release`)
- **Out of scope**: No new features, no behavioral enhancements beyond the rename
