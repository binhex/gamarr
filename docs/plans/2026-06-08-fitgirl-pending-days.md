# FitGirl Pending Days — Separate pending expiry for FitGirl matching

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single `pending_days` into two independent expiry values — one for score-waiting (`metacritic.platform_overrides.pc.pending_days`, default 30) and one for FitGirl-matching (`sources.fitgirl.pending_days`, default 60).

**Architecture:** Add `pending_days` to `FitGirlSourceConfig`, thread `fitgirl_pending_days` through the pipeline's `AcquisitionConfig` → `_verify_pending_scores()` → `_process_verify_result()`, and call a new `Database.update_pending_expiry()` to recalculate `expires_at` when a game's scores pass verification, giving it a fresh expiry window for the FitGirl matching phase.

**Tech Stack:** Python 3.12+, SQLAlchemy, Pydantic, APScheduler

---

## Files changed

| File | Change |
|------|--------|
| `src/gamarr/config.py` | Add `pending_days: int = 60` to `FitGirlSourceConfig` |
| `src/gamarr/database.py` | Add `update_pending_expiry()` method |
| `src/gamarr/pipeline.py` | Add `fitgirl_pending_days` to `AcquisitionConfig`, `run_acquisition()`, `_verify_pending_scores()`, `_process_verify_result()`; add expiry update call in scores-pass branch |
| `src/gamarr/scheduler.py` | Add `fitgirl_pending_days` to `_build_kwargs()` return dict |
| `configs/gamarr.yml` | Add `pending_days: 60` under `sources.fitgirl` |
| `tests/unit/test_database.py` | Add `test_update_pending_expiry` |
| `tests/unit/test_pipeline.py` | Add three tests for expiry update behaviour |

---

### Task 1: Add `pending_days` to `FitGirlSourceConfig`

**Files:**
- Modify: `src/gamarr/config.py:30`
- Modify: `configs/gamarr.yml`

- [ ] **Step 1: Add field to FitGirlSourceConfig**

In `src/gamarr/config.py`, add `pending_days: int = 60` to `FitGirlSourceConfig`:

```python
class FitGirlSourceConfig(BaseModel):
    """FitGirl Repacks source settings."""
    enabled: bool = True
    rss_url: str = "https://fitgirl-repacks.site/feed/"
    platform: str = "pc"
    cache_ttl_hours: int = Field(default=6, gt=0, le=168)
    exclude_keywords: list[str] = Field(default_factory=list)
    pending_days: int = 60  # ← new
```

- [ ] **Step 2: Add field to config YAML**

In `configs/gamarr.yml`, add `pending_days: 60` under `sources.fitgirl`:

```yaml
sources:
  fitgirl:
    enabled: true
    rss_url: https://fitgirl-repacks.site/feed/
    platform: pc
    cache_ttl_hours: 6
    exclude_keywords:
    - 'hv'
    pending_days: 60
```

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/config.py configs/gamarr.yml
git commit -m "feat(config): add pending_days to FitGirlSourceConfig"
```

---

### Task 2: Add `update_pending_expiry()` to Database

**Files:**
- Modify: `src/gamarr/database.py:180` (after `reset_verify_attempts`)
- Test: `tests/unit/test_database.py:0` (new test class)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_database.py`, inside a new class `TestPendingExpiry`:

```python
class TestPendingExpiry:
    """update_pending_expiry method tests."""

    def test_update_pending_expiry(self, tmp_path: Path) -> None:
        """update_pending_expiry should recalculate expires_at to now + pending_days."""
        import datetime

        db = Database(str(tmp_path / "test.db"))
        # Insert a pending game with a past expiry
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=10)).isoformat()
        db.record_pending(
            slug="expiry-test",
            game_title="Expiry Test",
            platform="pc",
            expires_at=past,
        )
        # Call update_pending_expiry with 60 days
        db.update_pending_expiry("expiry-test", 60)
        # Retrieve and verify new expiry
        pending = db.get_pending()
        assert len(pending) == 1
        row = pending[0]
        new_expiry = datetime.datetime.fromisoformat(row.expires_at)
        expected_min = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=59)
        expected_max = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=61)
        assert expected_min < new_expiry < expected_max, (
            f"Expected expiry near now+60d, got {new_expiry}"
        )
        db.close()

    def test_update_pending_expiry_nonexistent_slug(self, tmp_path: Path) -> None:
        """update_pending_expiry should silently skip non-existent slugs."""
        db = Database(str(tmp_path / "test.db"))
        db.update_pending_expiry("does-not-exist", 60)  # should not raise
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_database.py::TestPendingExpiry -v`
Expected: FAIL with "Database object has no attribute 'update_pending_expiry'"

