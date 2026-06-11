# Age Recheck Weeks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `age_recheck_weeks` config option that permanently marks old verified games as "Processed" instead of re-checking them every cycle.

**Architecture:** A new config field on `MetacriticPlatformConfig` flows through the existing call chain (`scheduler._build_kwargs` → `run_acquisition` → `AcquisitionConfig` → `_verify_pending_scores` → `_process_verify_batch` → `_process_verify_result`). After scores pass thresholds, a new `_should_seal_by_age` function checks `release_date` against `age_recheck_weeks`. If old enough, `_seal_game` records the game as "Processed" and removes it from pending.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy

---

### Task 1: Add config field + thread through call chain

**Files:**
- Modify: `src/gamarr/config.py:73` — add field to `MetacriticPlatformConfig`
- Modify: `src/gamarr/pipeline.py:116` — add field to `AcquisitionConfig`
- Modify: `src/gamarr/pipeline.py:196` — add param to `run_acquisition()`
- Modify: `src/gamarr/pipeline.py:222` — pass into `AcquisitionConfig()`
- Modify: `src/gamarr/pipeline.py:352` — pass to `_verify_pending_scores()` via `cfg`
- Modify: `src/gamarr/pipeline.py:929` — add param to `_verify_pending_scores()`
- Modify: `src/gamarr/pipeline.py:1011` — pass to `_process_verify_batch()`
- Modify: `src/gamarr/pipeline.py:847` — add param to `_process_verify_batch()`
- Modify: `src/gamarr/pipeline.py:892` — pass to `_process_verify_result()`
- Modify: `src/gamarr/pipeline.py:755` — add param to `_process_verify_result()`
- Modify: `src/gamarr/scheduler.py:116` — add key to `_build_kwargs()`

- [ ] **Step 1: Write the failing config model test**

```python
# In tests/unit/test_config.py, add to TestConfigModels:

def test_age_recheck_weeks_default(self) -> None:
    """MetacriticPlatformConfig.age_recheck_weeks defaults to None."""
    from gamarr.config import MetacriticPlatformConfig

    cfg = MetacriticPlatformConfig()
    assert cfg.age_recheck_weeks is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py::TestConfigModels::test_age_recheck_weeks_default -v --tb=short`
Expected: FAIL with `AttributeError: 'MetacriticPlatformConfig' object has no attribute 'age_recheck_weeks'`

- [ ] **Step 3: Add `age_recheck_weeks` to `MetacriticPlatformConfig`**

In `src/gamarr/config.py:73`, add the field after `reject_title`:

```python
    reject_title: list[str] = Field(default_factory=list)  # case-insensitive substrings
    age_recheck_weeks: int | None = Field(default=None, ge=0)
```

- [ ] **Step 4: Add `age_recheck_weeks` to `AcquisitionConfig`**

In `src/gamarr/pipeline.py:116`, add the field after `notify_on_scrape_failure`:

```python
    notify_on_scrape_failure: bool = True
    age_recheck_weeks: int | None = None
```

- [ ] **Step 5: Add param to `run_acquisition()`**

After the `reject_title` param in `src/gamarr/pipeline.py:196`:

```python
    reject_title: list[str] | None = None,
    age_recheck_weeks: int | None = None,
```

Pass it into `AcquisitionConfig` at line 222:

```python
        reject_title=reject_title,
        age_recheck_weeks=age_recheck_weeks,
```

Pass it via `cfg` to `_verify_pending_scores` at line 352:

```python
                reject_title=cfg.reject_title,
                age_recheck_weeks=cfg.age_recheck_weeks,
```

- [ ] **Step 6: Add param to `_verify_pending_scores()`**

In `src/gamarr/pipeline.py:929`, after `reject_title`:

```python
    reject_title: list[str] | None = None,
    fitgirl_recheck_days: int = 60,
    notifier: Any = None,
```

Add `age_recheck_weeks: int | None = None` after `reject_title`.

