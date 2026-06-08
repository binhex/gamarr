# reject_genre Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `reject_genre` filtering to Metacritic browsing — games matching any rejected genre are immediately removed from the pending queue.

**Architecture:** A `reject_genre: list[str]` field added to the config model, threaded through `AcquisitionConfig` → scheduler → `run_acquisition()` → `_verify_pending_scores()` → `_process_verify_result()`. The genre check runs after the one required Metacritic detail-page HTTP lookup (which provides `ScoreResult.genres`) and before score evaluation. Matches are removed via the existing `_fail_game_after_max_attempts` helper.

**Tech Stack:** Python 3.12+, Pydantic config model, sqlite3 (database), ThreadPoolExecutor (concurrent lookups)

---

### Task 1: Config model (field + migration)

**Files:**
- Modify: `src/gamarr/config.py:59` — add `reject_genre` field to `MetacriticPlatformConfig`
- Test: No migration needed — this is a new field with an empty default

- [ ] **Step 1: Add the field to `MetacriticPlatformConfig`**

Add after `exclude_keywords` on line 48:

```python
class MetacriticPlatformConfig(BaseModel):
    """Metacritic scoring thresholds for a single platform."""

    min_metascore: int = 75
    min_metascore_reviews: int = 5
    min_user_score: float = 7.5
    min_user_reviews: int = 10
    days_since_release: int = 90
    cache_ttl_days: int = 7
    cache_ttl_hours: int = 4
    pending_days: int = 30
    enabled: bool = True
    max_games: int = Field(default=1000, ge=0, le=20000)
    max_verify_attempts: int = Field(default=6, ge=0)
    cutoff_weeks: int | None = None
    exclude_keywords: list[str] = Field(default_factory=list)
    reject_genre: list[str] = Field(default_factory=list)  # ← add here
```

- [ ] **Step 2: Run config tests to verify field is accepted**

Run: `uv run pytest tests/unit/test_config.py -v -x`
Expected: All pass. The Pydantic model will accept `reject_genre` from YAML with the default factory.

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/config.py
git commit -m "feat: add reject_genre field to MetacriticPlatformConfig"
```

---

### Task 2: Pipeline model + run_acquisition parameter

**Files:**
- Modify: `src/gamarr/pipeline.py:111` — `AcquisitionConfig` dataclass
- Modify: `src/gamarr/pipeline.py:175-212` — `run_acquisition()` signature + construction

- [ ] **Step 1: Add `reject_genre` to `AcquisitionConfig`**

Add after `exclude_keywords` on line 111:

```python
reject_genre: list[str] | None = None
```

The dataclass now reads:

```python
@dataclass
class AcquisitionConfig:
    min_metascore: int = 75
    min_metascore_reviews: int = 5
    min_user_score: float = 7.5
    min_user_reviews: int = 10
    days_since_release: int = 90
    cache_ttl_days: int = 7
    cache_ttl_hours: int = 4
    enabled: bool = True
    pending_days: int = 30
    max_games: int = 1000
    max_verify_attempts: int = 6
    cutoff_weeks: int | None = None
    exclude_keywords: list[str] | None = None
    reject_genre: list[str] | None = None  # ← add here
```

- [ ] **Step 2: Add `reject_genre` to `run_acquisition()` signature**

Add after `exclude_keywords` on line 182:

```python
reject_genre: list[str] | None = None,
```

- [ ] **Step 3: Add to `AcquisitionConfig` construction in `run_acquisition()`**

Inside `run_acquisition()` around line 206, add:

```python
reject_genre=reject_genre,
```

- [ ] **Step 4: Run pipeline tests to verify no regression**

Run: `uv run pytest tests/unit/test_pipeline.py -v -x --timeout=60`
Expected: All pass (new field defaults to None — existing behaviour unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: add reject_genre to AcquisitionConfig and run_acquisition()"
```

---

### Task 3: Scheduler wiring

**Files:**
- Modify: `src/gamarr/scheduler.py:61-68` — add kwarg in `_build_kwargs()`

- [ ] **Step 1: Add `reject_genre` to `_build_kwargs()` return dict**

After `"exclude_keywords": mc_cfg.exclude_keywords,` (line 61):

```python
"reject_genre": mc_cfg.reject_genre,
```

The full return dict section now reads:

```python
return {
    ...
    "cutoff_weeks": mc_cfg.cutoff_weeks,
    "exclude_keywords": mc_cfg.exclude_keywords,
    "reject_genre": mc_cfg.reject_genre,     # ← add here
    "pending_days": mc_cfg.pending_days,
    ...
}
```

- [ ] **Step 2: Run scheduler tests**