- [ ] **Step 3: Write minimal implementation**

Add to `src/gamarr/database.py`, after `reset_verify_attempts()` (around line 180):

```python
def update_pending_expiry(self, slug: str, pending_days: int) -> None:
    """Recalculate expires_at to now + pending_days for a pending game.

    Used to extend the expiry window when a game transitions from
    the score-waiting phase to the FitGirl-matching phase.
    """
    expires_at = (
        datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=pending_days)
    ).isoformat()
    with self._session() as session:
        row = session.get(PendingGame, slug)
        if row is not None:
            row.expires_at = expires_at
            session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_database.py::TestPendingExpiry -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/database.py tests/unit/test_database.py
git commit -m "feat(db): add update_pending_expiry method"
```

---

### Task 3: Add `fitgirl_pending_days` to AcquisitionConfig and `run_acquisition()`

**Files:**
- Modify: `src/gamarr/pipeline.py:95` (AcquisitionConfig)
- Modify: `src/gamarr/pipeline.py:162` (run_acquisition signature)
- Modify: `src/gamarr/pipeline.py:196` (AcquisitionConfig construction)

- [ ] **Step 1: Add field to AcquisitionConfig**

In `src/gamarr/pipeline.py`, add `fitgirl_pending_days: int = 60` to `AcquisitionConfig`:

```python
@dataclass
class AcquisitionConfig:
    """Thresholds and settings for the acquisition run."""
    min_metascore: int
    min_metascore_reviews: int
    min_user_score: float
    min_user_reviews: int
    days_since_release: int
    cache_ttl_days: int = 7
    cache_ttl_hours: int = 4
    enabled: bool = True
    pending_days: int = 30
    max_games: int = 1000
    max_verify_attempts: int = 6
    cutoff_weeks: int | None = None
    exclude_keywords: list[str] | None = None
    reject_genre: list[str] | None = None
    fitgirl_pending_days: int = 60  # ← new
```

- [ ] **Step 2: Add parameter to `run_acquisition()` signature**

In `src/gamarr/pipeline.py`, add `fitgirl_pending_days: int = 60` between `pending_days` and `max_games`:

```python
    pending_days: int = 30,
    fitgirl_pending_days: int = 60,  # ← new
    max_games: int = 1000,
```

- [ ] **Step 3: Pass into AcquisitionConfig construction**

In `src/gamarr/pipeline.py`, add to the `AcquisitionConfig()` call:

```python
    cfg = AcquisitionConfig(
        ...
        pending_days=pending_days,
        fitgirl_pending_days=fitgirl_pending_days,  # ← new
        max_games=max_games,
        ...
    )
```

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat(pipeline): add fitgirl_pending_days to AcquisitionConfig and run_acquisition"
```

---

### Task 4: Thread `fitgirl_pending_days` through `_verify_pending_scores()` and `_process_verify_result()`

**Files:**
- Modify: `src/gamarr/pipeline.py:674` (`_process_verify_result` signature)
- Modify: `src/gamarr/pipeline.py:700` (inside scores-pass branch)
- Modify: `src/gamarr/pipeline.py:747` (`_verify_pending_scores` signature)
- Modify: `src/gamarr/pipeline.py:810` (call to `_process_verify_result`)
- Modify: `src/gamarr/pipeline.py:316` (call site in `_run_discovery_phases`)

- [ ] **Step 1: Add `fitgirl_pending_days` to `_process_verify_result()`**

Change the signature (line ~674):

```python
def _process_verify_result(
    db: Database,
    game: Any,
    result: Any,
    thresholds: dict[str, Any],
    *,
    max_verify_attempts: int = 6,
    reject_genre: list[str] | None = None,
    fitgirl_pending_days: int = 60,  # ← new
) -> bool:
```

Update the docstring to document the new parameter.

- [ ] **Step 2: Add expiry update call in scores-pass branch**

In `_process_verify_result()`, after `db.reset_verify_attempts(str(game.slug))` and the `logger.debug(...)` line (around line ~727), add:

```python
    if fitgirl_pending_days:
        db.update_pending_expiry(str(game.slug), fitgirl_pending_days)
