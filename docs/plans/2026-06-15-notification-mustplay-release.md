# Notification: Must Play and Release Date — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `Must Play: Yes/No` and `Release: YYYY-MM-DD` fields to the download notification body, with reordered format (scores first).

**Architecture:** Two files change: `notifications.py` adds the new parameters and formats them; `pipeline.py` passes the already-available `game_must_play` and `game_release_date` through `_safe_notify`. Test files mirror both changes.

**Tech Stack:** Python 3.12, Apprise, pytest

**Spec:** `docs/superpowers/specs/2026-06-15-notification-mustplay-release-design.md`

---

### Task 1: Update notification format in `send_download_notification`

**Files:**
- Modify: `src/gamarr/notifications.py`
- Test: `tests/unit/test_notifications.py`

**Context:** `send_download_notification` builds a body string from a `parts` list. Currently the order is: Status, Link, Genre, Critic Score, User Score. The new order is: Status, Critic Score, User Score, Must Play, Genre, Release, Link. Two new parameters `must_play` and `release_date` are added.

The spec requires testing `must_play=True`, `must_play=False`, and `must_play=None` (omitted). The existing format tests cover `None` (they don't pass the new params). Step 1 covers `False`. Step 1b covers `True`.

- [ ] **Step 1: Write the failing test for the new format (must_play=False)**

Add this test to `TestNotifierFormat` in `tests/unit/test_notifications.py`, after `test_download_notification_when_paused`:

```python
def test_download_notification_with_must_play_and_release(self) -> None:
    """When must_play and release_date are provided, they appear in the body."""
    mock_apobj = MagicMock()
    with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
        notifier = Notifier(apprise_urls=["json://localhost"])
        notifier.send_download_notification(
            title="PRAGMATA",
            platform="pc",
            metascore=85.0,
            metascore_reviews=50,
            user_score=8.8,
            user_reviews=100,
            slug="pragmata",
            genres=["Action", "Adventure"],
            must_play=False,
            release_date="2026-06-15",
            add_paused=False,
        )
        mock_apobj.notify.assert_called_once_with(
            title="gamarr - PRAGMATA (pc)",
            body=(
                "Status: Downloading\n"
                "Critic Score: 85.0 (50 reviews)\n"
                "User Score: 8.8 (100 reviews)\n"
                "Must Play: No\n"
                "Genre: Action, Adventure\n"
                "Release: 2026-06-15\n"
                "Link: https://www.metacritic.com/game/pc/pragmata/"
            ),
        )

```

- [ ] **Step 1b: Write a second failing test (must_play=True)**

Add after `test_download_notification_with_must_play_and_release`:

```python
def test_download_notification_must_play_yes(self) -> None:
    """When must_play is True, shows 'Must Play: Yes'."""
    mock_apobj = MagicMock()
    with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
        notifier = Notifier(apprise_urls=["json://localhost"])
        notifier.send_download_notification(
            title="PRAGMATA",
            platform="pc",
            metascore=85.0,
            metascore_reviews=50,
            user_score=8.8,
            user_reviews=100,
            slug="pragmata",
            must_play=True,
            add_paused=False,
        )
        mock_apobj.notify.assert_called_once()
        body = mock_apobj.notify.call_args[1]["body"]
        assert "Must Play: Yes" in body
        assert "Must Play: No" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_notifications.py::TestNotifierFormat::test_download_notification_with_must_play_and_release tests/unit/test_notifications.py::TestNotifierFormat::test_download_notification_must_play_yes -v --no-cov`
Expected: Both FAIL — `TypeError: send_download_notification() got unexpected keyword arguments 'must_play', 'release_date'`

- [ ] **Step 3: Update the existing two format tests to match new body order**

The tests `test_download_notification_format` and `test_download_notification_when_paused` both assert the old body format. Update their expected body strings to the new order (without Must Play or Release lines when the params are omitted):

**Update `test_download_notification_format` body assertion** (lines ~131-136):
Change from:
```python
body=(
    "Status: Downloading\n"
    "Link: https://www.metacritic.com/game/pc/pragmata/\n"
    "Genre: Action, Adventure\n"
    "Critic Score: 85.0 (50 reviews)\n"
    "User Score: 8.8 (100 reviews)"
),
```
To:
```python
body=(
    "Status: Downloading\n"
    "Critic Score: 85.0 (50 reviews)\n"
    "User Score: 8.8 (100 reviews)\n"
    "Genre: Action, Adventure\n"
    "Link: https://www.metacritic.com/game/pc/pragmata/"
),
```

**Update `test_download_notification_when_paused` body assertion** (lines ~162-167):
Change from:
```python
body=(
    "Status: Paused\n"
    "Link: https://www.metacritic.com/game/ps5/elden-ring/\n"
    "Critic Score: N/A\n"
    "User Score: N/A"
),
```
To:
```python
body=(
    "Status: Paused\n"
    "Critic Score: N/A\n"
    "User Score: N/A\n"
    "Link: https://www.metacritic.com/game/ps5/elden-ring/"
),
```

- [ ] **Step 4: Run tests to verify they now fail (correctly updated)**

Run: `uv run pytest tests/unit/test_notifications.py -v --no-cov`
Expected: All three format tests FAIL — the function doesn't have the new params or new order yet.

- [ ] **Step 5: Implement the changes in `src/gamarr/notifications.py`**

Change `send_download_notification` to:

1. Add two new optional parameters: `must_play: bool | None = None`, `release_date: str | None = None`
2. Reorder the `parts` list construction
3. Add Must Play and Release lines when values are present

Replace the body-building portion of `send_download_notification`:

```python
    def send_download_notification(
        self,
        title: str,
        platform: str,
        metascore: float | None,
        user_score: float | None,
        slug: str,
        add_paused: bool = False,
        metascore_reviews: int | None = None,
        user_reviews: int | None = None,
        genres: list[str] | None = None,
        must_play: bool | None = None,
        release_date: str | None = None,
    ) -> None:
        if not self._on_download or not self._apprise:
            return
        status = "Paused" if add_paused else "Downloading"
        link_slug = slug if slug else "unknown"
        link_platform = platform if platform else "unknown"
        genre_line = f"Genre: {', '.join(genres)}" if genres else None
        must_play_line = f"Must Play: {'Yes' if must_play else 'No'}" if must_play is not None else None
        release_line = f"Release: {release_date}" if release_date else None

        parts = [f"Status: {status}"]
        parts.extend(
            [
                self._format_score_line("Critic Score", metascore, metascore_reviews),
                self._format_score_line("User Score", user_score, user_reviews),
            ]
        )
        if must_play_line:
            parts.append(must_play_line)
        if genre_line:
            parts.append(genre_line)
        if release_line:
            parts.append(release_line)
        parts.append(f"Link: https://www.metacritic.com/game/{link_platform}/{link_slug}/")

        self._send(f"gamarr - {title} ({platform})", "\n".join(parts))
```

Note: `must_play_line` uses `'Yes' if must_play else 'No'` — since `must_play` is a `bool | None`, the `if must_play` correctly evaluates `True`/`False`. This works because we already checked `must_play is not None` in the guard above.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_notifications.py -v --no-cov`
Expected: All tests PASS (including the 3 format tests and all other notification tests)

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest --cov=src/gamarr --cov-fail-under=95`
Expected: All tests pass, coverage >= 95%

- [ ] **Step 8: Commit**

```bash
git add src/gamarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat: add Must Play and Release Date to download notifications

Reorders notification body to: Status, Critic Score, User Score,
Must Play, Genre, Release, Link. Omits Must Play/Release lines
when their value is None."
```

---

### Task 2: Pass `game_must_play` and `game_release_date` through `_deliver_match`

**Files:**
- Modify: `src/gamarr/pipeline.py`
- Test: `tests/unit/test_pipeline.py`

**Context:** `_deliver_match` already receives `game_must_play` and `game_release_date` as parameters (lines ~1305-1306). It already passes `game_genres` through `_safe_notify` to `send_download_notification`. We just need to add the two new fields to the kwargs dict at the `_safe_notify` call site (line ~1517).

- [ ] **Step 1: Write the failing test**

Add this test to `TestMetacriticBrowse` (or create a new test class) in `tests/unit/test_pipeline.py`:

```python
    def test_deliver_match_passes_must_play_and_release_to_notify(self) -> None:
        """_deliver_match should forward must_play and release_date to the notifier."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _deliver_match

        db = Database(":memory:")
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "tag-123"
        mock_notifier = MagicMock()
        mock_magnet = MagicMock(return_value="magnet:?xt=urn:btih:abc")

        best = {"url": "https://fitgirl-repacks.site/game/", "title": "Some Game"}

        result = _deliver_match(
            db,
            qbt=mock_qbt,
            magnet_fetcher=mock_magnet,
            notifier=mock_notifier,
            best=best,
            game_slug="some-game",
            game_title="Some Game",
            game_platform="pc",
            game_metascore=80.0,
            game_user_score=8.0,
            game_metascore_reviews=10,
            game_user_reviews=50,
            game_genres=["Action"],
            game_must_play=True,
            game_release_date="2024-10-11",
        )

        assert result["result"] == "Passed"
        # Verify must_play and release_date were passed to the notifier
        call_kwargs = mock_notifier.send_download_notification.call_args[1]
        assert call_kwargs.get("must_play") is True
        assert call_kwargs.get("release_date") == "2024-10-11"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pipeline.py::TestMetacriticBrowse::test_deliver_match_passes_must_play_and_release_to_notify -v --no-cov`
Expected: FAIL — `send_download_notification()` got unexpected keyword argument `must_play` (because the call site hasn't been updated). Verify the error message is about missing kwargs.

- [ ] **Step 3: Update `_deliver_match` — add must_play and release_date to `_safe_notify` call**

In `src/gamarr/pipeline.py`, at the `_safe_notify` call at line ~1517, add two new kwargs:

```python
    _safe_notify(
        notifier,
        "send_download_notification",
        title=game_title,
        platform=game_platform,
        metascore=game_metascore,
        metascore_reviews=game_metascore_reviews,
        user_score=game_user_score,
        user_reviews=game_user_reviews,
        slug=game_slug,
        genres=game_genres,
        must_play=game_must_play,
        release_date=game_release_date,
        add_paused=qbt.add_paused,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_pipeline.py::TestMetacriticBrowse::test_deliver_match_passes_must_play_and_release_to_notify -v --no-cov`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest --cov=src/gamarr --cov-fail-under=95`
Expected: All tests pass, coverage >= 95%

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: forward must_play and release_date to notification dispatch"
```

---

### Task 3: Final verification and linting

- [ ] **Step 1: Run full QA stack**

Run:
```bash
cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy . && uv run pytest --cov=src/gamarr --cov-fail-under=95
```

Expected: All checks pass, no warnings, 95%+ coverage.

- [ ] **Step 2: Commit plan document**

```bash
git add docs/superpowers/plans/2026-06-15-notification-mustplay-release.md
git commit -m "docs: add implementation plan for notification must-play and release fields"
```
