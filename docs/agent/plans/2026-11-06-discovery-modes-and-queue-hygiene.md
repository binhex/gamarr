# Discovery Modes, Verify-Before-Queue, and Keyword Scope — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make gamarr's discovery predictable — an explicit `mode: latest | backlog`, queue only games that already pass detail verification, and match reject keywords against the FitGirl "Repack Features" block with whole-word/negation rules instead of a substring scan of the whole article.

**Architecture:** Four coupled changes, sequenced so the delivery blocker lands first: (1) keyword scope extractor, (2) whole-word/negation matcher wired into `_check_reject_keywords`, (3) one-time queue migration plus `record_pending(score_checks_passed=...)`, (4) verify-before-insert inside `_process_browse_games`, (5) `mode` config + pipeline plumbing, (6) the latest-mode branch in the existing year loop, (7) README. Discovery stays Metacritic-first; no new network surface beyond one detail lookup per new candidate.

**Tech Stack:** Python 3.12, pydantic v2, SQLAlchemy + SQLite, loguru, pytest, ruff, mypy, `uv`.

**Spec:** `docs/agent/specs/2026-11-06-discovery-modes-and-queue-hygiene-design.md` (approved).

**Commit policy:** when this plan runs inside the chain, do **not** commit between tasks — the finalising step owns commits. The commit steps below are for standalone execution.

---

## Spec refinements found while planning

1. **The "heading-like line" terminator cannot be implemented.** `_fetch_fitgirl_page_content` collapses whitespace in the extracted article, so there are no newlines to anchor a colon-terminated heading. The features block therefore ends at the first known section heading (`Screenshots`, `System Requirements`, `Backwards Compatibility`, `Game Description`, `Included DLCs`, `Download Mirrors`) or at 4000 characters, whichever comes first. The spec is amended in the same change.
2. **`record_pending` needs a `score_checks_passed` argument.** It currently never sets the column (rows are marked verified later by `update_pending_scores`). Verify-before-insert inserts an already-verified row in one call, so the argument is added with a default of `False` to keep every existing caller unchanged.

## Scope check

The spec covers **one** subsystem: the acquisition pipeline's discovery/queue/match path plus its config surface. All seven tasks touch that path or its documentation. **No plan split needed.**

## File map

| File | Responsibility after this change |
| ------ | ---------------------------------- |
| `src/gamarr/pipeline.py` | Keyword scope extractor + whole-word/negation matcher; verify-before-insert in `_process_browse_games`; `mode` resolution and the latest-mode branch; mode/inert-key logging |
| `src/gamarr/config.py` | `mode` field on `MetacriticPlatformConfig` |
| `src/gamarr/database.py` | `record_pending(score_checks_passed=...)`; one-time `pending_games` cleanup guarded by `PRAGMA user_version` |
| `src/gamarr/scheduler.py` | `_build_kwargs` forwards `mode` |
| `README.md` | `mode` row, per-mode key meanings, keyword-scope note, queue-migration note |
| `tests/unit/test_pipeline.py` | Keyword extractor/matcher, verify-before-insert, latest-mode matrix |
| `tests/unit/test_config.py` | `mode` default + validation |
| `tests/unit/test_database.py` | `record_pending` flag; queue migration idempotency |
| `tests/unit/test_scheduler.py` | `mode` forwarding |
| `tests/unit/test_readme_accuracy.py` | `mode` row matches the model default |
| `docs/agent/specs/2026-11-06-discovery-modes-and-queue-hygiene-design.md` | Refinement 1 (block terminator) |

**Unchanged:** `src/gamarr/metacritic.py` (the latest branch reuses the existing year loop, cutoff handling and detail cache).

---

### Task 1: FitGirl "Repack Features" scope extractor

**Files:**

