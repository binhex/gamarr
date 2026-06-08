# reject_title — Filter games by title substring matching

**Date:** 2026-06-08
**Status:** Approved design

## Problem

Currently `reject_genre` filters games by genre after the Metacritic detail page is
fetched, but there is no equivalent mechanism to filter by game title using the same
substring-matching pattern. The existing `exclude_keywords` only works at the
browse-page discovery stage and is less discoverable — named differently and not
co-located with `reject_genre`.

Users who want to ignore games with certain patterns in their titles (e.g. "Remake",
"Collection", "VR", "Demo") need a single, consistent mechanism that works at both
the initial discovery stage and the score verification stage, just like `reject_genre`
works for genres.

## Solution

Add a `reject_title` field to `MetacriticPlatformConfig` — a list of case-insensitive
substrings that, when matched against a game's title, cause the game to be rejected.
The matching logic mirrors `reject_genre` exactly.

## Design

### 1. Config model

#### `src/gamarr/config.py` — `MetacriticPlatformConfig`

```python
class MetacriticPlatformConfig(BaseModel):
    ...
    reject_genre: list[str] = Field(default_factory=list)
    reject_title: list[str] = Field(default_factory=list)  # ← new
```

#### `configs/gamarr.yml`

```yaml
metacritic:
  platform_overrides:
    pc:
      reject_genre:
        - novel
        - card
      reject_title: []
```

The default is an empty list, so no filtering occurs unless the user explicitly adds
entries.

### 2. Pipeline — `_reject_by_title()` helper

A new helper function following the same signature and logic pattern as
`_reject_by_genre()`:

```python
def _reject_by_title(
    game: Any,
    reject_title: list[str] | None,
) -> str | None:
    """Return the first title substring that matched *reject_title*, or *None*.

    Case-insensitive substring match means ``reject_title=["Remake"]`` matches
    ``"Resident Evil 4 Remake"``, ``"Remake Collection"``, etc.
    """
    if not (reject_title and game and game.game_title):
        return None
    title_lower = str(game.game_title).lower()
    for term in reject_title:
        term_lower = term.lower()
        if term_lower in title_lower:
            logger.info(
                "Removing '{}' — title matches reject_title '{}'",
                game.game_title,
                term,
            )
            return str(term)
    return None
```

### 3. Checkpoint 1 — Browse-page discovery

In `_is_game_eligible()`, add a `reject_title` check alongside the existing
`exclude_keywords` check. If the browse-page title matches, the game is skipped
before entering the pending queue.

```python
def _is_game_eligible(
    game: dict[str, Any],
    db: Database,
    thresholds: dict[str, Any],
    days_since_release: int,
    exclude_keywords: list[str] | None,
    reject_title: list[str] | None = None,  # ← new
) -> bool:
    ...
    if _title_contains_keywords(title, exclude_keywords):
        logger.debug("Skipping '{}' — matches exclude keyword", title)
        return False
    if _title_matches_reject(title, reject_title):  # ← new
        logger.debug("Skipping '{}' — matches reject_title", title)
        return False
    ...
```

Where `_title_matches_reject()` is a simple helper that checks the browse-page title
string against `reject_title`:

```python
def _title_matches_reject(title: str, reject_title: list[str] | None) -> bool:
    """Return True if *title* case-insensitively matches any reject_title entry."""
    if not reject_title:
        return False
    title_lower = title.lower()
    return any(term.lower() in title_lower for term in reject_title)
```

### 4. Checkpoint 2 — Score verification

In `_process_verify_result()`, call `_reject_by_title()` alongside the existing
`_reject_by_genre()` call. Uses the pending game's title (`game.game_title`).

```python
matched_title = _reject_by_title(game, reject_title)
if matched_title is not None:
    attempts = db.increment_verify_attempts(str(game.slug))
    _fail_game_after_max_attempts(
        db,
        game,
        result,
        attempts=attempts,
        result_details=f"Game '{game.game_title}' — title matches reject_title '{matched_title}'",
    )
    return True
```

### 5. Threading through pipeline

#### `AcquisitionConfig` (pipeline.py)

```python
@dataclass
class AcquisitionConfig:
    ...
    reject_genre: list[str] | None = None
    reject_title: list[str] | None = None  # ← new
```

#### `run_acquisition()` signature

```python
def run_acquisition(
    ...
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,  # ← new
    ...
```

#### `_verify_pending_scores()` signature

```python
def _verify_pending_scores(
    ...
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,  # ← new
    ...
```

### 6. Scheduler wiring

#### `src/gamarr/scheduler.py` — `_build_kwargs()`

```python
"reject_genre": mc_cfg.reject_genre,
"reject_title": mc_cfg.reject_title,  # ← new
```

### 7. Testing

| Test | Stage | Coverage |
|------|-------|----------|
| `test_reject_title_at_browse` | Browse discovery | Game with matching title substring is skipped; not added to pending |
| `test_reject_title_at_verify` | Score verification | Game already pending with matching title is removed during verification |
| `test_reject_title_no_match` | Both | Game with non-matching title proceeds normally |
| `test_reject_title_empty_list` | Both | Empty reject_title list has no effect |
| `test_reject_title_case_insensitive` | Both | Matching is case-insensitive |
| `test_reject_title_substring` | Both | Partial substring match works (e.g. "Remake" matches "4 Remake") |

## Non-goals

- No change to the existing `exclude_keywords` field (it remains in place for
  FitGirl-specific title filtering and continue to work at browse discovery)
- No changes to how games are matched against FitGirl (the title matching in
  `_match_pending_games` is unaffected)
- No retroactive rejection of already-processed games
