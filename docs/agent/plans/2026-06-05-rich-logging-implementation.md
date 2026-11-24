# Rich Metacritic Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add gamecritic-style rich logging to the acquisition pipeline — genre, critic score, user score, review counts, must-play status, and release date, colorized with Loguru's ``opt(colors=True)``.

**Architecture:** Extend ``ScoreResult`` with new fields (genres, must_play, release_date, description). Rename ``_find_scores_in_nuxt_data`` to ``_find_game_details_in_nuxt_data`` and add extraction of game metadata from the Nuxt JSON. Add a colorized log line in ``_process_entry()`` after the Metacritic lookup.

**Tech Stack:** Python 3.12+, Loguru ``opt(colors=True)``, Nuxt JSON parsing.

---

### Task 1: Extend ScoreResult and rename Nuxt parser

**Files:**
- Modify: `src/gamarr/metacritic.py`
- Modify: `tests/unit/test_metacritic.py`

- [ ] **Step 1: Add new fields to ScoreResult**

In `src/gamarr/metacritic.py`, extend the `ScoreResult` dataclass:

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

- [ ] **Step 2: Rename _find_scores_in_nuxt_data to _find_game_details_in_nuxt_data and extend it**

Rename the function and add game metadata extraction:

```python
def _find_game_details_in_nuxt_data(page_data: list[Any]) -> dict[str, Any] | None:
    """Extract critic scores, user scores, and game details from Nuxt JSON."""
    metascore = metascore_reviews = user_score = user_reviews = None
    genres = must_play = release_date = description = None

    for item in page_data:
        if not isinstance(item, dict):
            continue

        ms, msv = _extract_critic_score(page_data, item, metascore)
        if ms != metascore:
            metascore, metascore_reviews = ms, msv

        us, usv = _extract_user_score(page_data, item, user_score)
        if us != user_score:
            user_score, user_reviews = us, usv

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

- [ ] **Step 3: Update all call sites and references**

In `src/gamarr/metacritic.py`:
- Line 128: Change `result = _find_scores_in_nuxt_data(page_data)` to `result = _find_game_details_in_nuxt_data(page_data)`
- Both `ScoreResult(...)` constructors (lines ~270 and ~306): Pass through new fields from parsed data:

```python
return ScoreResult(
    title=...,
    slug=slug,
    metascore=parsed.get("metascore"),
    metascore_review_count=parsed.get("metascore_reviews"),
    user_score=parsed.get("user_score"),
    user_review_count=parsed.get("user_reviews"),
    passed=False,
    genres=parsed.get("genres"),
    must_play=parsed.get("must_play"),
    release_date=parsed.get("release_date"),
)
```

- [ ] **Step 4: Write a failing test for game metadata extraction**

Add to `tests/unit/test_metacritic.py`:

```python
class TestFindGameDetailsInNuxtData:
    """Extraction of game metadata from Nuxt data."""

    def test_extracts_genres(self) -> None:
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        data = [
            {"score": 1, "reviewCount": 2, "userScore": {"score": 3, "reviewCount": 4}},
            {"mustPlay": False, "genres": [{"name": "Action"}], "releaseDate": "2025-01-01"},
            "x" * 2000,
        ]
        result = _find_game_details_in_nuxt_data(data)
        assert result is not None
        assert result["genres"] == ["Action"]
        assert result["must_play"] is False

    def test_extracts_release_date(self) -> None:
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        data = [
            {"score": 1, "reviewCount": 2, "userScore": {"score": 3, "reviewCount": 4}},
            {"mustPlay": True, "genres": [{"name": "RPG"}], "releaseDate": "2024-06-15"},
            "x" * 2000,
        ]
        result = _find_game_details_in_nuxt_data(data)
        assert result is not None
        assert result["release_date"] == "2024-06-15"
        assert result["must_play"] is True

    def test_backward_compat_no_metadata(self) -> None:
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        data = [
            {"score": 1, "reviewCount": 2, "userScore": {"score": 3, "reviewCount": 4}},
            "x" * 2000,
        ]
        result = _find_game_details_in_nuxt_data(data)
        assert result is not None
        assert result["metascore"] == 1.0
        assert result["user_score"] == 3.0
        assert result["genres"] is None

    def test_non_dict_items_skipped(self) -> None:
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        result = _find_game_details_in_nuxt_data([None, "string", 42])
        assert result is None
