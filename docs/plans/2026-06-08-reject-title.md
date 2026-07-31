# reject_title — Filter games by title substring matching

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `reject_title` config field that filters out games whose title contains a matching substring, checked at both browse discovery and score verification stages.

**Architecture:** Follow the exact same pattern as `reject_genre` — add the field to `MetacriticPlatformConfig`, create a `_reject_by_title()` helper with case-insensitive substring matching, call it in `_is_game_eligible()` (browse stage) and `_process_verify_result()` (verify stage), and thread the parameter through the pipeline.

**Tech Stack:** Python 3.12+, Pydantic, APScheduler

---

## Files changed

| File | Change |
|------|--------|
| `src/gamarr/config.py` | Add `reject_title: list[str] = Field(default_factory=list)` to `MetacriticPlatformConfig` |
| `src/gamarr/pipeline.py` | Add `_reject_by_title()` and `_title_matches_reject()` helpers; wire into `_is_game_eligible()`, `_process_browse_games()`, `_process_verify_result()`, `_verify_pending_scores()`, `AcquisitionConfig`, `run_acquisition()` |
| `src/gamarr/scheduler.py` | Add `"reject_title": mc_cfg.reject_title` to `_build_kwargs()` |
| `configs/gamarr.yml` | Add `reject_title: []` under `metacritic.platform_overrides.pc` (optional, default is empty — auto-migration adds it) |
| `tests/unit/test_pipeline.py` | Add `TestRejectTitle` class with 5+ tests |
| `README.md` | Add `reject_title` to the config table |

---

### Task 1: Add `reject_title` to config model

**Files:**
- Modify: `src/gamarr/config.py:74`

- [ ] **Step 1: Add field to MetacriticPlatformConfig**

In `src/gamarr/config.py`, add `reject_title` after `reject_genre`:

```python
    reject_genre: list[str] = Field(default_factory=list)
    reject_title: list[str] = Field(default_factory=list)  # ← new
```

- [ ] **Step 2: Add to config YAML**

The auto-migration in `load_config()` will write the field on next run (like all new config fields). Optionally add `reject_title: []` under `metacritic.platform_overrides.pc` in `configs/gamarr.yml`.

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/config.py
git commit -m "feat(config): add reject_title to MetacriticPlatformConfig"
```

---

### Task 2: Add `_reject_by_title()` and `_title_matches_reject()` helpers

**Files:**
- Modify: `src/gamarr/pipeline.py` (after `_reject_by_genre`, around line 679)

- [ ] **Step 1: Add `_title_matches_reject()` helper**

Add before `_reject_by_genre` (around line 649):

```python
def _title_matches_reject(title: str, reject_title: list[str] | None) -> bool:
    """Return True if *title* case-insensitively matches any reject_title entry."""
    if not reject_title:
        return False
    title_lower = title.lower()
    return any(term.lower() in title_lower for term in reject_title)
