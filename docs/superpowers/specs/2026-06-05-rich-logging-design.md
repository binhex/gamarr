# gamarr — Rich Metacritic Logging Design

**Date:** 2026-06-05
**Status:** Draft
**Version:** 1.0.0

## 1. Overview

gamarr's current pipeline output is minimal — it logs "Processing entry: Title" and
"✓ Sent Title to qBittorrent" without showing any Metacritic scores, genres, or
review counts. This makes it hard to see at a glance what quality of games are being
processed.

This feature adds **gamecritic-style rich logging** to the acquisition pipeline:
genre, critic score + count, user score + count, must-play status, and release date
are displayed for every game that is successfully looked up on Metacritic.

The output is colorized using Loguru's ``opt(colors=True)``, matching the format
used in the gamecritic project at ``/data/gamecritic/``.

## 2. Architecture

### 2.1 Data Flow

```
Metacritic Nuxt JSON page data
    │
    └─── _find_game_details_in_nuxt_data()  [renamed from _find_scores_in_nuxt_data]
         │
         ├── Critic score block     → metascore, metascore_review_count
         ├── User score block       → user_score, user_review_count
         └── Game metadata block    → genres, must_play, release_date, description
              (NEW — added to existing Nuxt parsing)
    │
    ▼
ScoreResult (extended with new fields)
    │
    ▼
_process_entry() — rich log line after lookup
    │
    ├── Before score evaluation → always shown (even for failed games)
    ├── Colorized output via logger.opt(colors=True)
    └── Format: Title | Metascore X (N) | User Y (M) | Genre: ... | Must Play: Z | Released: D
```

### 2.2 Changes to Existing Code

| File | Change |
|---|---|
| `metacritic.py` | Rename `_find_scores_in_nuxt_data` to `_find_game_details_in_nuxt_data` and add extraction of genres, mustPlay, releaseDate, description |
| `metacritic.py` | Add fields to `ScoreResult`: genres, must_play, release_date, description |
| `metacritic.py` | Update `_parse_game_details` and `_find_nuxt_scores_in_page` call sites |
| `pipeline.py` | Add rich log line in `_process_entry()` after lookup |
| `tests/` | Update all test references to renamed function |

## 3. ScoreResult Extension

```python
@dataclass
class ScoreResult:
    """Result of a Metacritic score lookup for a single game."""

    title: str
    slug: str
    metascore: float | None
    metascore_review_count: int | None
    user_score: float | None
    user_review_count: int | None
    passed: bool
    genres: list[str] | None = None
    must_play: bool | None = None
    release_date: str | None = None
    description: str | None = None
```

All new fields default to ``None`` for backward compatibility. Existing code that
constructs ``ScoreResult`` without these fields continues to work unchanged.

## 4. Nuxt Data Extraction

### 4.1 Rename and Extend

The existing `_find_scores_in_nuxt_data` function is renamed to
`_find_game_details_in_nuxt_data`. A new extraction path for game metadata is added:

```python
def _find_game_details_in_nuxt_data(page_data: list[Any]) -> dict[str, Any] | None:
    """Extract critic scores, user scores, and game details from Nuxt JSON."""
    metascore = metascore_reviews = user_score = user_reviews = None
    genres = must_play = release_date = description = None

    for item in page_data:
        if not isinstance(item, dict):
            continue

        # Existing: critic score extraction
        ms, msv = _extract_critic_score(page_data, item, metascore)
        if ms != metascore:
            metascore, metascore_reviews = ms, msv

        # Existing: user score extraction
        us, usv = _extract_user_score(page_data, item, user_score)
        if us != user_score:
            user_score, user_reviews = us, usv

        # NEW: game metadata block (has mustPlay, genres, etc.)
        if genres is None and "mustPlay" in item and "genres" in item:
            must_play = _nuxt_val(page_data, item.get("mustPlay"))
            genres_list = _nuxt_val(page_data, item.get("genres"))
            if isinstance(genres_list, list):
                genres = []
                for g in genres_list:
                    gd = _nuxt_val(page_data, g)
                    if isinstance(gd, dict):
                        name = _nuxt_val(page_data, gd.get("name"))
                        if name:
                            genres.append(str(name))
            release_date = _nuxt_val(page_data, item.get("releaseDate"))
            description = _nuxt_val(page_data, item.get("description"))

    if metascore is not None or user_score is not None:
        return {
            "metascore": metascore,
            "metascore_reviews": metascore_reviews,
            "user_score": user_score,
            "user_reviews": user_reviews,
            "genres": genres,
            "must_play": must_play,
            "release_date": str(release_date) if release_date else None,
            "description": str(description)[:200] if description else None,
        }
    return None
```

