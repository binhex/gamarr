# Age Recheck Weeks v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three issues in the age_recheck_weeks feature: rename "seal" → "process", replace per-verify-batch age check with a standalone sweep over ALL pending games, and fix log levels.

**Architecture:** Remove the age check from `_process_verify_result` (which only ran within the `max_verify`-limited batch). Add a new `_process_aged_games()` function that scans ALL pending games with `last_checked_at` set, checks `release_date` against `age_recheck_weeks`, and marks them as "Processed". Rename all "seal" terminology to "process".

**Tech Stack:** Python 3.12, SQLAlchemy

---

### Task 1: Rename functions + remove age check from verification path

**Files:**
- Modify: `src/gamarr/pipeline.py:754-792` — rename two functions, update docstrings, remove `logger.info`, change to `logger.debug`
- Modify: `src/gamarr/pipeline.py:860-865` — remove the age-check call from `_process_verify_result`
- Modify: `src/gamarr/pipeline.py:804` — remove `age_recheck_weeks` param from `_process_verify_result`
- Modify: `src/gamarr/pipeline.py:896,963,1048` — remove `age_recheck_weeks` from intermediate callers
- Modify: `src/gamarr/pipeline.py:347` — remove `age_recheck_weeks` from `_verify_pending_scores` call
- Modify: `tests/unit/test_pipeline.py` — rename test class and test methods

- [ ] **Step 1: Rename `_should_seal_by_age` to `_should_process_by_age`**

Replace the function definition and its docstring:

```python
def _should_process_by_age(game: Any, age_recheck_weeks: int | None) -> bool:
    """Return True if *game* is old enough to be permanently processed.

    When *age_recheck_weeks* is ``None`` or ``0``, processing is disabled.
    Games without a ``release_date`` are never processed (we can't determine
    their age).
    """
    if age_recheck_weeks is None:
        return False
    release_date = getattr(game, "release_date", None)
    if not release_date:
        return False
    return _is_older_than(release_date, days=age_recheck_weeks * 7)
```

- [ ] **Step 2: Rename `_seal_game` to `_record_processed_game`**

Replace the function definition. Change `result_details` to remove duplicate "age_recheck_weeks" and change `logger.info` to `logger.debug`:

```python
def _record_processed_game(db: Database, game: Any, result: Any, age_recheck_weeks: int) -> bool:
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
        result_details=f"Game older than {age_recheck_weeks}-week threshold, not re-checked",
    )
    db.remove_pending(str(game.slug))
    logger.debug(
        "Processed '{}' \u2014 release date older than {} weeks",
        game.game_title,
        age_recheck_weeks,
    )
    return True
```

- [ ] **Step 3: Remove age check from `_process_verify_result`**

Delete these lines from `_process_verify_result` (around lines 860-865):

```python
    # Seal old games instead of keeping them in the pending queue
    if _should_seal_by_age(game, age_recheck_weeks):
        assert age_recheck_weeks is not None  # guarded by _should_seal_by_age above
        return _seal_game(db, game, result, age_recheck_weeks)
```

Also remove the `age_recheck_weeks: int | None = None` parameter from `_process_verify_result` signature (line 804).

- [ ] **Step 4: Remove `age_recheck_weeks` from intermediate callers**

Remove `age_recheck_weeks=age_recheck_weeks` from:
- `_verify_pending_scores` signature (line 986)
- `_verify_pending_scores` → `_process_verify_batch` call (line 1051)
- `_process_verify_batch` signature (line 899)
- `_process_verify_batch` → `_process_verify_result` call (line 966)
- `run_acquisition` → `_verify_pending_scores` call (line 347)

- [ ] **Step 5: Rename test class and methods**

In `tests/unit/test_pipeline.py`:

```python
class TestProcessByAge:
    """Tests for _should_process_by_age helper."""

    def test_process_by_age_returns_true_for_old_game(self) -> None:
        """A game older than age_recheck_weeks should be processed."""
        from gamarr.pipeline import _should_process_by_age

        class FakeGame:
            release_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

        result = _should_process_by_age(FakeGame(), age_recheck_weeks=52)
        assert result is True

    def test_process_by_age_returns_false_for_recent_game(self) -> None:
        """A game newer than age_recheck_weeks should NOT be processed."""
        from gamarr.pipeline import _should_process_by_age

        class FakeGame:
            release_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        result = _should_process_by_age(FakeGame(), age_recheck_weeks=52)
        assert result is False

    def test_process_by_age_returns_false_when_disabled(self) -> None:
        """age_recheck_weeks=None or 0 should disable processing."""
        from gamarr.pipeline import _should_process_by_age

        class FakeGame:
            release_date = "2020-01-01"

        assert _should_process_by_age(FakeGame(), age_recheck_weeks=None) is False
        assert _should_process_by_age(FakeGame(), age_recheck_weeks=0) is False

    def test_process_by_age_returns_false_when_no_release_date(self) -> None:
        """A game with no release_date should NOT be processed."""
        from gamarr.pipeline import _should_process_by_age

        class FakeGame:
            release_date = None

        result = _should_process_by_age(FakeGame(), age_recheck_weeks=52)
        assert result is False

    def test_record_processed_game_records_and_removes(self, tmp_path: Path) -> None:
        """_record_processed_game should record as Processed and remove from pending."""
        import datetime
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="old-game", game_title="Old Game", platform="pc",
            metascore=85.0, user_score=8.0, release_date="2020-01-01", expires_at=expires,
        )
        db.update_pending_scores(slug="old-game", metascore=85.0, user_score=8.0)
        from gamarr.pipeline import _record_processed_game
        import types

        game = db.get_pending(platform="pc")[0]
        result = types.SimpleNamespace(metascore=85.0, metascore_review_count=20, user_score=8.0, user_review_count=100)
        processed = _record_processed_game(db, game, result, age_recheck_weeks=52)
        assert processed is True
        assert not db.is_pending("old-game")
        stats = db.get_stats()
        assert stats["total"] == 1
        db.close()
```

Also delete the two integration tests that tested the old path:
- `test_process_verify_result_seals_old_game`
- `test_process_verify_result_keeps_recent_game`

These tests verified behaviour that no longer exists (age check inside `_process_verify_result`). They will be replaced by new tests in Task 2.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest -x -q --tb=short`
Expected: All tests pass (test count drops by 2 since we removed the old integration tests, plus the rename may adjust it)

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "refactor: rename seal->process, remove age check from verify path"
```

---

### Task 2: Add `_process_aged_games()` sweep function

**Files:**
- Modify: `src/gamarr/pipeline.py` — add new function + wire into `_run_discovery_phases`
- Modify: `src/gamarr/pipeline.py:343-362` — add call after `_verify_pending_scores`
- Test: `tests/unit/test_pipeline.py` — add sweep integration tests

- [ ] **Step 1: Write the failing test for `_process_aged_games`**

Add to `tests/unit/test_pipeline.py`:

```python
class TestProcessAgedGames:
    """Tests for _process_aged_games sweep function."""

    def test_process_aged_games_processes_old_verified_games(self, tmp_path: Path) -> None:
        """Old games with last_checked_at set should be processed."""
        import datetime
        from gamarr.database import Database
        from gamarr.pipeline import _process_aged_games, AcquisitionConfig

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
        # Old game that HAS been checked
        db.record_pending(
            slug="old-checked", game_title="Old Checked", platform="pc",
            metascore=80.0, user_score=7.5, release_date="2010-01-01", expires_at=expires,
        )
        # Direct SQL to set last_checked_at for the old game
        with db._session() as session:
            from gamarr.database import PendingGame
            row = session.get(PendingGame, "old-checked")
            row.last_checked_at = past
            session.commit()

        # Old game that has NOT been checked yet (last_checked_at is None)
        db.record_pending(
            slug="old-unchecked", game_title="Old Unchecked", platform="pc",
            metascore=80.0, user_score=7.5, release_date="2010-01-01", expires_at=expires,
        )

        cfg = AcquisitionConfig(
            min_metascore=75, min_metascore_reviews=5, min_user_score=7.5, min_user_reviews=10,
            age_recheck_weeks=52,
        )
        count = _process_aged_games(db, cfg, platform="pc")
        assert count == 1, f"Expected 1 processed, got {count}"
        assert not db.is_pending("old-checked"), "Old checked game should be removed"
        assert db.is_pending("old-unchecked"), "Old unchecked game should remain"
        stats = db.get_stats()
        assert stats["total"] == 1, "One history record should exist"
        db.close()

    def test_process_aged_games_skips_recent_games(self, tmp_path: Path) -> None:
        """Recent games should NOT be processed by the sweep."""
        import datetime
        from gamarr.database import Database
        from gamarr.pipeline import _process_aged_games, AcquisitionConfig

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        recent = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
        db.record_pending(
            slug="recent-game", game_title="Recent Game", platform="pc",
            metascore=80.0, user_score=7.5, release_date=recent, expires_at=expires,
        )
        with db._session() as session:
            from gamarr.database import PendingGame
            row = session.get(PendingGame, "recent-game")
            row.last_checked_at = past
            session.commit()

        cfg = AcquisitionConfig(
            min_metascore=75, min_metascore_reviews=5, min_user_score=7.5, min_user_reviews=10,
            age_recheck_weeks=52,
        )
        count = _process_aged_games(db, cfg, platform="pc")
        assert count == 0, "Recent game should not be processed"
        assert db.is_pending("recent-game"), "Recent game should remain"
        db.close()

    def test_process_aged_games_disabled_when_none(self, tmp_path: Path) -> None:
        """When age_recheck_weeks is None, no games should be processed."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_aged_games, AcquisitionConfig

        db = Database(str(tmp_path / "test.db"))
        cfg = AcquisitionConfig(
            min_metascore=75, min_metascore_reviews=5, min_user_score=7.5, min_user_reviews=10,
            age_recheck_weeks=None,
        )
        count = _process_aged_games(db, cfg, platform="pc")
        assert count == 0
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pipeline.py::TestProcessAgedGames -x -v --tb=short`
Expected: FAIL with `Function not defined: _process_aged_games`