```

- [ ] **Step 5: Run test to verify it fails first, then passes after implementation**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_metacritic.py -v --no-cov`
Expected: All metacritic tests PASS

- [ ] **Step 6: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 7: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: extend ScoreResult and Nuxt parser for game metadata

- Add genres, must_play, release_date, description fields to ScoreResult
- Rename _find_scores_in_nuxt_data to _find_game_details_in_nuxt_data
- Add extraction of game metadata (genres, mustPlay, releaseDate) from Nuxt JSON
- Add tests for new extraction paths and backward compatibility"
```

---

### Task 2: Add rich colorized logging to pipeline

**Files:**
- Modify: `src/gamarr/pipeline.py`
- Modify: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write a failing test for the log helper**

Add to `tests/unit/test_pipeline.py`:

```python
class TestLogGameDetails:
    """_escape_markup helper for log output."""

    def test_escape_markup_angle_brackets(self) -> None:
        from gamarr.pipeline import _escape_markup

        assert _escape_markup("<title>") == "\\<title\\>"
        assert _escape_markup("plain text") == "plain text"
        assert _escape_markup(42) == "42"
```

Run: `cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py::TestLogGameDetails -v --no-cov`
Expected: FAIL — ImportError for `_escape_markup`

- [ ] **Step 2: Add _escape_markup and _log_game_details to pipeline.py**

Add at the top of `src/gamarr/pipeline.py` (after imports):

```python
def _escape_markup(value: object) -> str:
    """Escape Loguru markup angle brackets in user-provided values."""
    return str(value).replace("<", "\\<").replace(">", "\\>")


def _log_game_details(mc_result: Any) -> None:
    """Log a colorized summary line for a looked-up game (gamecritic-style)."""
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

- [ ] **Step 3: Add the log call in _process_entry**

In `src/gamarr/pipeline.py`, find `_process_entry` and add `_log_game_details(mc_result)` after the None check and before score evaluation:

```python
def _process_entry(...) -> dict[str, Any]:
    logger.info("Processing entry: '{}'", entry.title)

    mc_result = mc.lookup_game(...)

    if mc_result is None:
        return _handle_game_not_found(db, entry)

    _log_game_details(mc_result)  # ← NEW

    game_title = mc_result.title
    ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py::TestLogGameDetails -v --no-cov`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /data/gamarr && timeout 30 uv run pytest --no-cov -q`
Expected: All tests PASS

- [ ] **Step 6: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 7: Verify the output by running gamarr**

Run: `cd /data/gamarr && timeout 20 uv run gamarr 2>&1 | head -20`
Expected: The log line shows colorized output with genre, scores, must play, release date.

- [ ] **Step 8: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add rich gamecritic-style logging to pipeline

- Add _escape_markup helper for Loguru markup escaping
- Add _log_game_details with colorized output (Title | Metascore: X |
  User: Y | Genre: ... | Must Play: Z | Released: D)
- Call after Metacritic lookup in _process_entry()
- Match gamecritic output format"
```

---

## Spec Coverage Check

| Spec Section | Task Implementing It |
|---|---|
| 3. ScoreResult extension | Task 1 (metacritic.py) |
| 4.1 Rename + extend Nuxt parser | Task 1 (metacritic.py) |
| 4.2 Game metadata extraction strategy | Task 1 (metacritic.py) |
| 5.1 Rich log line | Task 2 (pipeline.py) |
| 5.2 Call site | Task 2 (pipeline.py) |
| 6.1 Unit tests | Task 1 + 2 (test files) |
| 6.2 Existing test updates | Task 1 (test_metacritic.py) |