Run: `uv run pytest tests/unit/test_scheduler.py -v -x --timeout=30`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/scheduler.py
git commit -m "feat: wire reject_genre through scheduler kwargs"
```

---

### Task 4: Config file + filtering logic in pipeline

**Files:**
- Modify: `configs/gamarr.yml` — add `reject_genre: []`
- Modify: `src/gamarr/pipeline.py:313-325` — pass `reject_genre` to `_verify_pending_scores()`
- Modify: `src/gamarr/pipeline.py:689-693` — add `reject_genre` param to `_verify_pending_scores()`
- Modify: `src/gamarr/pipeline.py:764` — pass `reject_genre` to `_process_verify_result()`
- Modify: `src/gamarr/pipeline.py:634-638` — add `reject_genre` param to `_process_verify_result()`
- Modify: `src/gamarr/pipeline.py:642-656` — insert genre check before the `result is None` check

- [ ] **Step 1: Add `reject_genre` to config yml**

In `configs/gamarr.yml`, add under `metacritic.platform_overrides.pc` (after `exclude_keywords`):

```yaml
      reject_genre: []
```

- [ ] **Step 2: Add `reject_genre` param to `_verify_pending_scores()`**

Update signature at line 689:

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
    reject_genre: list[str] | None = None,   # ← new param
) -> int:
```

- [ ] **Step 3: Thread `reject_genre` from `_verify_pending_scores` into `_process_verify_result`**

In the processing loop at line 764, change:

```python
# Before:
if _process_verify_result(db, game, result, thresholds, max_verify_attempts=max_verify_attempts):
    removed += 1

# After:
if _process_verify_result(
    db, game, result, thresholds,
    max_verify_attempts=max_verify_attempts,
    reject_genre=reject_genre,
):
    removed += 1
```

- [ ] **Step 4: Add `reject_genre` param to `_process_verify_result()`**

Update signature at line 634:

```python
def _process_verify_result(
    db: Database,
    game: Any,
    result: Any,
    thresholds: dict[str, Any],
    *,
    max_verify_attempts: int = 6,
    reject_genre: list[str] | None = None,   # ← new param
) -> bool:
```

- [ ] **Step 5: Insert genre rejection check at the top of `_process_verify_result`**

Insert **before** the `if result is None:` block (line 642):

```python
    # Check if the game's genre matches a rejected genre (case-insensitive exact match)
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

    if result is None:
        ...
```

- [ ] **Step 6: Pass `reject_genre` from the call site in `_run_discovery_phases`**

In `_run_discovery_phases()` around line 313, add the kwarg:

```python
removed = _verify_pending_scores(
    db,
    mc,
    platform,
    thresholds,
    cache_ttl_days=cfg.cache_ttl_days,
    max_verify=len(pending_games) if cfg.max_games == 0 else min(len(pending_games), cfg.max_games),
    max_verify_attempts=cfg.max_verify_attempts,
    reject_genre=cfg.reject_genre,  # ← add this
)
```

- [ ] **Step 7: Run pipeline tests**

Run: `uv run pytest tests/unit/test_pipeline.py -v -x --timeout=60`
Expected: All existing tests pass

- [ ] **Step 8: Commit**

```bash
git add src/gamarr/pipeline.py configs/gamarr.yml
git commit -m "feat: add reject_genre filtering in _process_verify_result"
```

---

### Task 5: Unit tests for `_process_verify_result` genre rejection

**Files:**
- Modify: `tests/unit/test_pipeline.py` — add tests after the existing `_process_verify_result` / `_verify_pending_scores` test block

**Find the insertion point:** After the last `test_verify_pending_*` test (around line 2560). Look for the next class or test function.

- [ ] **Step 1: Write `test_reject_genre_matches`**

Insert after the last `test_verify_pending_max_attempts_zero_removes_immediately` test:

```python
    def test_reject_genre_matches(self, tmp_path: Path) -> None:
        """Game with a genre in reject_genre should be removed immediately."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=95.0,
            metascore_reviews=100,
            user_score=9.0,
            user_reviews=500,
            release_date="2022-02-25",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=95.0,
            metascore_review_count=100,
            user_score=9.0,
            user_review_count=500,
            genres=["Action", "RPG"],
            must_play=True,
            release_date="2022-02-25",
            slug="elden-ring",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        assert db.is_pending("elden-ring") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["rpg"])
        assert removed == 1, "Game with rejected genre should be removed"
        assert db.is_pending("elden-ring") is False, "Game should no longer be pending"
        db.close()
```

- [ ] **Step 2: Write `test_reject_genre_no_match`**