```

The `if fitgirl_pending_days:` guard ensures backward compatibility when set to 0.

The full scores-pass branch should look like:

```python
    db.update_pending_scores(
        slug=str(game.slug),
        metascore=result.metascore,
        metascore_reviews=result.metascore_review_count,
        user_score=result.user_score,
        user_reviews=result.user_review_count,
    )
    db.reset_verify_attempts(str(game.slug))
    if fitgirl_pending_days:
        db.update_pending_expiry(str(game.slug), fitgirl_pending_days)
    logger.debug(
        "'{}' passed score check — ({}, {}) with ({} reviews, {} reviews)",
        game.game_title,
        result.metascore,
        result.user_score,
        result.metascore_review_count or 0,
        result.user_review_count or 0,
    )
    return False
```

- [ ] **Step 3: Add `fitgirl_pending_days` to `_verify_pending_scores()`**

Change the signature (line ~747):

```python
def _verify_pending_scores(
    db: Database,
    mc: MetacriticClient,
    platform: str,
    thresholds: dict[str, Any],
    *,
    cache_ttl_days: int = 7,
    max_verify: int = 50,
    max_verify_attempts: int = 6,
    reject_genre: list[str] | None = None,
    fitgirl_pending_days: int = 60,  # ← new
) -> int:
```

Add `fitgirl_pending_days` to the docstring Args section.

- [ ] **Step 4: Pass `fitgirl_pending_days` through to `_process_verify_result()`**

In the `_verify_pending_scores()` call to `_process_verify_result()` (around line ~810), add:

```python
            if _process_verify_result(
                db,
                game,
                result,
                thresholds,
                max_verify_attempts=max_verify_attempts,
                reject_genre=reject_genre,
                fitgirl_pending_days=fitgirl_pending_days,  # ← new
            ):
                removed += 1
```

- [ ] **Step 5: Pass `fitgirl_pending_days` from `_run_discovery_phases()` call site**

In the call to `_verify_pending_scores()` (line ~316), add:

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
                fitgirl_pending_days=cfg.fitgirl_pending_days,  # ← new
            )
```

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat(pipeline): thread fitgirl_pending_days through verify pipeline"
```

---

### Task 5: Wire `fitgirl_pending_days` through scheduler

**Files:**
- Modify: `src/gamarr/scheduler.py:36` (`_build_kwargs`)

- [ ] **Step 1: Add fitgirl_pending_days to scheduler kwargs**

In `src/gamarr/scheduler.py`, inside `_build_kwargs()`, add after existing fitgirl entries:

```python
        "fitgirl_cache_ttl_hours": config.sources.fitgirl.cache_ttl_hours,
        "fitgirl_exclude_keywords": config.sources.fitgirl.exclude_keywords,
        "fitgirl_pending_days": config.sources.fitgirl.pending_days,  # ← new
        "library_paths": config.library.paths,