- [ ] **Step 3: Add `_process_aged_games` function**

Add this function in `src/gamarr/pipeline.py` (place it after `_record_processed_game`, around line 792):

```python
def _process_aged_games(
    db: Database,
    cfg: AcquisitionConfig,
    platform: str,
) -> int:
    """Mark old verified pending games as processed.

    Queries all non-expired pending games that have been checked at
    least once (``last_checked_at IS NOT NULL``) and whose
    ``release_date`` is older than ``cfg.age_recheck_weeks``.

    These games are permanently recorded with ``result="Processed"``
    and removed from the pending queue — they will not be re-checked
    on future cycles.

    Returns the count of games processed.
    """
    if not cfg.age_recheck_weeks:
        return 0

    pending = db.get_pending(platform=platform)
    processed = 0
    for game in pending:
        if game.last_checked_at is None:
            continue  # never verified — give it a chance first
        if not game.release_date:
            continue  # can't determine age
        if not _is_older_than(game.release_date, days=cfg.age_recheck_weeks * 7):
            continue  # still recent enough to re-check

        db.record_processed(
            source="metacritic",
            source_title=str(game.game_title),
            source_url=f"mc:{game.slug}",
            game_title=str(game.game_title),
            platform=str(game.platform),
            metascore=game.metascore,
            user_score=game.user_score,
            result="Processed",
            result_details=f"Game older than {cfg.age_recheck_weeks}-week threshold, not re-checked",
        )
        db.remove_pending(str(game.slug))
        logger.debug(
            "Processed '{}' \u2014 release date older than {} weeks",
            game.game_title,
            cfg.age_recheck_weeks,
        )
        processed += 1

    if processed:
        logger.info(
            "Processed {} old game(s) \u2014 will not be re-checked",
            processed,
        )
    return processed
```

- [ ] **Step 4: Wire `_process_aged_games` into `_run_discovery_phases`**

Add the call immediately after the `_verify_pending_scores` block and its log message, and before the `library_paths` block. In `pipeline.py` around line 363:

```python
            if removed:
                logger.info(
                    "Removed {} games from queue — rejected by genre, title, or not found on Metacritic",
                    removed,
                )

        # NEW: Process old verified games so they aren't re-checked
        if not is_cancelled(cancel_event):
            aged_removed = _process_aged_games(db, cfg, platform)
            if aged_removed:
                logger.info(
                    "Removed {} games from queue — rejected by genre, title, or not found on Metacritic",
                    removed,
                )
                # The removed count already includes aged games from the verify
                # pass, so this is additive to any existing removed count
```

Wait, the `removed` variable is local to the `if pending_games:` block above. Let me adjust — it should be a standalone call after verification:

```python
        # Process old verified games so they aren't re-checked
        _process_aged_games(db, cfg, platform)
```

(It logs its own info message if any games are processed.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_pipeline.py::TestProcessAgedGames -x -v --tb=short`
Expected: All 3 tests PASS

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest -x -q --tb=short`
Expected: All tests pass

- [ ] **Step 7: Run linters**

Run: `uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 8: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: add _process_aged_games sweep for old verified games"
```

---

### Task 3: Clean up — remove stale params from callers

**Files:**
- Modify: `src/gamarr/scheduler.py:122` — remove `age_recheck_weeks` from `_build_kwargs`
- Modify: `src/gamarr/pipeline.py` — remove `age_recheck_weeks` from `run_acquisition` signature and `AcquisitionConfig`

Wait — we still need `age_recheck_weeks` in `AcquisitionConfig` and the call chain because `_process_aged_games` reads it from `cfg`. So the config field, the `AcquisitionConfig` field, the `run_acquisition` parameter, and `_build_kwargs` all still need to pass it through. The only parameters we removed were from `_process_verify_result` and its intermediate callers.

So the remaining work is just to update the README if needed.

**Files:**
- No changes needed — config field + AcquisitionConfig + run_acquisition + _build_kwargs are all still wired

- [ ] **Step 1: Verify `age_recheck_weeks` still flows through correctly**

Run: `uv run pytest -x -q --tb=short && uv run mypy .`
Expected: All clean

- [ ] **Step 2: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "fix: final cleanup after age_recheck_weeks v2 redesign"
```
