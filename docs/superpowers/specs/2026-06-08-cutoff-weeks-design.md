# cutoff_weeks — Relative date cutoff for Metacritic browsing

**Date:** 2026-06-08
**Status:** Approved design — ready for implementation

## Summary

Replace the static `cutoff_date: str | None` configuration option with a
relative `cutoff_weeks: int | None` that computes the cutoff date as
*today minus N weeks* at runtime.  Users no longer need to manually bump a
hard-coded date as time passes.

## Model changes

### `MetacriticPlatformConfig` & `AcquisitionConfig`

| Before | After |
|---|---|
| `cutoff_date: str \| None = None` | `cutoff_weeks: int \| None = None` |

Semantics of `cutoff_weeks`:

| Value | Behavior |
|---|---|
| `None` / not set | No date filter — browse indefinitely |
| `0` | No date filter (same as None) |
| `52` | Cutoff = today − 364 days |
| `1` | Cutoff = today − 7 days |

- Must be a non-negative integer if set.
- No upper bound enforced.

## Config migration (`_migrate_config`)

- Remove the two old-key rename entries that targeted `cutoff_date`:
  - `"browse_cutoff_date" → "cutoff_date"`
  - `"metacritic_cutoff_date" → "cutoff_date"`
- Add a new step: if `cutoff_date` is found (from any source), log a
  deprecation warning and delete it.  No auto-conversion to weeks.

## Pipeline computation

In `_run_discovery_phases()` (inside `pipeline.py`), compute the absolute
ISO date string from `cfg.cutoff_weeks` just before calling
`mc.scan_recent_games()`:

```
if cfg.cutoff_weeks is not None and cfg.cutoff_weeks > 0:
    cutoff_date = (now_utc_date - timedelta(weeks=cfg.cutoff_weeks)).isoformat()
else:
    cutoff_date = None
```

The Metacritic client API (`scan_recent_games`, `_page_is_before_cutoff`,
`_is_before_date`) is unchanged — it still receives and operates on
ISO date strings.  This keeps the diff minimal.

## Parameter wiring

- `scheduler.py`: rename the kwarg key from `"cutoff_date"` to
  `"cutoff_weeks"` that it passes to `run_acquisition()`.
- `run_acquisition()`: rename the function parameter from
  `cutoff_date: str | None = None` to `cutoff_weeks: int | None = None`;
  also update the `AcquisitionConfig(...)` constructor call inside it.

## Config file

`configs/gamarr.yml` — replace the old line:

```yaml
cutoff_date: 2026-05-01
```

with:

```yaml
cutoff_weeks: 0    # 0 = no cutoff, positive N = look back N weeks
```

## Files changed

| File | Change |
|---|---|
| `src/gamarr/config.py` | Rename field in `MetacriticPlatformConfig`; update `_migrate_config` |
| `src/gamarr/pipeline.py` | Rename in `AcquisitionConfig`; compute date in `_run_discovery_phases`; rename param in `run_acquisition` |
| `src/gamarr/scheduler.py` | Rename kwarg key |
| `configs/gamarr.yml` | Replace `cutoff_date` with `cutoff_weeks: 0` |
| `tests/unit/test_config.py` | Update migration tests: `browse_cutoff_date` and `metacritic_cutoff_date` should be dropped (not migrated to `cutoff_date`) |
| `tests/unit/test_metacritic.py` | No changes needed (MC client API unchanged) |
| `tests/unit/test_pipeline.py` | No changes needed (run_acquisition parameter is renamed but no test directly references it by that name) |

## Out of scope

- No changes to `MetacriticClient.scan_recent_games()` or any date-parsing
  internals in `metacritic.py`.
- No upper-bound validation on `cutoff_weeks`.
- No auto-conversion of old `cutoff_date` values to weeks during migration.