```

- [ ] **Step 2: Add `_reject_by_title()` helper**

Add after `_reject_by_genre` (around line 678):

```python
def _reject_by_title(
    game: Any,
    reject_title: list[str] | None,
) -> str | None:
    """Return the first reject_title entry that matched the game title, or None.

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

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat(pipeline): add _reject_by_title and _title_matches_reject helpers"
```

---

### Task 3: Thread through pipeline and add both checkpoints

**Files:**
- Modify: `src/gamarr/pipeline.py` (multiple locations)

This task adds `reject_title` to all the pipeline plumbing and adds the two checkpoints (browse and verify).

- [ ] **Step 1: Add to AcquisitionConfig**

```python
@dataclass
class AcquisitionConfig:
    ...
    exclude_keywords: list[str] | None = None
    reject_genre: list[str] | None = None
    reject_title: list[str] | None = None  # ← new
    fitgirl_pending_days: int = 60
```

- [ ] **Step 2: Add to `run_acquisition()` signature**

Add between `reject_genre` and `apprise_urls`:

```python
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,  # ← new
    apprise_urls: list[str] | None = None,
```

- [ ] **Step 3: Pass into AcquisitionConfig construction**

```python
    cfg = AcquisitionConfig(
        ...
        exclude_keywords=exclude_keywords,
        reject_genre=reject_genre,
        reject_title=reject_title,  # ← new
    )
```

- [ ] **Step 4: Add browse-stage checkpoint**

In `_is_game_eligible()`, add `reject_title` parameter and check:

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

- [ ] **Step 5: Thread through `_process_browse_games()`**

Add `reject_title` parameter and pass to `_is_game_eligible()`:

```python
def _process_browse_games(
    ...
    *,
    pending_days: int = 30,
    days_since_release: int = 0,
    exclude_keywords: list[str] | None = None,
    reject_title: list[str] | None = None,  # ← new
) -> int:
```

Update the `_is_game_eligible` call:

```python
        if not _is_game_eligible(game, db, thresholds, days_since_release, exclude_keywords, reject_title=reject_title):
            continue
```

- [ ] **Step 6: Thread through the `_process_browse_games` call site in `_run_discovery_phases()`**

```python
                new_pending = _process_browse_games(
                    browse_games,
                    platform,
                    db,
                    thresholds,
                    pending_days=cfg.pending_days,
                    days_since_release=cfg.days_since_release,
                    exclude_keywords=cfg.exclude_keywords or None,
                    reject_title=cfg.reject_title,  # ← new
                )
```

- [ ] **Step 7: Add verify-stage checkpoint in `_process_verify_result()`**

Add `reject_title` parameter and check after the `reject_genre` check:

```python
def _process_verify_result(
    ...
    *,
    max_verify_attempts: int = 6,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,  # ← new
    fitgirl_pending_days: int = 60,
) -> bool:
```

Add the reject_title check right after the reject_genre block:

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

- [ ] **Step 8: Thread through `_verify_pending_scores()`**

```python
def _verify_pending_scores(
    ...
    max_verify_attempts: int = 6,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,  # ← new
    fitgirl_pending_days: int = 60,
) -> int:
```

Update the `_process_verify_result` call inside:

```python
            if _process_verify_result(
                db,
                game,
                result,
                thresholds,
                max_verify_attempts=max_verify_attempts,
                reject_genre=reject_genre,
                reject_title=reject_title,  # ← new
                fitgirl_pending_days=fitgirl_pending_days,
            ):
                removed += 1
```

- [ ] **Step 9: Pass from `_run_discovery_phases()`**

```python
            removed = _verify_pending_scores(
                db,
                mc,
                platform,
                thresholds,
                cache_ttl_days=cfg.cache_ttl_days,
                max_verify=len(pending_games) if cfg.max_games == 0 else min(len(pending_games), cfg.max_games),
                max_verify_attempts=cfg.max_verify_attempts,
                reject_genre=cfg.reject_genre,
                reject_title=cfg.reject_title,  # ← new
                fitgirl_pending_days=cfg.fitgirl_pending_days,
            )
```

- [ ] **Step 10: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat(pipeline): thread reject_title through pipeline with both checkpoints"
```

---

### Task 4: Wire through scheduler

**Files:**
- Modify: `src/gamarr/scheduler.py:62`

- [ ] **Step 1: Add to `_build_kwargs()`**

```python
        "reject_genre": mc_cfg.reject_genre,
        "reject_title": mc_cfg.reject_title,  # ← new
```

- [ ] **Step 2: Commit**

```bash
git add src/gamarr/scheduler.py
git commit -m "feat(scheduler): wire reject_title from config to pipeline"
```

---

### Task 5: Write tests

**Files:**
- Test: `tests/unit/test_pipeline.py` (add at the end, before or after `TestFitgirlPendingExpiry`)

- [ ] **Step 1: Write the failing tests**

Add a new class `TestRejectTitle` with these tests:

```python
class TestRejectTitle:
    """Tests for reject_title title substring filtering."""

    def test_reject_title_at_browse(self, tmp_path: Path) -> None:
        """Game with title matching reject_title should be skipped at browse stage."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Resident Evil 4 Remake",
                "slug": "resident-evil-4-remake",
                "score": 85,
                "critic_review_count": 20,
                "user_rating": 8.0,
                "user_review_count": 100,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games, "pc", db, thresholds,
            reject_title=["Remake"],
        )
        assert added == 0, "Game with matching title should not be added"
        assert not db.is_pending("resident-evil-4-remake")
        db.close()

    def test_reject_title_at_verify(self, tmp_path: Path) -> None:
        """Game with title matching reject_title should be removed during verification."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="vr-game",
            game_title="VR Adventure",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        assert db.is_pending("vr-game") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_title=["VR"])
        assert removed == 1, "Game with matching title should be removed"
        assert not db.is_pending("vr-game"), "Game should no longer be pending"
        db.close()

    def test_reject_title_no_match(self, tmp_path: Path) -> None:
        """Game with non-matching title should proceed normally."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Elden Ring",
                "slug": "elden-ring",
                "score": 96,
                "critic_review_count": 120,
                "user_rating": 8.5,
                "user_review_count": 5000,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games, "pc", db, thresholds,
            reject_title=["Remake"],
        )
        assert added == 1, "Non-matching game should be added"
        assert db.is_pending("elden-ring")
        db.close()

    def test_reject_title_empty_list(self, tmp_path: Path) -> None:
        """Empty reject_title should have no effect."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "VR Adventure",
                "slug": "vr-adventure",
                "score": 85,
                "critic_review_count": 20,
                "user_rating": 8.0,
                "user_review_count": 100,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games, "pc", db, thresholds,
            reject_title=[],
        )
        assert added == 1, "Empty reject_title should not filter anything"
        db.close()

    def test_reject_title_case_insensitive(self, tmp_path: Path) -> None:
        """reject_title should match case-insensitively."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "The Legend of Zelda: Remake",
                "slug": "zelda-remake",
                "score": 95,
                "critic_review_count": 100,
                "user_rating": 9.0,
                "user_review_count": 5000,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games, "pc", db, thresholds,
            reject_title=["remake"],  # lowercase, title has "Remake"
        )
        assert added == 0, "reject_title should match case-insensitively"
        db.close()

    def test_reject_title_substring(self, tmp_path: Path) -> None:
        """reject_title should match partial substrings, not just whole words."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Collection of Classic Games Vol 3",
                "slug": "collection-classic-3",
                "score": 80,
                "critic_review_count": 10,
                "user_rating": 7.5,
                "user_review_count": 50,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games, "pc", db, thresholds,
            reject_title=["Classic"],  # "Classic" is a substring of "Collection"
        )
        assert added == 0, "reject_title should match on substrings"
        db.close()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/unit/test_pipeline.py::TestRejectTitle -v`
Expected: PASS (6 passed)

- [ ] **Step 3: Run all pipeline tests to confirm nothing broken**

Run: `pytest tests/unit/test_pipeline.py -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test(pipeline): add reject_title tests"
```

---

### Task 6: Update README and run full suite

- [ ] **Step 1: Update README.md**

Add `reject_title` to the `metacritic.platform_overrides.<platform>` config table:

```
| `reject_title` | Reject games whose title contains any of these substrings (case-insensitive). E.g. `["Remake"]` matches "Resident Evil 4 Remake", "Remake Collection". | `[]` |
```

- [ ] **Step 2: Run full test suite**

Run: `pytest --cov=src/gamarr --cov-fail-under=95 -q`
Expected: All tests pass, coverage ≥ 95%

- [ ] **Step 3: Run quality checks**

Run: `uv run ruff check --fix . && uv run ruff format .`
Expected: All checks passed

Run: `uv run mypy .`
Expected: No type issues

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document reject_title in README"
```

---

## Self-Review

### Spec coverage
- ✅ Config model: `reject_title: list[str] = Field(default_factory=list)` (Task 1)
- ✅ `_reject_by_title()` helper (Task 2)
- ✅ `_title_matches_reject()` helper (Task 2)
- ✅ Browse-stage checkpoint in `_is_game_eligible()` (Task 3)
- ✅ Verify-stage checkpoint in `_process_verify_result()` (Task 3)
- ✅ Pipeline threading through `AcquisitionConfig`, `run_acquisition()`, `_verify_pending_scores()` (Task 3)
- ✅ Scheduler wiring in `_build_kwargs()` (Task 4)
- ✅ Tests for browse rejection, verify rejection, no-match, empty list, case-insensitive, substring (Task 5)
- ✅ Non-goals respected: `exclude_keywords` unchanged, FitGirl matching unaffected

### Placeholder scan
- No "TBD", "TODO", "implement later", or "fill in details"
- No "write tests for the above" without test code
- All steps have complete code shown
- All file paths exact

### Type consistency
- `reject_title: list[str] | None = None` used consistently across `AcquisitionConfig`, `run_acquisition()`, `_process_browse_games()`, `_is_game_eligible()`, `_process_verify_result()`, `_verify_pending_scores()`
- `reject_title: list[str] = Field(default_factory=list)` in config model
- `_reject_by_title(game, reject_title)` matches how `_reject_by_genre(game, result, reject_genre)` is called
- `_title_matches_reject(title, reject_title)` uses the raw title string at browse stage
