# YouTube Search Link in Download Notification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a YouTube search link to download notifications by constructing a search URL from the game title and appending it after the Metacritic link. Also rename `Link:` → `Metacritic:` to avoid label collision.

**Architecture:** Single-file change in `notifications.py` — add a static helper `_youtube_search_url()` that URL-encodes the game title with `urllib.parse.quote_plus` and returns a YouTube search URL. Append the result as a `YouTube:` line in `_format_download_body()`. No new files, no config changes, no pipeline changes.

**Tech Stack:** Python 3.12+, stdlib `urllib.parse`, Apprise notifications

---

### Task 1: Write the failing tests for `_youtube_search_url`

**Files:**
- Modify: `tests/unit/test_notifications.py`

- [ ] **Step 1: Add `import urllib.parse` at the top of the test file**

Near the other imports (line 5 after `from gamarr.notifications import Notifier`), add:

```python
import urllib.parse
```

- [ ] **Step 2: Add a new test class `TestYouTubeSearchUrl` with two tests**

Add this new class at the end of `tests/unit/test_notifications.py`, before the final blank line:

```python
class TestYouTubeSearchUrl:
    """Tests for _youtube_search_url static helper."""

    def test_youtube_search_url_encodes_spaces(self) -> None:
        """Title with spaces produces URL with + separators."""
        result = Notifier._youtube_search_url("Elden Ring")
        assert result == "https://www.youtube.com/results?search_query=Elden+Ring+review"

    def test_youtube_search_url_encodes_special_chars(self) -> None:
        """Titles with colons and punctuation are URL-safe encoded."""
        result = Notifier._youtube_search_url("STAR WARS: Battlefront")
        encoded = urllib.parse.quote_plus("STAR WARS: Battlefront review")
        expected = f"https://www.youtube.com/results?search_query={encoded}"
        assert result == expected
```

- [ ] **Step 3: Run the new tests to verify they fail (RED)**

```bash
uv run pytest tests/unit/test_notifications.py::TestYouTubeSearchUrl -v
```

Expected: `FAILED` — `AttributeError: type object 'Notifier' has no attribute '_youtube_search_url'`.

### Task 2: Implement `_youtube_search_url` helper

**Files:**
- Modify: `src/gamarr/notifications.py`

- [ ] **Step 1: Add `import urllib.parse` to notification module**

At the top of `src/gamarr/notifications.py`, after the existing `from typing import Any` (line 5), add:

```python
import urllib.parse
```

- [ ] **Step 2: Add the `_youtube_search_url` static method to `Notifier`**

Add this static method inside the `Notifier` class, right after `_format_score_line` (after line 47, before `send_download_notification`):

```python
    @staticmethod
    def _youtube_search_url(title: str) -> str:
        """Build a YouTube search URL for a game title.

        Args:
            title: The game title to search for.

        Returns:
            A YouTube search results URL with the title and "review" as the query.
        """
        query = urllib.parse.quote_plus(f"{title} review")
        return f"https://www.youtube.com/results?search_query={query}"
```

- [ ] **Step 3: Run the new tests to verify they pass (GREEN)**

```bash
uv run pytest tests/unit/test_notifications.py::TestYouTubeSearchUrl -v
```

Expected: `2 passed`.

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat: add _youtube_search_url helper to Notifier"
```

### Task 3: Add YouTube line and rename Metacritic label in notification body

**Files:**
- Modify: `src/gamarr/notifications.py`

- [ ] **Step 1: Rename `Link:` → `Metacritic:` and add `YouTube:` line**

In `_format_download_body()` (around line 109), change the last `parts.append` before the return from:

```python
        parts.append(f"Link: https://www.metacritic.com/game/{slug or 'unknown'}/")
        return "\n".join(parts)
```

To:

```python
        parts.append(f"Metacritic: https://www.metacritic.com/game/{slug or 'unknown'}/")
        parts.append(f"YouTube: {Notifier._youtube_search_url(title)}")
        return "\n".join(parts)
```

Note: `title` is already a parameter of `_format_download_body()`. If the format doesn't have a `title` parameter, we need to add it. Let's check — looking at the signature at line 82, the parameters are `add_paused`, `metascore`, `metascore_reviews`, `user_score`, `user_reviews`, `must_play`, `genres`, `release_date`, `slug`, `platform`. No `title` parameter. So we need to add `title: str` after `slug: str,` in the parameter list.

The full signature change — in `_format_download_body()`, add `title: str,` between `slug: str,` and `platform: str,`:

```python
    @staticmethod
    def _format_download_body(
        *,
        add_paused: bool,
        metascore: float | None,
        metascore_reviews: int | None,
        user_score: float | None,
        user_reviews: int | None,
        must_play: bool | None,
        genres: list[str] | None,
        release_date: str | None,
        slug: str,
        title: str,
        platform: str,
    ) -> str:
```

- [ ] **Step 2: Update the call to `_format_download_body` in `send_download_notification`**

In `send_download_notification()` (around line 69), the call to `_format_download_body` doesn't pass `title`. Add `title=title,` to the call. Look for:

```python
        body = self._format_download_body(
            add_paused=add_paused,
            ...
            slug=slug,
            platform=platform,
        )
```

Add `title=title,` before `platform=platform,`:

```python
        body = self._format_download_body(
            add_paused=add_paused,
            metascore=metascore,
            metascore_reviews=metascore_reviews,
            user_score=user_score,
            user_reviews=user_reviews,
            must_play=must_play,
            genres=genres,
            release_date=release_date,
            slug=slug,
            title=title,
            platform=platform,
        )
```

- [ ] **Step 3: Run all notification tests to verify nothing is broken**

```bash
uv run pytest tests/unit/test_notifications.py -v
```

Expected: Some existing tests will FAIL because their expected body strings still say `"Link:"` instead of `"Metacritic:"` and are missing the `"YouTube:"` line. This is expected — those test assertions need updating in Task 4.

### Task 4: Update existing format test assertions

**Files:**
- Modify: `tests/unit/test_notifications.py`

- [ ] **Step 1: Update `test_download_notification_format`**

Change the expected body in `test_download_notification_format` from:

```python
                    "Status: Downloading\n"
                    "Critic Score: 85.0 (50 reviews)\n"
                    "User Score: 8.8 (100 reviews)\n"
                    "Genre: Action, Adventure\n"
                    "Link: https://www.metacritic.com/game/pragmata/"
```

To:

```python
                    "Status: Downloading\n"
                    "Critic Score: 85.0 (50 reviews)\n"
                    "User Score: 8.8 (100 reviews)\n"
                    "Genre: Action, Adventure\n"
                    "Metacritic: https://www.metacritic.com/game/pragmata/\n"
                    "YouTube: https://www.youtube.com/results?search_query=PRAGMATA+review"
```

Also update the docstring: change `Link: https://www.metacritic.com/game/<slug>/` to `Metacritic: https://www.metacritic.com/game/<slug>/` and add a new docstring line `YouTube: https://www.youtube.com/results?search_query=<title>+review`.

- [ ] **Step 2: Update `test_download_notification_when_paused`**

Change the expected body from:

```python
                    "Status: Paused\n"
                    "Critic Score: N/A\n"
                    "User Score: N/A\n"
                    "Link: https://www.metacritic.com/game/elden-ring/"
```

To:

```python
                    "Status: Paused\n"
                    "Critic Score: N/A\n"
                    "User Score: N/A\n"
                    "Metacritic: https://www.metacritic.com/game/elden-ring/\n"
                    "YouTube: https://www.youtube.com/results?search_query=Elden+Ring+review"
```

- [ ] **Step 3: Update `test_download_notification_with_must_play_and_release`**

Change the expected body from:

```python
                    "Status: Downloading\n"
                    "Critic Score: 85.0 (50 reviews)\n"
                    "User Score: 8.8 (100 reviews)\n"
                    "Must Play: No\n"
                    "Genre: Action, Adventure\n"
                    "Release: 2026-06-15\n"
                    "Link: https://www.metacritic.com/game/pragmata/"
```

To:

```python
                    "Status: Downloading\n"
                    "Critic Score: 85.0 (50 reviews)\n"
                    "User Score: 8.8 (100 reviews)\n"
                    "Must Play: No\n"
                    "Genre: Action, Adventure\n"
                    "Release: 2026-06-15\n"
                    "Metacritic: https://www.metacritic.com/game/pragmata/\n"
                    "YouTube: https://www.youtube.com/results?search_query=PRAGMATA+review"
```

- [ ] **Step 4: Run all notification tests — all should pass**

```bash
uv run pytest tests/unit/test_notifications.py -v
```

Expected: All tests pass (no failures).

- [ ] **Step 5: Run the full test suite to ensure no regressions**

```bash
uv run pytest -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat: add YouTube search link to download notifications, rename Link to Metacritic"
```

---

### Task 5: Run quality gates

**Files:** None (verification only)

- [ ] **Step 1: Run ruff check and format**

```bash
uv run ruff check --fix . && uv run ruff format .
```

- [ ] **Step 2: Run mypy type check**

```bash
uv run mypy src/gamarr/notifications.py tests/unit/test_notifications.py
```

- [ ] **Step 3: Run test coverage**

```bash
uv run pytest --cov=gamarr --cov-fail-under=80 -v
```

Expected: Coverage stays at or above 80%, all tests pass.

- [ ] **Step 4: Commit any formatting fixes**

```bash
git add -A && git commit -m "chore: ruff fixes for YouTube notification feature" || echo "Nothing to commit"
```
