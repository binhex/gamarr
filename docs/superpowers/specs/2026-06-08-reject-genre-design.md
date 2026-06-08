# reject_genre — Genre-based filtering for Metacritic browsing

**Date:** 2026-06-08
**Status:** Approved design (amended 2026-06-08: exact match → case-insensitive substring match)

## Problem

Users want to exclude games of certain genres (e.g. "RPG", "Sports") from
being downloaded, regardless of the game's Metacritic scores. Currently
there is no genre-based filtering — the only rejection mechanism is
`exclude_keywords`, which operates on game titles.

## Solution

Add a `reject_genre` field to the Metacritic platform config. It accepts
a list of genre substrings. If a game's Metacritic detail-page genres
contain any entry (case-insensitive substring match), the game is
immediately removed from the pending queue and recorded in history as
filtered — no score verification is performed and no future re-verification
occurs (genres never change).

**Substring matching rules:**
- `reject_genre: ["RPG"]` matches any genre containing `"RPG"`
  (e.g. `"Action RPG"`, `"Western RPG"`, `"JRPG"`, `"RPG"`)
- `reject_genre: ["Western RPG"]` only matches genres containing
  `"Western RPG"` (does NOT match `"Action RPG"` or `"JRPG"`)
- Matching is case-insensitive: `"rpg"` matches `"RPG"`, `"Action RPG"`

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
      reject_genre: []    # case-insensitive substring; e.g. ["RPG"] matches "Action RPG", "JRPG"
```

### 2. Scheduler wiring

#### `src/gamarr/scheduler.py` — `_build_kwargs()`

```python
"reject_genre": mc_cfg.reject_genre,
```

### 3. Filtering logic

#### `src/gamarr/pipeline.py` — `_reject_by_genre()`

Uses case-insensitive substring matching: each entry in the reject list is
checked against every game genre. If any entry is found as a substring of
the genre (after lowering both sides), the game is rejected.

```python
genre_lower = [g.lower() for g in result.genres]
for term in reject_genre:
    term_lower = term.lower()
    for i, genre in enumerate(genre_lower):
        if term_lower in genre:
            # match found
```

#### `src/gamarr/pipeline.py` — `_process_verify_result()`

Calls `_reject_by_genre()` as the first check. If a genre matches, the
game is removed via `_fail_game_after_max_attempts` with a custom
`result_details` containing the matching term and genre name.

Key behaviour:
- If `result is None` (lookup failed), the genre check is skipped
- Substring match: `"rpg" in "action rpg"` matches, `"western rpg" in "action rpg"` does not
- `_fail_game_after_max_attempts` records with accurate reason in history
- Returns `True` when removed

### 4. Logging

A single `logger.info` line records which genre term matched which game
genre. This follows the same verbosity pattern as other filter reasons.

### 5. Tests

#### Unit tests for `_reject_by_genre` (via `_verify_pending_scores`)

| Test | Coverage |
|------|----------|
| `test_reject_genre_matches` | Genre "Action", reject_genre=["action"] → removed |
| `test_reject_genre_no_match` | Genre "Racing", reject_genre=["action"] → not removed |
| `test_reject_genre_empty_list` | reject_genre=[] → not removed |
| `test_reject_genre_multi_match` | Genres ["Action","RPG","Open-World"], reject_genre=["rpg","sports"] → removed |
| `test_reject_genre_case_insensitive` | Genre "Roguelike", reject_genre=["ROGUELIKE"] → removed |
| `test_reject_genre_result_none` | result=None → genre check skipped |
| `test_reject_genre_none_genres` | genres=None → genre check skipped |
| `test_reject_genre_none_default` | reject_genre not passed → not removed |
| `test_reject_genre_substring_broad` | Genre "Action RPG", reject_genre=["RPG"] → removed |
| `test_reject_genre_substring_narrow` | Genre "Action RPG", reject_genre=["Western RPG"] → not removed |

All existing tests must continue passing unchanged.

## Files changed

| File | Change |
|------|--------|
| `src/gamarr/config.py` | Add `reject_genre` field to `MetacriticPlatformConfig` |
| `src/gamarr/pipeline.py` | Add `_reject_by_genre()` with substring matching, `_scores_fail_check()`, threading through `_process_verify_result` and `_verify_pending_scores` |
| `src/gamarr/scheduler.py` | Add kwarg in `_build_kwargs()` |
| `configs/gamarr.yml` | Add `reject_genre: []` under `metacritic.platform_overrides.pc` |
| `tests/unit/test_pipeline.py` | Add 10 unit tests for genre rejection |
| `tests/unit/test_database.py` | Add 2 migration tests for `_migrate()` |
| `docs/superpowers/specs/2026-06-08-reject-genre-design.md` | This document |

## Non-goals

- No database schema changes (genres are not persisted to the pending table)
- No browse-page genre fetching (genres are only available on detail pages)
- No allowlist or inclusion-only genre filtering
- No UI or CLI for managing the reject list
- No minimum substring length restriction