Pass it to `_process_verify_batch` at line 1011:

```python
        cache_details_days=cache_details_days,
        reject_genre=reject_genre,
        reject_title=reject_title,
        age_recheck_weeks=age_recheck_weeks,
        fitgirl_recheck_days=fitgirl_recheck_days,
```

- [ ] **Step 7: Add param to `_process_verify_batch()`**

In `src/gamarr/pipeline.py:847`, after `reject_title`:

```python
    reject_title: list[str] | None = None,
    fitgirl_recheck_days: int = 60,
    cancel_event: threading.Event | None = None,
```

Add `age_recheck_weeks: int | None = None` after `reject_title`.

Pass it to `_process_verify_result` at line 892:

```python
                reject_genre=reject_genre,
                reject_title=reject_title,
                age_recheck_weeks=age_recheck_weeks,
                fitgirl_recheck_days=fitgirl_recheck_days,
```

- [ ] **Step 8: Add param to `_process_verify_result()`**

In `src/gamarr/pipeline.py:755`, after `reject_title`:

```python
    reject_title: list[str] | None = None,
    fitgirl_recheck_days: int = 60,
```

Add `age_recheck_weeks: int | None = None` after `reject_title`.

- [ ] **Step 9: Add key to `_build_kwargs()`**

In `src/gamarr/scheduler.py:116`, after `reject_title`:

```python
        "reject_title": mc_cfg.reject_title,
        "age_recheck_weeks": mc_cfg.age_recheck_weeks,
        "recheck_days": mc_cfg.recheck_days,
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py::TestConfigModels::test_age_recheck_weeks_default -v --tb=short`
Expected: PASS

Run: `uv run pytest tests/unit/test_config.py -x -q --tb=short`
Expected: all config tests pass

- [ ] **Step 11: Commit**

```bash
git add src/gamarr/config.py src/gamarr/pipeline.py src/gamarr/scheduler.py tests/unit/test_config.py
git commit -m "feat: add age_recheck_weeks config field threaded through call chain"
```

---

### Task 2: Add sealing logic to pipeline.py

**Files:**
- Modify: `src/gamarr/pipeline.py` — add `_should_seal_by_age`, `_seal_game`, modify `_process_verify_result`
- Test: `tests/unit/test_pipeline.py` — add tests for sealing

- [ ] **Step 1: Write the failing `_should_seal_by_age` test**

Add to `tests/unit/test_pipeline.py` (add after the `TestEvaluateScoresCoverage` class or near the other helper tests):

```python
from datetime import datetime, timedelta


class TestSealByAge:
    """Tests for _should_seal_by_age helper."""

    def test_seal_by_age_returns_true_for_old_game(self) -> None:
        """A game older than age_recheck_weeks should be sealed."""
        from gamarr.pipeline import _should_seal_by_age

        class FakeGame:
            release_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

        result = _should_seal_by_age(FakeGame(), age_recheck_weeks=52)  # 52 weeks = 364 days
        assert result is True

    def test_seal_by_age_returns_false_for_recent_game(self) -> None:
        """A game newer than age_recheck_weeks should NOT be sealed."""
        from gamarr.pipeline import _should_seal_by_age

        class FakeGame:
            release_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        result = _should_seal_by_age(FakeGame(), age_recheck_weeks=52)
        assert result is False

    def test_seal_by_age_returns_false_when_disabled(self) -> None:
        """age_recheck_weeks=None or 0 should disable sealing."""
        from gamarr.pipeline import _should_seal_by_age

        class FakeGame:
            release_date = "2020-01-01"

        assert _should_seal_by_age(FakeGame(), age_recheck_weeks=None) is False
        assert _should_seal_by_age(FakeGame(), age_recheck_weeks=0) is False

    def test_seal_by_age_returns_false_when_no_release_date(self) -> None:
        """A game with no release_date should NOT be sealed."""
        from gamarr.pipeline import _should_seal_by_age

        class FakeGame:
            release_date = None

        result = _should_seal_by_age(FakeGame(), age_recheck_weeks=52)
        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py::TestSealByAge -x -v --tb=short`
