# Source-Name Prefix on Torrent Titles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepend `[FitGirl]` or `[FreeGOG]` to torrent titles so the download source is visible in qBittorrent at a glance.

**Architecture:** One-line change in `_deliver_match` (pipeline.py:1646) — format the title with the source display name from the existing `_source_display()` helper before passing to `qbt.add_torrent()`.

**Tech Stack:** Python 3.12, existing gamarr pipeline

---

## File Map

| Action | File | Line | Purpose |
|--------|------|------|---------|
| Modify | `src/gamarr/pipeline.py` | ~1646 | Format torrent title |
| Modify | `tests/unit/test_pipeline.py` | after line ~733 | Test title formatting |

---

### Task 1: Add test for source-prefixed FitGirl torrent title (RED)

**Files:**
- Modify: `tests/unit/test_pipeline.py` — add after `test_deliver_match_passes_must_play_and_release_to_notify` (line ~768)

- [ ] **Step 1: Write the failing test**

Add to `TestMetacriticBrowse` class. The test creates a pending game, calls `_deliver_match` with `source_name="fitgirl"`, and asserts the title passed to `qbt.add_torrent` contains `[FitGirl]`.

Insert after the last `_deliver_match` test (around line 768, after `test_deliver_match_passes_must_play_and_release_to_notify`):

```python
    def test_deliver_match_prepends_source_prefix_to_torrent_title(self, tmp_path: Path) -> None:
        """qbt.add_torrent should receive the title with the source name in brackets."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _deliver_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="test-game",
            game_title="Test Game",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="test-game", metascore=85.0, user_score=8.0)

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_notifier = MagicMock()

        best = {
            "title": "Test Game Repack",
            "url": "https://fitgirl-repacks.site/test-game/",
            "magnet": "magnet:?xt=urn:btih:abc",
        }

        _deliver_match(
            db,
            qbt=mock_qbt,
            magnet_fetcher=MagicMock(),
            notifier=mock_notifier,
            best=best,
            game_slug="test-game",
            game_title="Test Game",
            game_platform="pc",
            game_metascore=85.0,
            game_user_score=8.0,
            source_name="fitgirl",
        )

        _, kwargs = mock_qbt.add_torrent.call_args
        assert kwargs["title"] == "[FitGirl] Test Game Repack", (
            f"Expected '[FitGirl] Test Game Repack', got '{kwargs['title']}'"
        )
        db.close()
```

- [ ] **Step 2: Run test — verify it FAILS (RED)**

Run: `uv run pytest tests/unit/test_pipeline.py::TestMetacriticBrowse::test_deliver_match_prepends_source_prefix_to_torrent_title -v --tb=short`

Expected: FAIL with `AssertionError: Expected '[FitGirl] Test Game Repack', got 'Test Game Repack'`

(The current code passes `source_title` as-is without the prefix.)

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add failing test for source prefix on torrent titles"
```

---

### Task 2: Implement the fix (GREEN)

**Files:**
- Modify: `src/gamarr/pipeline.py:1646`

- [ ] **Step 1: Change the `qbt.add_torrent` call in `_deliver_match`**

In `_deliver_match`, locate line 1646:

```python
tag = qbt.add_torrent(magnet_url=magnet, title=source_title)
```

Replace with:

```python
display_name = _source_display(source_name)
tag = qbt.add_torrent(magnet_url=magnet, title=f"[{display_name}] {source_title}")
```

`_source_display` is already defined at line 620 in the same file. `source_name` is already a parameter of `_deliver_match` at line 1608.

- [ ] **Step 2: Run test — verify it PASSES (GREEN)**

Run: `uv run pytest tests/unit/test_pipeline.py::TestMetacriticBrowse::test_deliver_match_prepends_source_prefix_to_torrent_title -v --tb=short`

Expected: PASS

- [ ] **Step 3: Run existing delivery tests to check for regressions**

Run: `uv run pytest tests/unit/test_pipeline.py -k "deliver_match or match_pending_delivers" -v`

Expected: All existing tests pass (no regressions).

- [ ] **Step 4: Commit the fix**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: prepend source display name to torrent titles"
```

---

### Task 3: Add FreeGOG variant test

**Files:**
- Modify: `tests/unit/test_pipeline.py` — add after the FitGirl test

- [ ] **Step 1: Write the FreeGOG test**

Add right after the FitGirl test from Task 1:

```python
    def test_deliver_match_prepends_freegog_source_prefix(self, tmp_path: Path) -> None:
        """FreeGOG source name is correctly prepended to the torrent title."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _deliver_match

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="test-game2",
            game_title="Test Game 2",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="test-game2", metascore=85.0, user_score=8.0)

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_notifier = MagicMock()

        best = {
            "title": "Test Game 2",
            "url": "https://freegogpcgames.com/123/test-game-2/",
            "magnet": "magnet:?xt=urn:btih:def",
        }

        _deliver_match(
            db,
            qbt=mock_qbt,
            magnet_fetcher=MagicMock(),
            notifier=mock_notifier,
            best=best,
            game_slug="test-game2",
            game_title="Test Game 2",
            game_platform="pc",
            game_metascore=85.0,
            game_user_score=8.0,
            source_name="freegog",
        )

        _, kwargs = mock_qbt.add_torrent.call_args
        assert kwargs["title"] == "[FreeGOG] Test Game 2", (
            f"Expected '[FreeGOG] Test Game 2', got '{kwargs['title']}'"
        )
        db.close()
```

- [ ] **Step 2: Run both prefix tests**

Run: `uv run pytest tests/unit/test_pipeline.py -k "prepends_source_prefix" -v`

Expected: Both PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add FreeGOG variant for source prefix on torrent titles"
```

---

### Task 4: Full test suite and quality checks

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass (`X passed in Ys`).

- [ ] **Step 2: Run linter**

```bash
uv run ruff check .
```
Expected: `All checks passed!`

- [ ] **Step 3: Run type checker**

```bash
uv run mypy src/gamarr/pipeline.py
```
Expected: `Success: no issues found in 1 source file`

- [ ] **Step 4: Commit if any fixups needed, otherwise done**

```bash
git add -A
git commit -m "chore: post-implementation cleanup"
```
