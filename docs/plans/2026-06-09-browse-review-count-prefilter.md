# Browse-page Critic Review Count Pre-filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Metacritic detail-page HTTP requests by pre-filtering games at browse time when their critic/user review counts are already below threshold.

**Architecture:** Add a new function `_reject_by_browse_review_counts()` that uses browse-page `critic_review_count` and `user_review_count` fields (real counts, zero HTTP cost) to reject games before they enter the pending queue. Wire it into the existing `_process_browse_games()` loop. Remove the now-redundant review count checks from `_game_passes_thresholds()`.

**Tech Stack:** Python, pytest, existing test infrastructure

---

### Task 1: Add `_reject_by_browse_review_counts` function

**Files:**
- Add: `src/gamarr/pipeline.py` — new function after `_cancel_remaining_futures` (~line 405)
- Test: `tests/unit/test_pipeline.py` — new class `TestBrowseReviewCountPrefilter` after the `TestCancellation` class

- [ ] **Step 1: Write the four failing unit tests**

Add this block after the `TestCancellation` class in `tests/unit/test_pipeline.py`:

```python
class TestBrowseReviewCountPrefilter:
    """_reject_by_browse_review_counts function."""

    def test_returns_none_when_both_counts_missing(self) -> None:
        """When both critic_review_count and user_review_count are None
        (not available in browse data), the function should return None
        so the game proceeds to detail-page verification as before."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Test Game",
            "slug": "test-game",
            "critic_review_count": None,
            "user_review_count": None,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result is None

    def test_returns_none_when_counts_sufficient(self) -> None:
        """When both counts meet or exceed thresholds, return None."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Popular Game",
            "slug": "popular-game",
            "critic_review_count": 20,
            "user_review_count": 50,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result is None

    def test_returns_reason_when_critic_count_too_low(self) -> None:
        """When critic_review_count is below min_critic_reviews, return the reason string."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Obscure Game",
            "slug": "obscure-game",
            "critic_review_count": 2,
            "user_review_count": 0,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result == "critic_reviews_too_few_at_browse"

    def test_returns_reason_when_user_count_too_low(self) -> None:
        """When user_review_count is below min_user_reviews (and critic count is fine), return reason."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Unreviewed Game",
            "slug": "unreviewed-game",
            "critic_review_count": 20,
            "user_review_count": 3,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result == "user_reviews_too_few_at_browse"

    def test_ignores_zero_threshold(self) -> None:
        """When min thresholds are 0 (disabled), function should never reject."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Zero Reviews Game",
            "slug": "zero-review-game",
            "critic_review_count": 0,
            "user_review_count": 0,
        }
        # 0 < 0 is False, so neither condition triggers
        result = _reject_by_browse_review_counts(game, min_critic_reviews=0, min_user_reviews=0)
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py::TestBrowseReviewCountPrefilter -xvs`
Expected: `FAILED` with `ImportError: cannot import name '_reject_by_browse_review_counts'`

- [ ] **Step 3: Implement `_reject_by_browse_review_counts` in pipeline.py**

Add after `_cancel_remaining_futures` (around line 405) in `src/gamarr/pipeline.py`:

```python
def _reject_by_browse_review_counts(
    game: dict[str, Any],
    min_critic_reviews: int,
    min_user_reviews: int,
) -> str | None:
    """Return a rejection reason string if browse-page review counts
    are available and below threshold, or None if the game should proceed.

    Only uses browse-page fields that are real counts (not scaled browse
    metrics).  When a count is None (missing from browse data), the check
    is skipped and the game proceeds to the detail-page verify phase.

    Args:
        game: A browse-page game dict from ``_parse_browse_page``.
        min_critic_reviews: Minimum critic reviews threshold (``min_metascore_reviews``).
        min_user_reviews: Minimum user reviews threshold (``min_user_reviews``).

    Returns:
        ``"critic_reviews_too_few_at_browse"``, ``"user_reviews_too_few_at_browse"``,
        or ``None`` if the game should proceed.
    """
    critic_count = game.get("critic_review_count")
    if critic_count is not None and critic_count < min_critic_reviews:
        return "critic_reviews_too_few_at_browse"
    user_count = game.get("user_review_count")
    if user_count is not None and user_count < min_user_reviews:
        return "user_reviews_too_few_at_browse"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_pipeline.py::TestBrowseReviewCountPrefilter -xvs`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat(pipeline): add _reject_by_browse_review_counts pre-filter