Expected: FAIL with `ImportError` / `Function not defined`

- [ ] **Step 3: Add `_should_seal_by_age` function**

Add this function in `src/gamarr/pipeline.py`, just before `_process_verify_result` (around line 745):

```python
def _should_seal_by_age(game: Any, age_recheck_weeks: int | None) -> bool:
    """Return True if *game* is old enough to be permanently sealed.

    When *age_recheck_weeks* is ``None`` or ``0``, sealing is disabled.
    Games without a ``release_date`` are never sealed (we can't determine
    their age).
    """
    if not age_recheck_weeks:
        return False
    if not getattr(game, "release_date", None):
        return False
    return _is_older_than(game.release_date, days=age_recheck_weeks * 7)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_pipeline.py::TestSealByAge -x -v --tb=short`
Expected: PASS

- [ ] **Step 5: Write the failing `_seal_game` test**

```python
    def test_seal_game_records_and_removes(self, tmp_path: Path) -> None:
        """_seal_game should record as Processed and remove from pending."""
        import datetime

        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="old-game",
            game_title="Old Game",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            release_date="2020-01-01",
            expires_at=expires,
        )
        db.update_pending_scores(slug="old-game", metascore=85.0, user_score=8.0)

        from gamarr.pipeline import _seal_game

        import types

        game = db.get_pending(platform="pc")[0]
        result = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
        )
        sealed = _seal_game(db, game, result)
        assert sealed is True, "_seal_game should return True"
        assert not db.is_pending("old-game"), "Game should be removed from pending"
        stats = db.get_stats()
        assert stats["total"] == 1, "One history record should exist"
        db.close()
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pipeline.py::TestSealByAge::test_seal_game_records_and_removes -v --tb=short`
Expected: FAIL with `Function not defined`

- [ ] **Step 7: Add `_seal_game` function**

Add this in `src/gamarr/pipeline.py`, just after `_should_seal_by_age`:

```python
def _seal_game(db: Database, game: Any, result: Any) -> bool:
    """Permanently record *game* as processed and remove from pending.

    Writes a history row with ``result="Processed"`` and removes the
    pending row.  Returns ``True`` to signal the caller that the game
    was removed.
    """
    db.record_processed(
        source="metacritic",
        source_title=str(game.game_title),
        source_url=f"mc:{game.slug}",
        game_title=str(game.game_title),
        platform=str(game.platform),
        metascore=result.metascore,
        user_score=result.user_score,
        result="Processed",
        result_details=f"Game older than age_recheck_weeks threshold",
    )
    db.remove_pending(str(game.slug))
    return True
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_pipeline.py::TestSealByAge::test_seal_game_records_and_removes -v --tb=short`
Expected: PASS

- [ ] **Step 9: Update `_process_verify_result` to call the new functions**

Modify `_process_verify_result` in `src/gamarr/pipeline.py` (around line 795, after the `scores_fail_check` block and before the existing "passes score check" block). Add the seal check:

```python
    if _scores_fail_check(result, thresholds):
        logger.debug(
            "Keeping '{}' in queue \u2014 Metacritic scores ({}, {}) below thresholds",
            game.game_title,
            result.metascore,
            result.user_score,
        )
        return False

    # NEW: Seal old games instead of keeping them in the pending queue
    if _should_seal_by_age(game, age_recheck_weeks):
        return _seal_game(db, game, result)

    db.update_pending_scores(
        slug=str(game.slug),
        metascore=result.metascore,
```

- [ ] **Step 10: Write integration test for the full `_process_verify_result` path**