### 4.2 Game Metadata Extraction Strategy

The game metadata block in the Nuxt data is identified by the presence of both
``"mustPlay"`` and ``"genres"`` keys in a single dict item (index ~1647 in the
current page data array). The function already iterates all items looking for
dicts, so adding this check is a natural extension.

## 5. Pipeline Logging

### 5.1 Rich Log Line

In `_process_entry()`, after `mc_result = mc.lookup_game(...)` and before score
evaluation, a single rich log line is emitted:

```python
def _escape_markup(v: object) -> str:
    """Escape Loguru markup angle brackets in user-provided values."""
    return str(v).replace("<", "\\<").replace(">", "\\>")


def _log_game_details(mc_result: ScoreResult) -> None:
    """Log a colorized summary line for a looked-up game."""
    if mc_result is None:
        return
    title = _escape_markup(mc_result.title)
    ms = _escape_markup(mc_result.metascore) if mc_result.metascore is not None else "TBD"
    ms_r = _escape_markup(mc_result.metascore_review_count) if mc_result.metascore_review_count is not None else "?"
    us = _escape_markup(mc_result.user_score) if mc_result.user_score is not None else "TBD"
    us_r = _escape_markup(mc_result.user_review_count) if mc_result.user_review_count is not None else "?"
    genre = ", ".join(mc_result.genres) if mc_result.genres else "N/A"
    must_play = (
        "<green><bold>Yes</bold></green>"
        if mc_result.must_play
        else "<dim>No</dim>"
    )
    release = _escape_markup(mc_result.release_date) if mc_result.release_date else "N/A"
    sep = " <dim>|</dim> "

    logger.opt(colors=True).info(
        f"<cyan><bold>{title}</bold></cyan>"
        f"{sep}<green>Metascore: <bold>{ms}</bold></green> <dim>({ms_r} reviews)</dim>"
        f"{sep}<yellow>User: <bold>{us}</bold></yellow> <dim>({us_r} reviews)</dim>"
        f"{sep}<magenta>Genre: {genre}</magenta>"
        f"{sep}Must Play: {must_play}"
        f"{sep}Released: <dim>{release}</dim>"
    )
```

### 5.2 Call Site

The log is called in `_process_entry()` after `mc_result = mc.lookup_game(...)`:

```python
def _process_entry(...) -> dict[str, Any]:
    logger.info("Processing entry: '{}'", entry.title)

    mc_result = mc.lookup_game(...)

    if mc_result is None:
        return _handle_game_not_found(db, entry)

    _log_game_details(mc_result)  # ← NEW: rich log line

    game_title = mc_result.title
    ...
```

This means the log appears even for games that fail score checks, so you always
see the scores and genres.

### 5.3 Example Output

```
Realm of Ink | Metascore: 96 (93 reviews) | User: 8.4 (200 reviews) | Genre: Action RPG | Must Play: No | Released: 2025-03-15
CarX Street | Metascore: 72 (45 reviews) | User: 7.2 (150 reviews) | Genre: Racing | Must Play: No | Released: 2024-11-01
Elden Ring | Metascore: 96 (120 reviews) | User: 8.5 (5000 reviews) | Genre: Action RPG | Must Play: Yes | Released: 2022-02-25
```

## 6. Test Strategy

### 6.1 Unit Tests

| Test | Scenario |
|---|---|
| `test_game_details_extracts_genres` | Nuxt data with genres → genres list |
| `test_game_details_extracts_must_play` | Nuxt data with mustPlay → bool |
| `test_game_details_extracts_release_date` | Nuxt data with releaseDate → string |
| `test_game_details_backward_compat` | Nuxt data with only scores → scores extracted, genres=None |
| `test_log_game_details_escapes_markup` | Title with `<`/`>` → properly escaped |

### 6.2 Existing Tests

All existing tests that reference `_find_scores_in_nuxt_data` must be updated to
use the new `_find_game_details_in_nuxt_data` name.

## 7. Files Changed

```
src/gamarr/metacritic.py      — extend ScoreResult, rename+extend Nuxt parser
src/gamarr/pipeline.py        — add _log_game_details, call in _process_entry
tests/unit/test_metacritic.py — update references to renamed function, add new tests
tests/unit/test_pipeline.py   — update any ScoreResult constructions
```

## 8. YAGNI Decisions

| Not included | Rationale |
|---|---|
| Description in the main log line | Nuxt data has it, but it's too long for a single log line. Stored in cache if needed later |
| Platform per game | The platform is already known from the source config (e.g. "pc") |
| Game taxonomy (tags) | Could be extracted but adds noise. Genres are sufficient for V1 |
| Output to file (gamecritic's --write-output) | Can be added later if needed; V1 is live log only |
