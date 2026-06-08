# reject_genre — Genre-based filtering for Metacritic browsing

**Date:** 2026-06-08
**Status:** Approved design

## Problem

Users want to exclude games of certain genres (e.g. "RPG", "Sports") from
being downloaded, regardless of the game's Metacritic scores. Currently
there is no genre-based filtering — the only rejection mechanism is
`exclude_keywords`, which operates on game titles.

## Solution

Add a `reject_genre` field to the Metacritic platform config. It accepts
a list of genre strings. If a game's Metacritic detail-page genres match
any entry (case-insensitive exact match), the game is immediately removed
from the pending queue and recorded in history as filtered — no score
verification is performed and no future re-verification occurs (genres
never change).

Genres are only available after a Metacritic detail-page lookup (the
browse-page Nuxt API does not expose them), so the one HTTP lookup per
game is unavoidable. However, once genres are known, a matching game
is removed immediately without score processing or future re-checks.

## Design

### 1. Model changes

#### `src/gamarr/config.py` — `MetacriticPlatformConfig`

```python
reject_genre: list[str] = Field(default_factory=list)
```

Default is an empty list — no genre filtering.

#### `src/gamarr/pipeline.py` — `AcquisitionConfig`

```python
reject_genre: list[str] | None = None
```

#### `src/gamarr/pipeline.py` — `run_acquisition()` signature

```python
reject_genre: list[str] | None = None,
```

Construction in `run_acquisition()`:

```python
reject_genre=reject_genre,
```

#### `configs/gamarr.yml`

```yaml
metacritic:
  platform_overrides:
    pc:
      reject_genre: []    # case-insensitive exact match; e.g. ["Action", "RPG"]
```

### 2. Scheduler wiring

#### `src/gamarr/scheduler.py` — `_build_kwargs()`

```python
"reject_genre": mc_cfg.reject_genre,
```

### 3. Filtering logic

#### `src/gamarr/pipeline.py` — `_process_verify_result()`

Add a `reject_genre` parameter and insert the genre check as the first
action inside the function, before the `result is None` check:

```python
def _process_verify_result(
    db: Database,
    game: Any,
    result: Any,
    thresholds: dict[str, Any],
    *,
    max_verify_attempts: int = 6,
    reject_genre: list[str] | None = None,
) -> bool:
```

Genre check pseudocode:

```python
# Check if the game's genre matches a rejected genre (case-insensitive)
if result is not None and reject_genre and result.genres:
    reject_lower = [g.lower() for g in reject_genre]
    for genre in result.genres:
        if genre.lower() in reject_lower:
            logger.info(
                "Removing '{}' — genre '{}' is in reject_genre list",
                game.game_title,
                genre,
            )
            _fail_game_after_max_attempts(db, game, result, attempts=1)
            return True
```

Key behaviour:
- If `result is None` (lookup failed), the genre check is skipped
  (no genres to check against)
- Both lists are lowered for case-insensitive comparison
- Uses `_fail_game_after_max_attempts` (existing helper) to record in
  history as "Failed" and remove from pending
- Returns `True` to count toward the `removed` counter in the caller

### 4. Logging

A single `logger.info` line records which genre caused the rejection.
This follows the same verbosity pattern as other filter reasons in the
pipeline.

### 5. Tests

#### Unit tests for `_process_verify_result`

| Test | Coverage |
|------|----------|
| `test_reject_genre_matches` | Game genre "Action", reject_genre=["action"] → removed (True) |
| `test_reject_genre_no_match` | Game genre "RPG", reject_genre=["action"] → scores checked normally (False) |
| `test_reject_genre_empty` | reject_genre=[] → normal flow preserved |
| `test_reject_genre_multi_match` | Game genres ["Action","RPG"], reject_genre=["rpg"] → removed |
| `test_reject_genre_case_insensitive` | Genre "Action", reject_genre=["ACTION"] → removed |
| `test_reject_genre_result_none` | result=None → genre check skipped → normal retry |

#### Integration test

| Test | Coverage |
|------|----------|
| `test_reject_genre_integration` | Full pipeline flow via `_run_discovery_phases` with reject_genre set |

All existing tests must continue passing unchanged.

## Files changed

| File | Change |
|------|--------|
| `src/gamarr/config.py` | Add `reject_genre` field to `MetacriticPlatformConfig` |
| `src/gamarr/pipeline.py` | Add field to `AcquisitionConfig` + `run_acquisition()` param + filter in `_process_verify_result` |
| `src/gamarr/scheduler.py` | Add kwarg in `_build_kwargs()` |
| `configs/gamarr.yml` | Add `reject_genre: []` under `metacritic.platform_overrides.pc` |
| `tests/unit/test_pipeline.py` | Add unit + integration tests for genre rejection |

## Non-goals

- No database schema changes (genres are not persisted to the pending table)
- No browse-page genre fetching (genres are only available on detail pages)
- No partial/substring matching (exact match only, case-insensitive)
- No genre allowlist feature (this is rejection-only)
- No UI or CLI for managing the reject list