- Modify: `src/gamarr/pipeline.py` (new helpers next to `_title_contains_keywords`, ~line 871)
- Test: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_pipeline.py`:

```python
class TestExtractRepackFeatures:
    """The keyword scan is scoped to the Repack Features block."""

    def test_returns_block_after_heading(self) -> None:
        from gamarr.pipeline import _extract_repack_features

        article = (
            "Company: Game Science Original Size: 140 GB Download Mirrors "
            "Only updated files for a new non-HV repack. "
            "Repack Features Based on P2P Steam BuildID 21393610 release: 139.7 GB "
            "Hypervisor Bypass (INTEL & AMD) by DenuvOwO added "
            "Screenshots Screenshot gallery text"
        )
        features = _extract_repack_features(article)
        assert features is not None
        assert "Hypervisor Bypass" in features
        assert "Only updated files" not in features, "download-mirror boilerplate must be excluded"
        assert "Screenshot gallery" not in features, "block must stop at the next heading"

    def test_missing_heading_returns_none(self) -> None:
        from gamarr.pipeline import _extract_repack_features

        assert _extract_repack_features("Company: Ubisoft Repack Size: 40 GB") is None
        assert _extract_repack_features(None) is None
        assert _extract_repack_features("") is None

    def test_block_is_capped(self) -> None:
        from gamarr.pipeline import _extract_repack_features

        article = "Repack Features " + ("word " * 2000)
        features = _extract_repack_features(article)
        assert features is not None
        assert len(features) <= 4000, "a page missing the next heading must not scan the whole article"

    def test_stops_at_backwards_compatibility_heading(self) -> None:
        from gamarr.pipeline import _extract_repack_features

        article = (
            "Repack Features Nothing ripped, 100% lossless "
            "Backwards Compatibility This repack works with the HV version"
        )
        features = _extract_repack_features(article)
        assert features is not None
        assert "lossless" in features
        assert "works with the HV version" not in features
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py -k TestExtractRepackFeatures -q`
Expected: FAIL — `ImportError: cannot import name '_extract_repack_features'`.

- [ ] **Step 3: Implement the extractor**

In `src/gamarr/pipeline.py`, immediately before `def _title_contains_keywords(` add:

```python
# Section headings that end the FitGirl "Repack Features" block.  Scoping the
# keyword scan this way is deliberate: the download-mirror boilerplate above the
# block mentions "non-HV repack" and "INSTALLED HV version" even on pages whose
# repack is not HV at all, and the backwards-compatibility prose below it refers
# to older releases.
_FEATURES_HEADING = "repack features"
_FEATURES_STOP_HEADINGS: tuple[str, ...] = (
    "screenshots",
    "system requirements",
    "backwards compatibility",
    "game description",
    "included dlcs",
    "download mirrors",
)
_FEATURES_MAX_CHARS = 4000


def _extract_repack_features(article_text: str | None) -> str | None:
    """Return the FitGirl "Repack Features" block, or None when it is absent.

    The block starts at the "Repack Features" heading and ends at the first known
    section heading, or after ``_FEATURES_MAX_CHARS`` characters when none of
    them appear — so a page without a following heading cannot silently scan the
    whole article.  Extracted article text has whitespace collapsed, so heading
    detection is a case-insensitive substring search.

    Args:
        article_text: The stripped ``<article>`` text, or ``None``.

    Returns:
        The feature block, or ``None`` when the page has no such heading.
    """
    if not article_text:
        return None
    lowered = article_text.lower()
    start = lowered.find(_FEATURES_HEADING)
    if start < 0:
        return None
    body = article_text[start:]
    body_lowered = body.lower()
    cut = len(body)
    for heading in _FEATURES_STOP_HEADINGS:
        index = body_lowered.find(heading, len(_FEATURES_HEADING))
        if 0 <= index < cut:
            cut = index
    return body[: min(cut, _FEATURES_MAX_CHARS)].strip()
```

No new import is needed: `re` is already imported and the module's constants use plain annotations (`from typing import TYPE_CHECKING, Any, Literal`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_pipeline.py -k TestExtractRepackFeatures -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: scope reject-keyword text to the FitGirl Repack Features block"
```

---

### Task 2: Whole-word, negation-aware keyword matcher

**Files:**

- Modify: `src/gamarr/pipeline.py` (`_title_contains_keywords` replaced; `_check_reject_keywords` rewired; `_truncate_at_backwards_compat` deleted)
- Test: `tests/unit/test_pipeline.py` (new matcher tests; the existing backwards-compat test is replaced)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_pipeline.py`:

```python
class TestKeywordMatch:
    """Whole-word matching with a negation guard."""

    def test_plain_mention_matches(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Hypervisor Bypass (INTEL & AMD) applied", ["hv", "hypervisor"]) == "hypervisor"

    def test_hv_as_standalone_word_matches(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Custom launcher added for easier HV steps processing", ["hv"]) == "hv"

    def test_negated_non_hv_does_not_match(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Only updated files for a new non-HV repack", ["hv"]) is None

    def test_negated_without_hv_does_not_match(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Works without HV and without VBS", ["hv"]) is None

    def test_negated_no_hv_does_not_match(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("No HV required for this build", ["hv"]) is None

    def test_keyword_inside_a_word_does_not_match(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("shevron graphics and whatever", ["hv"]) is None

    def test_multi_word_keyword_matches(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match("Includes the Goldberg emulator build", ["emulator"]) == "emulator"

    def test_empty_inputs_return_none(self) -> None:
        from gamarr.pipeline import _keyword_match

        assert _keyword_match(None, ["hv"]) is None
        assert _keyword_match("HV", None) is None
        assert _keyword_match("HV", []) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py -k TestKeywordMatch -q`
Expected: FAIL — `ImportError: cannot import name '_keyword_match'`.

- [ ] **Step 3: Implement the matcher and rewire the check**

In `src/gamarr/pipeline.py`, replace the whole `_title_contains_keywords` function with:

```python
# Words that negate a keyword when they appear immediately before it, e.g.
# "non-HV repack" or "without HV".  FitGirl's patch-note prose uses these when
# describing how to move *off* an HV release, which must not reject the page.
_NEGATION_TOKENS: tuple[str, ...] = ("non-", "without", "no", "not")
_NEGATION_WINDOW = 25


def _keyword_match(text: str | None, keywords: list[str] | None) -> str | None:
    """Return the first whole-word, non-negated keyword found in *text*.

    Matching is case-insensitive and anchored on word boundaries, so ``hv``
    matches "HV version" but not "shevron".  An occurrence is skipped when the
    25 characters immediately before it end with a negation token
    (``non-``, ``without``, ``no``, ``not``) — that is the difference between
    "a new non-HV repack" (not this repack) and "an HV version" (this repack).

    Args:
        text: The text to scan, or ``None``.
        keywords: Configured reject keywords, or ``None``.

    Returns:
        The matched keyword, lowercased, or ``None``.
    """
    if not text or not keywords:
        return None
    for keyword in keywords:
        needle = keyword.strip()
        if not needle:
            continue
        pattern = re.compile(rf"\b{re.escape(needle)}\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - _NEGATION_WINDOW) : match.start()].lower().rstrip()
            if any(window.endswith(token) for token in _NEGATION_TOKENS):
                continue
            return needle.lower()
    return None
```

Then update `_check_reject_keywords` (currently around line 2178). Replace its body from the `article_text = _truncate_at_backwards_compat(article_text)` line to the final `return False` with:

```python
    features = _extract_repack_features(article_text)
    if features is None:
        logger.debug(
            "FitGirl page for '{}' has no Repack Features block — matching the page title only",
            game_title,
        )
    elif matched := _keyword_match(features, reject_keywords):
        logger.info(
            "Skipping match for '{}' — FitGirl Repack Features contains rejected keyword '{}'",
            game_title,
            matched,
        )
        db.touch_pending(game_slug)
        return True

    if page_title and (matched := _keyword_match(page_title, reject_keywords)):
        logger.info(
            "Skipping match for '{}' — FitGirl page title '{}' contains rejected keyword '{}'",
            game_title,
            page_title,
            matched,
        )
        db.touch_pending(game_slug)
        return True

    return False
```

Delete the now-unused `_truncate_at_backwards_compat` function entirely.

Update the `_check_reject_keywords` docstring: it currently describes truncating before "Backwards Compatibility" and checking the whole article body. Replace that paragraph with:

```python
    The check reads only the page ``<title>`` and the "Repack Features" block.
    Scanning the whole article produced false positives: the download-mirror
    boilerplate mentions "non-HV repack" even on pages whose repack is not HV,
    and the backwards-compatibility prose refers to older releases.
```

- [ ] **Step 4: Replace the obsolete backwards-compat test**

In `tests/unit/test_pipeline.py`, delete the whole `test_reject_keywords_backwards_compat_excluded` method (it asserts the removed truncation behaviour) and add in its place:

```python
    def test_reject_keywords_ignores_boilerplate_above_features(self, tmp_path: Path) -> None:
        """Boilerplate above the Repack Features block must not reject a non-HV repack."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _check_reject_keywords

        db = Database(str(tmp_path / "keywords.db"))
        db.record_pending(slug="forza-horizon-6", game_title="Forza Horizon 6", platform="pc")
        article = (
            "Download Mirrors Only updated files for a new non-HV repack. "
            "convert your INSTALLED HV version of the game "
            "Repack Features Nothing ripped, 100% lossless, custom launcher added "
            "Screenshots gallery"
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=("Forza Horizon 6", article),
        ):
            skipped = _check_reject_keywords(
                db,
                {"url": "https://fitgirl-repacks.site/forza-horizon-6/"},
                "Forza Horizon 6",
                "forza-horizon-6",
                ["hv", "hypervisor", "emulator"],
            )

        assert skipped is False, "boilerplate mentions of HV must not reject a non-HV repack"
        db.close()

    def test_reject_keywords_rejects_a_real_hv_repack(self, tmp_path: Path) -> None:
        """A repack that states a hypervisor bypass in its features is still rejected."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.pipeline import _check_reject_keywords

        db = Database(str(tmp_path / "keywords.db"))
        db.record_pending(slug="black-myth-wukong", game_title="Black Myth: Wukong", platform="pc")
        article = (
            "Download Mirrors direct links "
            "Repack Features Based on P2P Steam BuildID 21393610 release: 139.7 GB "
            "Hypervisor Bypass (INTEL & AMD) by DenuvOwO + Goldberg emu added "
            "Screenshots gallery"
        )
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=("Black Myth: Wukong - Digital Deluxe Edition", article),
        ):
            skipped = _check_reject_keywords(
                db,
                {"url": "https://fitgirl-repacks.site/black-myth-wukong/"},
                "Black Myth: Wukong",
                "black-myth-wukong",
                ["hv", "hypervisor", "emulator"],
            )

        assert skipped is True, "a genuine HV repack must still be rejected"
        db.close()
```

- [ ] **Step 5: Run the keyword suites**

Run: `uv run pytest tests/unit/test_pipeline.py -k "Keyword or reject_keywords" -q`
Expected: PASS — the new matcher tests, the rewritten boilerplate test, and the untouched keyword tests (`keeps_pending_when_fetch_fails`, `ignores_download_link_anchor_text`, `ignores_comments_section`, `skipped_for_freegog`) all pass.

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: match reject keywords as whole words and ignore negated mentions"
```

---

### Task 3: One-time queue migration and `record_pending(score_checks_passed=...)`

**Files:**

- Modify: `src/gamarr/database.py` (`record_pending`, `_migrate`, new `_migrate_pending_queue_hygiene`)
- Test: `tests/unit/test_database.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_database.py`:

```python
class TestRecordPendingVerified:
    """record_pending can insert an already-verified row."""

    def test_verified_row_carries_the_flag(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.record_pending(
            slug="verified-game",
            game_title="Verified Game",
            platform="pc",
            metascore=88.0,
            user_score=8.4,
            score_checks_passed=True,
        )
        pending = db.get_pending()
        assert len(pending) == 1
        assert pending[0].score_checks_passed is True
        db.close()

    def test_default_row_is_unverified(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.record_pending(slug="unverified-game", game_title="Unverified Game", platform="pc")
        pending = db.get_pending()
        assert pending[0].score_checks_passed is not True
        db.close()


class TestPendingQueueHygieneMigration:
    """Unverified rows are removed once, verified rows are kept."""

    def test_removes_unverified_keeps_verified(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        path = str(tmp_path / "queue.db")
        db = Database(path)
        db.record_pending(slug="keep-me", game_title="Keep Me", platform="pc", score_checks_passed=True)
        db.record_pending(slug="drop-me", game_title="Drop Me", platform="pc")
        db.close()

        # Re-opening runs the guarded migration.
        db = Database(path)
        slugs = {game.slug for game in db.get_pending()}
        assert slugs == {"keep-me"}
        db.close()

    def test_runs_only_once(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        path = str(tmp_path / "queue.db")
        db = Database(path)
        db.close()

        db = Database(path)
        # A row queued by the new (verified-only) path after the migration must survive.
        db.record_pending(slug="later-game", game_title="Later Game", platform="pc", score_checks_passed=True)
        db.record_pending(slug="later-unverified", game_title="Later Unverified", platform="pc")
        db.close()

        db = Database(path)
        slugs = {game.slug for game in db.get_pending()}
        assert slugs == {"later-game", "later-unverified"}, "the migration must not repeat"
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_database.py -k "RecordPendingVerified or QueueHygiene" -q`
Expected: FAIL — `TypeError: record_pending() got an unexpected keyword argument 'score_checks_passed'`; the migration tests fail because unverified rows survive.

- [ ] **Step 3: Add the record_pending argument**

In `src/gamarr/database.py`, add to `record_pending`'s signature (after `expires_at: str | None = None,`):

```python
        score_checks_passed: bool = False,
```

and in the `PendingGame(...)` construction add:

```python
                score_checks_passed=True if score_checks_passed else None,
```

Update the docstring with: `score_checks_passed: Mark the row verified at insert time (used by verify-before-queue).`

- [ ] **Step 4: Add the guarded migration**

In `src/gamarr/database.py`, add a module constant next to the other module-level constants (near `_INDEFINITE_DAYS`):

```python
# PRAGMA user_version stamp for the one-time pending-queue hygiene migration.
_QUEUE_HYGIENE_USER_VERSION = 1
```

Register it in `_migrate()` after `self._migrate_unify_pending_tables()`:

```python
        self._migrate_pending_queue_hygiene()
```

Add the method after `_migrate_unify_pending_tables`:

```python
    def _migrate_pending_queue_hygiene(self) -> None:
        """Delete pending rows that can never match a source (runs once).

        Rows queued before verify-before-queue were admitted on browse-page
        metrics, so most can never pass detail verification.  The delete is
        stamped in ``PRAGMA user_version`` because repeating it on every open
        would discard rows queued by the new verified-only path.
        """
        try:
            with self._engine.connect() as conn:
                version = int(conn.execute(text("PRAGMA user_version")).scalar() or 0)
                if version >= _QUEUE_HYGIENE_USER_VERSION:
                    return
                kept = conn.execute(
                    text("SELECT COUNT(*) FROM pending_games WHERE score_checks_passed IS TRUE")
                ).scalar()
                result = conn.execute(text("DELETE FROM pending_games WHERE score_checks_passed IS NOT TRUE"))
                removed = result.rowcount or 0
                conn.execute(text(f"PRAGMA user_version = {_QUEUE_HYGIENE_USER_VERSION}"))
                conn.commit()
            logger.info(
                "Queue migration: removed {} unverified pending rows ({} verified kept)",
                removed,
                kept or 0,
            )
        except Exception:
            logger.warning("Queue migration of pending_games failed — rows left in place", exc_info=True)
```

- [ ] **Step 5: Run the database suites**

Run: `uv run pytest tests/unit/test_database.py -q`
Expected: PASS — the new classes plus every existing migration test.

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/database.py tests/unit/test_database.py
git commit -m "feat: drop unverified pending rows once and allow verified inserts"
```

---

### Task 4: Verify before queueing

**Files:**

- Modify: `src/gamarr/pipeline.py` (`_process_browse_games` signature/body; its call site at ~line 589)
- Test: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_pipeline.py`:

```python
class TestVerifyBeforeQueue:
    """Only games that already pass detail verification enter the queue."""

    @staticmethod
    def _run(tmp_path: Path, *, result: Any) -> list[str]:
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games
        from gamarr.metacritic import ScoreResult

        db = Database(str(tmp_path / "queue.db"))
        mc = MagicMock()
        mc.lookup_game.return_value = result
        games = [
            {
                "title": "Candidate",
                "slug": "candidate",
                "score": 1478.0,
                "user_rating": 8.2,
                "critic_review_count": 40,
                "user_review_count": 40,
                "release_date": "2026-11-01",
            }
        ]
        _process_browse_games(
            games,
            "pc",
            db,
            {
                "min_criticscore": 75,
                "min_criticscore_reviews": 10,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
            max_queue_days=60,
            mc=mc,
            cache_details_days=7,
            cache_pages_hours=6,
        )
        slugs = [game.slug for game in db.get_pending()]
        db.close()
        return slugs

    def test_passing_game_is_queued_verified(self, tmp_path: Path) -> None:
        from gamarr.metacritic import ScoreResult

        result = ScoreResult(
            title="Candidate",
            slug="candidate",
            metascore=88.0,
            metascore_review_count=40,
            user_score=8.4,
            user_review_count=40,
            passed=True,
        )
        assert self._run(tmp_path, result=result) == ["candidate"]

    def test_failing_game_is_not_queued(self, tmp_path: Path) -> None:
        from gamarr.metacritic import ScoreResult

        result = ScoreResult(
            title="Candidate",
            slug="candidate",
            metascore=40.0,
            metascore_review_count=40,
            user_score=4.0,
            user_review_count=40,
            passed=False,
        )
        assert self._run(tmp_path, result=result) == []

    def test_unreviewed_game_is_not_queued(self, tmp_path: Path) -> None:
        from gamarr.metacritic import ScoreResult

        result = ScoreResult(
            title="Candidate",
            slug="candidate",
            metascore=None,
            metascore_review_count=None,
            user_score=None,
            user_review_count=None,
            passed=False,
        )
        assert self._run(tmp_path, result=result) == []

    def test_lookup_failure_is_not_queued(self, tmp_path: Path) -> None:
        assert self._run(tmp_path, result=None) == []

    def test_queued_row_is_marked_verified(self, tmp_path: Path) -> None:
        import datetime

        from gamarr.database import Database
        from gamarr.metacritic import ScoreResult

        result = ScoreResult(
            title="Candidate",
            slug="candidate",
            metascore=88.0,
            metascore_review_count=40,
            user_score=8.4,
            user_review_count=40,
            passed=True,
        )
        self._run(tmp_path, result=result)
        db = Database(str(tmp_path / "queue.db"))
        pending = db.get_pending()
        assert pending and pending[0].score_checks_passed is True
        assert pending[0].metascore == 88.0, "the real detail score must be stored, not the browse metric"
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py -k TestVerifyBeforeQueue -q`
Expected: FAIL — `TypeError: _process_browse_games() got an unexpected keyword argument 'mc'`.

- [ ] **Step 3: Implement verify-before-insert**

In `src/gamarr/pipeline.py`, extend `_process_browse_games`:

```python
def _process_browse_games(
    browse_games: list[dict[str, Any]],
    platform: str,
    db: Database,
    thresholds: dict[str, Any],
    *,
    max_queue_days: int = 30,
    reject_title: list[str] | None = None,
    mc: Any = None,
    cache_details_days: int = 7,
    cache_pages_hours: int = 6,
) -> int:
```

Extend the docstring with:

```python
    Games surviving the cheap filters are verified against the Metacritic detail
    page before insertion: only games that pass are queued, and they are queued
    with their real scores and ``score_checks_passed`` set.  Games that fail, or
    whose scores are not published yet, are not inserted — they are not marked
    known either, so the next browse pass re-evaluates them.
```

In the loop, after the browse-review-count rejection and before `expires_at = _calculate_pending_expiry(...)`, insert:

```python
        detail = mc.lookup_game(
            g_title,
            platform,
            cache_details_days=cache_details_days,
            cache_pages_hours=cache_pages_hours,
            slug=g_slug,
        )
        if detail is None:
            logger.debug("Skipping '{}' — detail lookup failed, retried on the next pass", g_title)
            continue
        if not _real_scores_pass_thresholds(detail, thresholds):
            logger.debug("Skipping '{}' — detail scores do not pass the thresholds yet", g_title)
            continue
```

Then change the `db.record_pending(...)` call to use the detail values and the verified flag:

```python
        db.record_pending(
            slug=g_slug,
            game_title=g_title,
            platform=platform,
            metascore=detail.metascore,
            metascore_reviews=detail.metascore_review_count,
            user_score=detail.user_score,
            user_reviews=detail.user_review_count,
            genres=detail.genres,
            release_date=detail.release_date or game.get("release_date"),
            expires_at=expires_at,
            score_checks_passed=True,
        )
```

Update the call site (~line 589) to pass the client and cache settings:

```python
                new_pending = _process_browse_games(
                    browse_games,
                    platform,
                    db,
                    thresholds,
                    max_queue_days=cfg.max_queue_days,
                    reject_title=cfg.reject_title,
                    mc=mc,
                    cache_details_days=cfg.cache_details_days,
                    cache_pages_hours=cfg.cache_hours,
                )
```

- [ ] **Step 4: Run the pipeline suite**

Run: `uv run pytest tests/unit/test_pipeline.py -q`
Expected: PASS. Tests that call `_process_browse_games` without `mc` rely on the `mc=None` default — if any of them now fail with `AttributeError: 'NoneType' object has no attribute 'lookup_game'`, give each such call `mc=MagicMock(lookup_game=lambda *_a, **_k: None)` and expect zero queued rows, which is the correct new behaviour.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: verify Metacritic detail scores before queueing a game"
```

---

### Task 5: `mode` config field and plumbing

**Files:**

- Modify: `src/gamarr/config.py` (`MetacriticPlatformConfig`)
- Modify: `src/gamarr/pipeline.py` (`AcquisitionConfig`, `run_acquisition` signature/construction)
- Modify: `src/gamarr/scheduler.py` (`_build_kwargs`)
- Test: `tests/unit/test_config.py`, `tests/unit/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
class TestDiscoveryModeConfig:
    """mode: latest | backlog."""

    def test_default_is_backlog(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        assert MetacriticPlatformConfig().mode == "backlog"

    def test_accepts_latest(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        assert MetacriticPlatformConfig(mode="latest").mode == "latest"

    def test_rejects_unknown_mode(self) -> None:
        import pytest
        from pydantic import ValidationError

        from gamarr.config import MetacriticPlatformConfig

        with pytest.raises(ValidationError):
            MetacriticPlatformConfig(mode="newest")  # type: ignore[arg-type]
```

Append to `tests/unit/test_scheduler.py`:

```python
class TestBuildKwargsDiscoveryMode:
    """_build_kwargs must forward the discovery mode."""

    def test_forwards_mode(self) -> None:
        from gamarr.scheduler import _build_kwargs

        config = _make_config()
        config.review_sites.metacritic.platform_overrides["pc"].mode = "latest"
        assert _build_kwargs(config)["mode"] == "latest"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -k DiscoveryModeConfig tests/unit/test_scheduler.py -k DiscoveryMode -q`
Expected: FAIL — `mode` is not a field; `_build_kwargs` has no `mode` key.

- [ ] **Step 3: Implement the plumbing**

`src/gamarr/config.py` — in `MetacriticPlatformConfig`, after `cache_hours: int = 6` add:

```python
    mode: Literal["latest", "backlog"] = "backlog"
```

`src/gamarr/pipeline.py` — in `AcquisitionConfig` after `max_cycle_months: int = 0` add:

```python
    mode: Literal["latest", "backlog"] = "backlog"
```

In `run_acquisition`'s signature after `max_cycle_months: int = 0,` add:

```python
    mode: Literal["latest", "backlog"] = "backlog",
```

and in the `AcquisitionConfig(...)` construction add:

```python
        mode=mode,
```

`src/gamarr/scheduler.py` — in `_build_kwargs`, after `"max_cycle_months": mc_cfg.max_cycle_months,` add:

```python
        "mode": mc_cfg.mode,
```

- [ ] **Step 4: Run the config and scheduler suites**

Run: `uv run pytest tests/unit/test_config.py tests/unit/test_scheduler.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py src/gamarr/pipeline.py src/gamarr/scheduler.py tests/unit/test_config.py tests/unit/test_scheduler.py
git commit -m "feat: add explicit discovery mode to config and pipeline"
```

---

### Task 6: Latest-mode resolution and depth

> **Superseded (2026-11-09):** latest mode is now date-driven. When `max_date` or
> `max_cycle_months` is set, those keys decide the window and `max_pages` is a fallback used
> only when neither is set. Two excerpts in this task were removed by that change and must
> **not** be re-implemented: the `max_cycle_months ignored in latest mode` warning and the
> `test_inert_pacing_keys_are_reported` assertion. The `Discovery mode: latest (depth ...)`
> line still exists for depth-driven latest — only its wording and arguments changed. The
> rest of the task is the historical record; the live contract is in
> `docs/agent/specs/2026-11-06-discovery-modes-and-queue-hygiene-design.md`
> ("Date keys win in latest mode"), which is the file to update if the behaviour changes
> again.

**Files:**

- Modify: `src/gamarr/pipeline.py` (`run_acquisition` normalisation; `_run_discovery_phases` mode block, year loop, progress log)
- Test: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_pipeline.py`:

```python
class TestLatestMode:
    """Latest mode reads the top of the window and never touches cursors."""

    @staticmethod
    def _run(
        *,
        tmp_path: Path,
        mode: str = "latest",
        max_pages: int = 3,
        max_date: int = 0,
        max_cycle_months: int = 0,
        max_cycle_pages: int = 0,
        sort_order: str = "new",
    ) -> tuple[list[dict[str, Any]], list[str]]:
        from loguru import logger as loguru_logger

        calls: list[dict[str, Any]] = []
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            calls.append(dict(kwargs))
            mock_mc._recent_games_last_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_cutoff_reached = False
            mock_mc._recent_games_stop_cutoff = None
            return [{"title": "G", "slug": "g", "release_date": "2026-11-01"}]

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc._recent_games_stop_cutoff = None
        mock_mc.scan_recent_games.side_effect = fake_scan

        logs: list[str] = []
        sink = loguru_logger.add(lambda msg: logs.append(str(msg)), level="INFO", format="{message}")
        try:
            with (
                patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
                patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
                patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
            ):
                mock_mc_cls.return_value = mock_mc
                mock_qbt = MagicMock()
                mock_qbt.is_connected.return_value = True
                mock_qbt_cls.return_value = mock_qbt
                run_acquisition(
                    platform="pc",
                    db_path=str(tmp_path / "latest.db"),
                    qbt_host="localhost",
                    qbt_port=8080,
                    mode=mode,
                    max_pages=max_pages,
                    max_date=max_date,
                    max_cycle_months=max_cycle_months,
                    max_cycle_pages=max_cycle_pages,
                    sort_order=sort_order,
                )
        finally:
            loguru_logger.remove(sink)
        return calls, logs

    def test_starts_at_page_one_and_stops_at_depth(self, tmp_path: Path) -> None:
        calls, _logs = self._run(tmp_path=tmp_path, max_pages=3)
        assert calls, "expected at least one browse call"
        assert calls[0]["start_page"] == 1
        assert all(call["start_page"] == 1 for call in calls)
        assert all(call["max_pages"] == 3 for call in calls), "depth is the per-call page limit"
        assert len(calls) == 1, "depth is spent in one year before descending"

    def test_does_not_persist_cursors(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        path = str(tmp_path / "latest.db")
        self._run(tmp_path=tmp_path, max_pages=3)
        db = Database(path)
        assert db.get_last_scanned_page("pc", 2026) == 0, "latest mode must not write cursors"
        db.close()

    def test_logs_the_mode_and_scan_summary(self, tmp_path: Path) -> None:
        _calls, logs = self._run(tmp_path=tmp_path, max_pages=3)
        assert any("Discovery mode: latest" in line for line in logs), logs
        assert any("Latest scan:" in line for line in logs), logs

    def test_inert_pacing_keys_are_reported(self, tmp_path: Path) -> None:
        _calls, logs = self._run(tmp_path=tmp_path, max_pages=3, max_cycle_months=2, max_cycle_pages=4)
        assert any("max_cycle_months ignored in latest mode" in line for line in logs), logs
        assert any("max_cycle_pages ignored in latest mode" in line for line in logs), logs

    def test_score_sort_is_coerced_with_a_warning(self, tmp_path: Path) -> None:
        calls, logs = self._run(tmp_path=tmp_path, max_pages=3, sort_order="criticscore")
        assert any("latest mode requires sort_order 'new'" in line.lower() for line in logs), logs
        assert calls[0]["year"] is not None, "coerced scans must keep the per-year dimension"

    def test_zero_depth_is_clamped(self, tmp_path: Path) -> None:
        calls, logs = self._run(tmp_path=tmp_path, max_pages=0)
        assert calls[0]["max_pages"] == 10
        assert any("clamping" in line.lower() for line in logs), logs

    def test_backlog_mode_keeps_using_cursors(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        path = str(tmp_path / "backlog.db")
        self._run(tmp_path=tmp_path, mode="backlog", max_pages=3)
        db = Database(path)
        assert db.get_last_scanned_page("pc", 2026) >= 1, "backlog mode must still advance cursors"
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py -k TestLatestMode -q`
Expected: FAIL — latest mode is not implemented; the runs behave like backlog (cursors written, no mode log).

- [ ] **Step 3: Add the depth normalisation**

In `run_acquisition`, after the existing `max_cycle_months` normalisation block, add:

```python
    if mode == "latest" and max_pages == 0:
        logger.warning("mode: latest with max_pages: 0 is unbounded — clamping depth to 10 pages")
        max_pages = 10
```

- [ ] **Step 4: Resolve the mode and the scan order**

In `_run_discovery_phases`, replace the date-mode preamble:

```python
        # Mode resolution: a date bound needs the per-year browse dimension,
        # which only sort_order "new" has.  Everywhere else the page budget and
        # its pace stay in charge.
        date_mode = bool(cfg.max_date) and cfg.sort_order == "new"
        floor_month = cfg.max_date if date_mode else 0
        floor_cutoff = year_month_to_date(floor_month) if date_mode else None
```

with:

```python
        # Mode resolution.  "latest" reads the top of the window every cycle and
        # never persists cursors; "backlog" sweeps with cursors and pacing.  A
        # date bound needs the per-year browse dimension, which only sort_order
        # "new" has.
        latest_mode = cfg.mode == "latest"
        scan_order: Literal["new", "criticscore", "userscore"] = (
            "new" if latest_mode else cfg.sort_order
        )
        if latest_mode and cfg.sort_order != "new":
            logger.warning(
                "Latest mode requires sort_order 'new' — scanning in 'new' order for this cycle (configured '{}')",
                cfg.sort_order,
            )
        date_mode = bool(cfg.max_date) and scan_order == "new"
        floor_month = cfg.max_date if date_mode else 0
        floor_cutoff = year_month_to_date(floor_month) if date_mode else None
        if latest_mode:
            logger.info(
                "Discovery mode: latest (depth {} pages, window floor {})",
                cfg.max_pages,
                floor_month or "none",
            )
        else:
            logger.info("Discovery mode: backlog")
```

Then, throughout `_run_discovery_phases`, replace every use of `cfg.sort_order` that means "the browse order" with `scan_order`:

- the sort-order-change detection (`db.get_last_sort_order` / `clear_browse_cache` / `reset_progress` / `db.set_last_sort_order`),
- `mc.sort_order = ...`,
- the year-range calculation,
- `year=scan_year if cfg.sort_order == "new" else None`,
- the `db.reset_progress(platform, cfg.sort_order)` calls.

Leave `db.get_pending(..., sort_order=cfg.sort_order)` and `_verify_pending_scores(..., sort_order=cfg.sort_order)` as `cfg.sort_order`: those order the *queue*, which latest mode does not change.

- [ ] **Step 5: Implement the latest branch in the year-loop preamble**

Replace the inert-key and year-range block with the mode-aware version:

```python
            if not latest_mode and cfg.max_date and not date_mode:
                logger.info(
                    "Config: max_date/max_cycle_months ignored because sort_order is '{}'",
                    scan_order,
                )
            if not latest_mode and cfg.max_cycle_months and not date_mode:
                logger.info(
                    "Config: max_cycle_months ignored because date mode is off (max_date={}, sort_order='{}')",
                    cfg.max_date,
                    scan_order,
                )
            if date_mode and not latest_mode and cfg.max_pages:
                logger.info(
                    "Config: max_pages ({}) is unused in date mode (max_date={})",
                    cfg.max_pages,
                    floor_month,
                )
            if date_mode and not latest_mode and cfg.max_cycle_pages:
                logger.info(
                    "Config: max_cycle_pages ignored in date mode (max_cycle_pages={}, max_cycle_months={})",
                    cfg.max_cycle_pages,
                    cfg.max_cycle_months,
                )
            if latest_mode:
                if cfg.max_cycle_months:
                    logger.info("Config: max_cycle_months ignored in latest mode ({})", cfg.max_cycle_months)
                if cfg.max_cycle_pages:
                    logger.info("Config: max_cycle_pages ignored in latest mode ({})", cfg.max_cycle_pages)

            # Calculate year range
            if scan_order == "new":
                if date_mode:
                    cutoff_year = min(scan_year_anchor, floor_month // 100)
                elif latest_mode:
                    # No floor configured: the window is the current year only.
                    cutoff_year = scan_year_anchor
                else:
                    years_back = max(0, math.ceil((cfg.max_pages if cfg.max_pages else 500) / 52))
                    cutoff_year = scan_year_anchor - years_back
                current_year = scan_year_anchor
            else:
                # sort_order != "new" (criticscore/userscore): no year dimension, use year=0 sentinel
                cutoff_year = 0
                current_year = 0
```

In the page-budget setup, latest mode must not use the durable budget. Replace:

```python
            max_pages_cfg = cfg.max_pages if cfg.max_pages else 0
            if date_mode:
```

with:

```python
            max_pages_cfg = cfg.max_pages if cfg.max_pages else 0
            if latest_mode:
                # Latest depth is per cycle: no durable budget, no reset.
                remaining_pages = 0
                cycle_allowance = 0
            elif date_mode:
```

- [ ] **Step 6: Implement the latest branch in the loop body**

In the year loop, replace the per-year guards:

```python
                if date_mode:
                    if cfg.max_cycle_months and _month_span(max_month_seen, min_month_seen) >= cfg.max_cycle_months:
                        break
                else:
```

with:

```python
                if latest_mode:
                    if pages_this_cycle >= max_pages_cfg:
                        break
                elif date_mode:
                    if cfg.max_cycle_months and _month_span(max_month_seen, min_month_seen) >= cfg.max_cycle_months:
                        break
                else:
```

Replace the cursor read:

```python
                start_page = db.get_last_scanned_page(platform, scan_year) + 1
```

with:

```python
                start_page = 1 if latest_mode else db.get_last_scanned_page(platform, scan_year) + 1
```

Replace the date-mode per-call branch so latest passes the depth as the page limit:

```python
                    if latest_mode:
                        per_call_max = max(max_pages_cfg - pages_this_cycle, 0)
                        cutoff_date = floor_cutoff
                        month_span_left = 0
                    elif date_mode:
```

(keeping the existing `date_mode` body and the `else:` page-mode body unchanged below it).

Replace the cursor write:

```python
                    if pages_scanned > 0:
                        db.set_last_scanned_page(platform, scan_year, last_page)
```

with:

```python
                    if pages_scanned > 0 and not latest_mode:
                        db.set_last_scanned_page(platform, scan_year, last_page)
```

Gate the drained/floor reset blocks so latest never resets cursors: change

```python
            if date_mode:
```

to

```python
            if date_mode and not latest_mode:
```

and the page-mode drained reset's `elif`-guard block from

```python
            else:
                # A full pass that found no pages anywhere means the range is fully
```

to

```python
            elif not latest_mode:
                # A full pass that found no pages anywhere means the range is fully
```

- [ ] **Step 7: Replace the progress log**

Replace the progress block:

```python
        if cfg.enabled and date_mode:
            logger.info(
                "Backlog: reached {} (floor {}, {} page(s) this cycle)",
```

with a latest branch before it:

```python
        if cfg.enabled and latest_mode:
            logger.info(
                "Latest scan: pages 1..{} of {} ({} page(s) read, {} collected, {} queued)",
                max_pages_cfg,
                f"{floor_month // 100}" if floor_month else "current year",
                pages_this_cycle,
                len(browse_games),
                new_pending,
            )
        elif cfg.enabled and date_mode:
            logger.info(
                "Backlog: reached {} (floor {}, {} page(s) this cycle)",
```

- [ ] **Step 8: Run the pipeline suite**

Run: `uv run pytest tests/unit/test_pipeline.py -q`
Expected: PASS — the new `TestLatestMode` class plus every existing backlog/date-mode test (the mode defaults to `backlog`, so nothing else changes).

- [ ] **Step 9: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: read the top of the window in latest mode without cursors"
```

---

### Task 7: README and README-accuracy tests

**Files:**

- Modify: `README.md` (config table ~line 181-186; migration notes; keyword note)
- Test: `tests/unit/test_readme_accuracy.py`

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_readme_accuracy.py`, inside `class TestReadmeDateModeRows` add:

```python
    def test_mode_default_row(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        defaults = self._readme_defaults()
        assert defaults.get("mode", "MISSING") == cfg.mode
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_readme_accuracy.py -k mode_default_row -q`
Expected: FAIL — `README says mode default is 'MISSING'`.

- [ ] **Step 3: Update the README**

In the `review_sites.metacritic.platform_overrides.<platform>` table, insert before the `max_pages` row:

```markdown
| `mode` | Discovery mode. `"latest"` reads the first `max_pages` pages of the window every cycle (no cursors, no pacing) so new releases are always seen; `"backlog"` sweeps back through the window with per-year cursors and pacing. Latest requires `sort_order: "new"` — a score sort is warned about and coerced to `"new"` for that cycle. | `"backlog"` |
```

Update the `max_pages` row's description to state both meanings:

```markdown
| `max_pages` | Page budget. In `backlog` mode: total browse page depth budget; the scanner advances across cycles and resets to page 1 when exhausted. In `latest` mode: depth — the first N pages of the window are read every cycle. `0` = unlimited in backlog, clamped to 10 in latest. | `500` |
```

Add a queue note under the table (three lines):

```markdown
Games are verified against their Metacritic detail page **before** they enter the pending
queue, so the queue only ever holds games that already pass the score thresholds. Games whose
scores are not published yet are re-checked on the next pass rather than queued.
```

Add to the migrations/upgrade notes near the README's "Automatic config migration" bullet:

```markdown
- **One-time queue cleanup** — on first start after upgrading, pending games that never passed
  detail verification are deleted (they are re-discovered by the next sweep). Verified games
  awaiting a source match are kept.
```

In the FitGirl per-source `reject_keywords` row, append:

```markdown
Matching is whole-word and negation-aware against the page title and the "Repack Features" block only, so "non-HV repack" does not match `hv`.
```

- [ ] **Step 4: Run the README tests**

Run: `uv run pytest tests/unit/test_readme_accuracy.py -q`
Expected: PASS.

Run: `markdownlint -c <(printf '{"default": true, "MD013": {"line_length": 120, "tables": false}}') README.md`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/unit/test_readme_accuracy.py
git commit -m "docs: document discovery modes, verify-before-queue and keyword scope"
```

---

### Task 8: Amend the spec and run the full gate

**Files:**

- Modify: `docs/agent/specs/2026-11-06-discovery-modes-and-queue-hygiene-design.md` (§4 block terminator)

- [ ] **Step 1: Amend the spec's block-terminator sentence**

Replace, in spec §4:

```markdown
  The block starts at the "Repack Features" heading and ends at the first of: a known
  section heading (`Screenshots`, `System Requirements`, `Backwards Compatibility`,
  `Game Description`, `Included DLCs`, `Download Mirrors`), a heading-like line that starts
  before a blank line and ends with a colon within 60 characters, or 4000 characters — so a
  page missing the following heading cannot silently scan the whole article.
```

with:

```markdown
  The block starts at the "Repack Features" heading and ends at the first known section
  heading (`Screenshots`, `System Requirements`, `Backwards Compatibility`,
  `Game Description`, `Included DLCs`, `Download Mirrors`) or after 4000 characters,
  whichever comes first — so a page missing the following heading cannot silently scan the
  whole article. (The extracted article text has whitespace collapsed, so a
  colon-terminated heading line cannot be detected; the known-heading list replaces it.)
```

- [ ] **Step 2: Run every gate**

```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy .
uv run pytest -q
uv run pytest --cov=gamarr --cov-fail-under=95 -q
```

Expected: ruff and mypy clean; all tests pass; coverage ≥ 95%.

- [ ] **Step 3: Confirm the behaviour end to end against the live database (read-only)**

```bash
sqlite3 /media/appdata/config/gamarr/gamarr/db/gamarr.db "select count(*), sum(score_checks_passed is true) from pending_games;"
```

Expected before the migration runs on that instance: `1555|21`. Do not modify that file — the migration applies on the daemon's next start.

- [ ] **Step 4: Commit**

```bash
git add docs/agent/specs/2026-11-06-discovery-modes-and-queue-hygiene-design.md
git commit -m "docs: record the Repack Features block terminator refinement"
```

---

## Self-review

### 1. Spec coverage

| Spec requirement | Task |
| ------------------ | ------ |
| §1 `mode` field, default `backlog` | Task 5 |
| §1 per-mode meanings of `max_pages`/`max_cycle_pages`/`max_date`/`max_cycle_months` | Task 6 (behaviour) + Task 7 (docs) |
| §1 inert-key warnings | Task 6 Step 5 |
| §1 depth clamp `0 → 10` | Task 6 Step 3 |
| §1 `run_acquisition`/`AcquisitionConfig` gain `mode`; scheduler forwards | Task 5 |
| §2 latest = page 1, depth, no cursors, no reset | Task 6 Steps 4-6 |
| §2 mode reported once per cycle | Task 6 Step 4 |
| §2 latest logging replaces the progress line | Task 6 Step 7 |
| §3 verify-before-insert, only passing games queued | Task 4 |
| §3 no schema change; `_verify_pending_scores` retained | Task 4 (`score_checks_passed` argument only) |
| §4 scope = title + Repack Features, truncation removed | Tasks 1, 2 |
| §4 whole-word + negation (25-char window) | Task 2 |
| §4 missing features block falls back to title-only | Task 1 + Task 2 Step 3 |
| §5 one-time delete of unverified rows, `PRAGMA user_version` | Task 3 |
| §5 migration logged at INFO | Task 3 |
| Error-handling table rows | Tasks 2 (missing block), 3 (migration failure), 4 (lookup failure), 6 (sort coercion, depth clamp, inert keys) |
| README rows, queue note, migration note, keyword note | Task 7 |

### 2. Placeholder scan

 no "TBD", "implement later", "similar to Task N", or code-free implementation steps. Every code step carries runnable code; every run step carries the command and its expected result. Signatures quoted in the plan were checked against the source (`_check_reject_keywords`, `_process_browse_games`, `record_pending`, `ScoreResult`, `MetacriticPlatformConfig`).

### 3. Type consistency

 `mode: Literal["latest", "backlog"]` on config and `AcquisitionConfig`; `scan_order: Literal["new", "criticscore", "userscore"]`; `_extract_repack_features(str | None) -> str | None`; `_keyword_match(str | None, list[str] | None) -> str | None`; `_process_browse_games(..., mc: Any = None, cache_details_days: int = 7, cache_pages_hours: int = 6)`; `record_pending(..., score_checks_passed: bool = False)`; `_QUEUE_HYGIENE_USER_VERSION = 1`. Names are used identically in every task.