```

- [ ] **Step 2: Commit**

```bash
git add src/gamarr/scheduler.py
git commit -m "feat(scheduler): wire fitgirl_pending_days from config to pipeline"
```

---

### Task 6: Write pipeline tests for expiry update behaviour

**Files:**
- Test: `tests/unit/test_pipeline.py` (add to `TestVerifyPendingScores` area, or a new class)

- [ ] **Step 1: Write the three failing tests**

After the existing `TestMetacriticBrowse` class (or in a new `TestFitgirlPendingExpiry` class), add:

```python
class TestFitgirlPendingExpiry:
    """Tests for fitgirl_pending_days expiry recalculation."""

    def test_fitgirl_pending_days_updates_expiry(self, tmp_path: Path) -> None:
        """Game with passing scores should have expires_at recalculated to now + fitgirl_pending_days."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="passing-game",
            game_title="Passing Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )
        original_expiry = expires  # Capture before verification

        mock_mc = MagicMock()
        import types
        mock_result = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=True,
            release_date="2026-06-01",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(db, mock_mc, "pc", thresholds, fitgirl_pending_days=60)

        # Game should still be pending
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        row = pending[0]
        # Expiry should be recalculated to now + 60 days (not the original +30)
        new_expiry = datetime.datetime.fromisoformat(row.expires_at)
        expected_min = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=59)
        expected_max = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=61)
        assert expected_min < new_expiry < expected_max, (
            f"Expiry should be near now+60d, got {new_expiry}"
        )
        # Verify it's different from the original
        assert row.expires_at != original_expiry, "Expiry should have been updated"
        db.close()

    def test_fitgirl_pending_days_zero_disabled(self, tmp_path: Path) -> None:
        """fitgirl_pending_days=0 should NOT update expiry."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="zero-days-game",
            game_title="Zero Days Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types
        mock_result = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=True,
            release_date="2026-06-01",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(db, mock_mc, "pc", thresholds, fitgirl_pending_days=0)

        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        # Expiry should be unchanged
        assert pending[0].expires_at == expires, (
            f"Expiry should still be {expires}, got {pending[0].expires_at}"
        )
        db.close()

    def test_fitgirl_pending_days_does_not_affect_failure(self, tmp_path: Path) -> None:
        """Game with failing scores should NOT have its expiry updated."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="failing-game",
            game_title="Failing Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types
        mock_result = types.SimpleNamespace(
            metascore=62.0,
            metascore_review_count=25,
            user_score=3.3,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(db, mock_mc, "pc", thresholds, fitgirl_pending_days=60)

        # Game should still be pending (re-check)
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        # Expiry must NOT be updated (still original +30d)
        assert pending[0].expires_at == expires, (
            f"Expiry should be unchanged ({expires}), got {pending[0].expires_at}"
        )
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_pipeline.py::TestFitgirlPendingExpiry -v`
Expected: FAIL — `_verify_pending_scores` doesn't accept `fitgirl_pending_days` yet

- [ ] **Step 3: Run all pipeline tests after implementation from Task 4**

Run: `pytest tests/unit/test_pipeline.py -v`
Expected: PASS (all existing tests + 3 new tests)

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test(pipeline): add fitgirl_pending_days expiry update tests"
```

---

### Task 7: Run all tests and fix any issues

- [ ] **Step 1: Run full test suite**

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 2: Run quality checks**

Run: `uv run ruff check --fix . && uv run ruff format .`
Expected: No errors

Run: `uv run mypy .`
Expected: No type errors

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "chore: final cleanup after fitgirl_pending_days implementation"
```

---

## Self-Review

### Spec coverage check
- ✅ Config model: `FitGirlSourceConfig.pending_days` (Task 1)
- ✅ AcquisitionConfig: `fitgirl_pending_days` field (Task 3)
- ✅ `run_acquisition()` parameter (Task 3)
- ✅ Scheduler wiring in `_build_kwargs()` (Task 5)
- ✅ `Database.update_pending_expiry()` method (Task 2)
- ✅ `_process_verify_result()` receives and uses `fitgirl_pending_days` (Task 4)
- ✅ `_verify_pending_scores()` receives and passes through `fitgirl_pending_days` (Task 4)
- ✅ Call site in `_run_discovery_phases()` passes `cfg.fitgirl_pending_days` (Task 4)
- ✅ Config YAML updated (Task 1)
- ✅ Database test: `test_update_pending_expiry` (Task 2)
- ✅ Pipeline tests: 3 tests for expiry update (Task 6)
- ✅ Non-goals respected: no changes to `metacritic.platform_overrides.pc.pending_days`, no retroactive updates

### Placeholder scan
- No "TBD", "TODO", "implement later", or "fill in details"
- No "add appropriate error handling" without specifics
- No "write tests for the above" without test code
- No "similar to Task N" without repeating code
- All steps have complete code shown
- All file paths exact

### Type consistency
- `fitgirl_pending_days: int = 60` used consistently across `AcquisitionConfig`, `run_acquisition()`, `_verify_pending_scores()`, and `_process_verify_result()`
- `pending_days: int = 60` on `FitGirlSourceConfig` is the config source
- `config.sources.fitgirl.pending_days` matches the scheduler wiring
- `update_pending_expiry(slug: str, pending_days: int)` matches all call sites