```python
    def test_process_verify_result_seals_old_game(self, tmp_path: Path) -> None:
        """A pending game older than age_recheck_weeks should be sealed
        when its scores pass thresholds."""
        import datetime
        from unittest.mock import MagicMock

        import types

        from gamarr.database import Database
        from gamarr.pipeline import _process_verify_result

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="ancient-game",
            game_title="Ancient Game",
            platform="pc",
            metascore=80.0,
            user_score=7.5,
            release_date="2010-06-01",
            expires_at=expires,
        )
        game = db.get_pending()[0]
        result = types.SimpleNamespace(
            metascore=80.0,
            metascore_review_count=50,
            user_score=7.5,
            user_review_count=100,
            genres=["Action"],
        )
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        removed = _process_verify_result(
            db, game, result, thresholds,
            age_recheck_weeks=52,  # 52 weeks ≈ 1 year
        )
        assert removed is True, "Old game should be sealed"
        assert not db.is_pending("ancient-game"), "Should be removed from pending"
        db.close()

    def test_process_verify_result_keeps_recent_game(self, tmp_path: Path) -> None:
        """A recent game should NOT be sealed."""
        import datetime
        import types

        from gamarr.database import Database
        from gamarr.pipeline import _process_verify_result

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        recent = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        db.record_pending(
            slug="recent-game",
            game_title="Recent Game",
            platform="pc",
            metascore=80.0,
            user_score=7.5,
            release_date=recent,
            expires_at=expires,
        )
        game = db.get_pending()[0]
        result = types.SimpleNamespace(
            metascore=80.0,
            metascore_review_count=50,
            user_score=7.5,
            user_review_count=100,
            genres=["Action"],
        )
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        removed = _process_verify_result(
            db, game, result, thresholds,
            age_recheck_weeks=52,
        )
        assert removed is False, "Recent game should NOT be sealed"
        assert db.is_pending("recent-game"), "Should still be pending"
        db.close()
```

- [ ] **Step 11: Run all new tests**

Run: `uv run pytest tests/unit/test_pipeline.py::TestSealByAge -x -v --tb=short`
Expected: All 6 tests PASS

- [ ] **Step 12: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: add age-based game sealing logic"
```

---

### Task 3: Verify config migration, YAML, and README

**Files:**
- Modify: `configs/gamarr.yml` — add `age_recheck_weeks` to platform overrides
- Modify: `README.md` — document the new config option
- Modify: `tests/unit/test_config.py` — add migration test

- [ ] **Step 1: Write migration test**

```python
# In TestConfigModels:

def test_age_recheck_weeks_in_default_config_dict(self) -> None:
    """The default config dict should contain age_recheck_weeks: null."""
    from gamarr.config import _default_config_dict

    defaults = _default_config_dict()
    mc_pc = defaults["metacritic"]["platform_overrides"]["pc"]
    assert "age_recheck_weeks" in mc_pc
    assert mc_pc["age_recheck_weeks"] is None
```

- [ ] **Step 2: Run migration test**

Run: `uv run pytest tests/unit/test_config.py::TestConfigModels::test_age_recheck_weeks_in_default_config_dict -v --tb=short`
Expected: PASS (the default config generation picks up the new field automatically)

- [ ] **Step 3: Update `configs/gamarr.yml`**

Add `age_recheck_weeks: null` under the `pc` platform overrides (around line 33):

```yaml
      reject_title: []
      age_recheck_weeks: null
```

- [ ] **Step 4: Update `README.md`**

Add a row in the config table, after `cutoff_weeks` (around line 134):

```markdown
| `age_recheck_weeks` | Games older than this (weeks since release) are permanently processed once their Metacritic scores pass thresholds. `null` or `0` = disabled. | `null` |
```

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -x -q --tb=short`
Expected: All tests pass

- [ ] **Step 6: Run linters**

Run: `uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 7: Commit**

```bash
git add configs/gamarr.yml README.md tests/unit/test_config.py
git commit -m "docs: document age_recheck_weeks config option"
```