```python
    def test_reject_genre_no_match(self, tmp_path: Path) -> None:
        """Game without a rejected genre should be processed normally."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="forza-horizon-6",
            game_title="Forza Horizon 6",
            platform="pc",
            metascore=1985.0,
            metascore_reviews=1986,
            user_score=1994.0,
            user_reviews=None,
            release_date="2026-05-19",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Racing"],
            must_play=True,
            release_date="2026-05-19",
            slug="forza-horizon-6",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["action"])
        assert removed == 0, "Game genre 'Racing' not in reject_genre ['action'] — should NOT be removed"
        assert db.is_pending("forza-horizon-6") is True, "Game should remain pending"
        db.close()
```

- [ ] **Step 3: Write `test_reject_genre_empty_list`**

```python
    def test_reject_genre_empty_list(self, tmp_path: Path) -> None:
        """Empty reject_genre list should have no effect."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=95.0,
            metascore_reviews=100,
            user_score=9.0,
            user_reviews=500,
            release_date="2022-02-25",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=95.0,
            metascore_review_count=100,
            user_score=9.0,
            user_review_count=500,
            genres=["Action", "RPG"],
            must_play=True,
            release_date="2022-02-25",
            slug="elden-ring",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=[])
        assert removed == 0, "Empty reject_genre — game should NOT be removed"
        assert db.is_pending("elden-ring") is True, "Game should remain pending"
        db.close()
```

- [ ] **Step 4: Write `test_reject_genre_multi_match`**

```python
    def test_reject_genre_multi_match(self, tmp_path: Path) -> None:
        """Game with multiple genres where one matches reject_genre should be removed."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="cyberpunk-2077",
            game_title="Cyberpunk 2077",
            platform="pc",
            metascore=86.0,
            metascore_reviews=90,
            user_score=7.4,
            user_reviews=500,
            release_date="2020-12-10",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=86.0,
            metascore_review_count=90,
            user_score=7.4,
            user_review_count=500,
            genres=["Action", "RPG", "Open-World"],
            must_play=True,
            release_date="2020-12-10",
            slug="cyberpunk-2077",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["rpg", "sports"])
        assert removed == 1, "Game has 'RPG' which is in reject_genre — should be removed"
        assert db.is_pending("cyberpunk-2077") is False
        db.close()
```

- [ ] **Step 5: Write `test_reject_genre_case_insensitive`**

```python
    def test_reject_genre_case_insensitive(self, tmp_path: Path) -> None:
        """Genre matching should be case-insensitive."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="hades-2",
            game_title="Hades II",
            platform="pc",
            metascore=90.0,
            metascore_reviews=80,
            user_score=8.5,
            user_reviews=300,
            release_date="2025-05-06",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=80,
            user_score=8.5,
            user_review_count=300,
            genres=["Early Access", "Roguelike"],
            must_play=False,
            release_date="2025-05-06",
            slug="hades-2",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        # User config says "ROGUELIKE" (uppercase), genre is "Roguelike" (title case)
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["ROGUELIKE"])
        assert removed == 1, "Case-insensitive match — 'ROGUELIKE' should match 'Roguelike'"
        db.close()
```

- [ ] **Step 6: Write `test_reject_genre_result_none`**

```python
    def test_reject_genre_result_none(self, tmp_path: Path) -> None:
        """When lookup returns None, genre check is skipped and normal retry logic applies."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="unknown-game",
            game_title="Unknown Game",
            platform="pc",
            metascore=0.0,
            metascore_reviews=0,
            user_score=0.0,
            user_reviews=0,
            release_date="2026-01-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None  # lookup failed

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["action"])
        assert removed == 0, "Lookup returned None — genre check skipped, game stays for re-check"
        assert db.is_pending("unknown-game") is True, "Game should remain pending for re-try"
        db.close()
```

- [ ] **Step 7: Run all pipeline tests**

Run: `uv run pytest tests/unit/test_pipeline.py -v -x --timeout=60`
Expected: All 6 new tests pass + all existing tests pass

- [ ] **Step 8: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add reject_genre unit tests for _verify_pending_scores"
```

---

### Task 6: Full QC verification

**Files:** None (verification only)

- [ ] **Step 1: Ruff lint + format**

Run: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: Clean (no lint errors, no formatting changes needed)

- [ ] **Step 2: Mypy type check**

Run: `uv run mypy src/gamarr/`
Expected: Clean (no type errors)

- [ ] **Step 3: Full test suite**

Run: `uv run pytest --no-cov -v --timeout=120`
Expected: All tests pass (current baseline: ~297 tests)

- [ ] **Step 4: Pre-commit**

Run: `pre-commit run --all-files 2>&1 | tail -20` (if `.pre-commit-config.yaml` exists)
Expected: Clean

- [ ] **Step 5: Final commit (if any fixes made)**

```bash
git add -A
git commit -m "chore: lint and type fixes for reject_genre"
```
