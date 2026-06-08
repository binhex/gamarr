# Remove `max_score_checks` — use `max_games` instead

**Date:** 2026-06-08
**Status:** Approved design — ready for implementation

## Summary

Remove `max_score_checks` as a configurable field.  The number of pending
games to verify per cycle is set to the same value as `max_games` (already
defined in the config).  Users have one knob instead of two that needed to
be kept in sync.

## Model changes

### `MetacriticPlatformConfig` (config.py)

```python
# Before
max_games: int = Field(default=1000, ge=0, le=20000)
max_score_checks: int = Field(default=200, ge=0, le=10000)

# After
max_games: int = Field(default=1000, ge=0, le=20000)
```

### `AcquisitionConfig` (pipeline.py)

```python
# Before
max_games: int = 1000
max_score_checks: int = 200

# After
max_games: int = 1000
```

### `run_acquisition()` signature (pipeline.py)

```python
# Before
max_games: int = 1000,
max_score_checks: int = 200,

# After
max_games: int = 1000,
```

Remove the `max_score_checks=` kwarg from the `AcquisitionConfig(...)`
constructor call inside `run_acquisition()`.

## Pipeline usage

At the verify-limit site in `_verify_pending_scores()`:

```python
# Before
max_verify = (
    0 if cfg.max_score_checks == 0
    else min(len(pending_games), cfg.max_score_checks)
),

# After
max_verify = (
    0 if cfg.max_games == 0
    else min(len(pending_games), cfg.max_games)
),
```

Same semantics — `0 = unlimited` — just using `cfg.max_games` instead of
its own field.  This is the only code path that consumed
`max_score_checks`.

## Migration

In `_migrate_config()` (`config.py`), consolidate the deprecation loop for
keys that should be dropped without conversion:

```python
            for old_key in ("browse_max_pages", "max_score_checks"):
                if old_key in mc_pc:
                    logger.warning(
                        "Config: '{}' is deprecated for platform '{}'; "
                        "use 'max_games' instead. Ignoring value.",
                        old_key,
                        platform_key,
                    )
                    mc_pc.pop(old_key)
```

The existing `_rename_config_key(mc_pc, "browse_max_pages", None, ...)`
call is replaced by the loop above — same drop-with-warning semantics.

## Scheduler wiring

Remove `"max_score_checks": mc_cfg.max_score_checks` from the kwargs dict
in `scheduler.py:_build_kwargs()`.

## Config file

Remove the `max_score_checks: 0` line from `configs/gamarr.yml`.

## Files changed

| File | Change |
|---|---|
| `src/gamarr/config.py` | Remove field from `MetacriticPlatformConfig`; update `_migrate_config` |
| `src/gamarr/pipeline.py` | Remove from `AcquisitionConfig`, `run_acquisition()` signature and constructor; replace `cfg.max_score_checks` → `cfg.max_games` at verify site; update docstring |
| `src/gamarr/scheduler.py` | Remove kwarg key |
| `configs/gamarr.yml` | Remove line |
| `tests/unit/test_config.py` | Remove `max_score_checks` default assertion; update migration tests |
| `tests/unit/test_pipeline.py` | Update `max_score_checks` tests to use `max_games`; update docstrings |

## Out of scope

- No changes to `metacritic.py` or `scan_recent_games()` — `max_games` is
  already a parameter there and stays unchanged.
- No changes to the `0 = unlimited` semantics.