Extract browse-page review count checking into its own function.
Returns a reason string when critic or user review counts are
available and below threshold, None otherwise. Zero HTTP cost
because it only examines already-fetched browse page data."
```

---

### Task 2: Wire pre-filter into `_process_browse_games`

**Files:**
- Modify: `src/gamarr/pipeline.py` — add the pre-filter call and logging in `_process_browse_games`
- Test: `tests/unit/test_pipeline.py` — add integration tests in `TestBrowseReviewCountPrefilter`

- [ ] **Step 1: Write integration tests**

Add these two tests inside `TestBrowseReviewCountPrefilter` in `tests/unit/test_pipeline.py`:

```python
    def test_process_browse_games_skips_low_review_count_games(self, tmp_path: Path) -> None:
        """Games with browse-page critic_review_count below threshold
        should NOT be added to the pending queue by _process_browse_games."""
        import threading
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Low Reviews",
                "slug": "low-reviews",
                "score": 1478.0,
                "critic_review_count": 2,   # below threshold 5
                "user_rating": 2007.0,
                "user_review_count": 50,
                "release_date": "2026-06-01",
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        new_count = _process_browse_games(
            browse_games,
            platform="pc",
            db=db,
            thresholds=thresholds,
            pending_days=30,
        )
        assert new_count == 0, "Low-review-count game should not be added to pending"
        pending = db.get_pending(platform="pc")
        assert len(pending) == 0
        db.close()

    def test_process_browse_games_passes_when_review_counts_unavailable(self, tmp_path: Path) -> None:
        """Games with None review counts on the browse page should still
        enter the pending queue (fallback to detail-page verification)."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "No Review Data",
                "slug": "no-review-data",
                "score": 1478.0,
                "critic_review_count": None,   # missing from browse data
                "user_rating": 2007.0,
                "user_review_count": None,
                "release_date": "2026-06-01",
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        new_count = _process_browse_games(
            browse_games,
            platform="pc",
            db=db,
            thresholds=thresholds,
            pending_days=30,
        )
        assert new_count == 1, "Game with missing review data should enter pending"
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        assert pending[0].slug == "no-review-data"
        db.close()
```

- [ ] **Step 2: Run the integration tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py::TestBrowseReviewCountPrefilter::test_process_browse_games_skips_low_review_count_games tests/unit/test_pipeline.py::TestBrowseReviewCountPrefilter::test_process_browse_games_passes_when_review_counts_unavailable -xvs`
Expected: The first test FAILS — the game IS added to pending because the pre-filter isn't wired yet. The second test passes (no regression on None counts).

- [ ] **Step 3: Wire the pre-filter into `_process_browse_games`**

In `src/gamarr/pipeline.py`, locate the `_process_browse_games` function. In the for-each-game loop, add the pre-filter check right after `_title_matches_reject` and before the pending-days/call to `db.record_pending`:

```python
        if _title_matches_reject(game.get("title", ""), reject_title):
            logger.debug("Skipping '{}' — matches reject_title", game.get("title", ""))
            continue

        # NEW: browse-page review count pre-filter
        reject_reason = _reject_by_browse_review_counts(
            game,
            min_critic_reviews=thresholds.get("min_metascore_reviews", 0),
            min_user_reviews=thresholds.get("min_user_reviews", 0),
        )
        if reject_reason is not None:
            logger.debug(
                "Skipping '{}' — {}",
                game.get("title", ""),
                reject_reason,
            )
            continue

        g_slug = game.get("slug", "")
```

- [ ] **Step 4: Add threshold extraction from cfg to the call at `_run_discovery_phases`**

Locate the call to `_process_browse_games` in `_run_discovery_phases` (~line 295). The `thresholds` dict is built just above it with `min_metascore_reviews` and `min_user_reviews` keys — verify these are present. The existing code already includes them:

```python
thresholds = {
    "min_metascore": cfg.min_metascore,
    "min_metascore_reviews": cfg.min_metascore_reviews,
    ...
}
```

This is already correct — no change needed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_pipeline.py::TestBrowseReviewCountPrefilter -xvs`
Expected: All 7 tests pass

- [ ] **Step 6: Run the existing acceptance test to verify no regression**

Run: `uv run pytest tests/unit/test_pipeline.py -x -q --no-header | tail -5`
Expected: All pipeline tests pass

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat(pipeline): wire browse review count pre-filter into _process_browse_games

Games whose browse-page critic or user review counts are below
configured thresholds are now skipped before entering the pending
queue, reducing unnecessary detail-page HTTP requests."
```

---

### Task 3: Remove redundant checks from `_game_passes_thresholds`

**Files:**
- Modify: `src/gamarr/pipeline.py` — remove critic_reviews and user_reviews lines from `_game_passes_thresholds`
- Test: `tests/unit/test_pipeline.py` — update existing tests that tested the removed checks

- [ ] **Step 1: Identify tests that need updating**

The following existing tests in `tests/unit/test_pipeline.py` directly test the review count checks inside `_game_passes_thresholds`:

- `test_browse_game_passes_thresholds_without_user_review_count` (line ~262) — tests that missing user_review_count allows pass
- `test_browse_game_passes_thresholds_without_critic_review_count` (line ~329) — tests that missing critic_review_count allows pass

After removing review count checks, `_game_passes_thresholds` only checks `score >= min_metascore` and `user_rating >= min_user_score`. The tests that verify the behavior of None review counts will now be redundant (the function no longer checks them). These tests should be moved to the new `TestBrowseReviewCountPrefilter` class (they're already covered by `test_returns_none_when_both_counts_missing`).

- [ ] **Step 2: Update `_game_passes_thresholds` in pipeline.py**

Remove the `critic_reviews` and `user_reviews` lines, and update the docstring:

```python
def _game_passes_thresholds(game: dict[str, Any], thresholds: dict[str, Any]) -> bool:
    """Check if a browse-page game dict passes all score thresholds.

    Note: browse-page ``score`` and ``user_rating`` fields are internal
    metrics on a different scale (e.g. 1478), not real 0-100 metascores
    or 0-10 user scores.  Real score verification happens during the
    detail-page phase.  Review count filtering is now handled by
    ``_reject_by_browse_review_counts``.
    """
    metascore = game.get("score")
    user_score = game.get("user_rating")
    if metascore is None or user_score is None:
        return False
    return all(
        [
            metascore >= thresholds["min_metascore"],
            user_score >= thresholds["min_user_score"],
        ]
    )
```

- [ ] **Step 3: Update the two affected existing tests**

Replace `test_browse_game_passes_thresholds_without_user_review_count` and `test_browse_game_passes_thresholds_without_critic_review_count` with new versions that no longer test review count behavior (those checks moved to the new pre-filter). The tests should now focus on what `_game_passes_thresholds` actually does after the removal.

In `tests/unit/test_pipeline.py`, find the `TestEvaluateScores` class and update the two tests:

```python
    def test_browse_game_passes_thresholds_with_high_browse_scores(self) -> None:
        """Browse-page games with inflated scores should pass the threshold check."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "Some Game",
            "slug": "some-game",
            "score": 1478.0,       # inflated browse metric
            "user_rating": 2007.0,  # inflated browse metric
        }
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is True

    def test_browse_game_fails_when_browse_score_missing(self) -> None:
        """A game with no browse scores should fail the threshold check."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "No Score Game",
            "slug": "no-score",
            "score": None,
            "user_rating": None,
        }
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is False
```

- [ ] **Step 4: Run the full test suite to verify**

Run: `uv run pytest tests/unit/test_pipeline.py -x -q --no-header | tail -5`
Expected: All pipeline tests pass (including the updated ones)

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "refactor(pipeline): remove review count checks from _game_passes_thresholds

Review count pre-filtering now lives in the dedicated
_reject_by_browse_review_counts function called from
_process_browse_games. The _game_passes_thresholds function
now only handles the unreliable browse-page score fields."
```

---

### Task 4: Final verification

**Files:** None — verification only

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/unit/ -x -q --no-header | tail -5`
Expected: All 367+ tests pass (coverage ≥ 95%)

- [ ] **Step 2: Run linters**

```bash
uv run ruff check src/gamarr/ tests/unit/
uv run ruff format --check src/gamarr/ tests/unit/
uv run mypy src/gamarr/ tests/unit/
```

Expected: All checks pass

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: finalize browse review count pre-filter implementation"
```
