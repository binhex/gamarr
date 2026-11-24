# Download-Source Link + Markdown Link Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add download-source link (FitGirl/FreeGOG) to notifications between Metacritic and YouTube links, and fix ntfy clickable-link issue via dual-format link rendering.

**Architecture:** Split Apprise URLs into two instances at init time — markdown-capable services (ntfy, Discord, Slack, Telegram, Matrix) and text-only services (email, everything else). Format links as `[label](url)` for markdown, `label: url` for text. Thread `source_name` + `source_url` from pipeline matching through to the notification call.

**Tech Stack:** Python 3.12+, Apprise (notification), pytest (testing), ruff + mypy (QC)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/gamarr/notifications.py` | Modify | Dual Apprise instances, `_format_link()`, `_is_markdown_service()`, `_maybe_upgrade_ntfy()`, source link in body, new params on `send_download_notification()` and `_format_download_body()` |
| `src/gamarr/pipeline.py` | Modify | Thread `source_name` through `_deliver_with_jit_verify` → `_deliver_match` → notification call |
| `tests/unit/test_notifications.py` | Modify | New test classes + update existing assertions |

---

### Task 1: Add `_format_link()` helper + markdown classification helpers (RED phase)

**Files:**
- Modify: `tests/unit/test_notifications.py`

- [ ] **Step 1: Write failing tests for `_format_link` and markdown classification**

Add after the existing `TestNotifierEdgeCases` class (before `TestYouTubeSearchUrl`):

```python
class TestFormatLink:
    """Tests for _format_link static helper."""

    def test_format_link_markdown(self) -> None:
        """Markdown mode produces [label](url) syntax."""
        result = Notifier._format_link("Metacritic", "https://example.com", use_markdown=True)
        assert result == "[Metacritic](https://example.com)"

    def test_format_link_text(self) -> None:
        """Text mode produces label: url syntax."""
        result = Notifier._format_link("Metacritic", "https://example.com", use_markdown=False)
        assert result == "Metacritic: https://example.com"

    def test_format_link_source_name_title_case(self) -> None:
        """source_name is title-cased: fitgirl -> FitGirl."""
        result = Notifier._format_link("FitGirl", "https://fitgirl-repacks.site/elden-ring", use_markdown=False)
        assert result == "FitGirl: https://fitgirl-repacks.site/elden-ring"


class TestMarkdownClassify:
    """Tests for URL scheme classification and ntfy upgrade."""

    def test_ntfy_is_markdown(self) -> None:
        """ntfy:// scheme is markdown-capable."""
        assert Notifier._is_markdown_service("ntfy://host/topic") is True

    def test_ntfys_is_markdown(self) -> None:
        """ntfys:// scheme is markdown-capable."""
        assert Notifier._is_markdown_service("ntfys://host/topic") is True

    def test_discord_is_markdown(self) -> None:
        """discord:// scheme is markdown-capable."""
        assert Notifier._is_markdown_service("discord://webhook_id/webhook_token") is True

    def test_email_is_not_markdown(self) -> None:
        """mailto:// scheme is NOT markdown-capable."""
        assert Notifier._is_markdown_service("mailto://user:pass@gmail.com") is False

    def test_json_is_not_markdown(self) -> None:
        """json:// scheme (used in tests) is NOT markdown-capable."""
        assert Notifier._is_markdown_service("json://localhost") is False

    def test_ntfy_upgrade_adds_format(self) -> None:
        """ntfy URL without format=markdown gets it appended."""
        result = Notifier._maybe_upgrade_ntfy("ntfy://host/topic")
        assert "format=markdown" in result

    def test_ntfy_upgrade_no_double(self) -> None:
        """ntfy URL already with format=markdown is left unchanged."""
        url = "ntfy://host/topic?format=markdown"
        result = Notifier._maybe_upgrade_ntfy(url)
        assert result == url

    def test_ntfy_upgrade_with_existing_params(self) -> None:
        """ntfy URL with other params gets format=markdown appended with &."""
        url = "ntfy://host/topic?priority=high"
        result = Notifier._maybe_upgrade_ntfy(url)
        assert result == "ntfy://host/topic?priority=high&format=markdown"

    def test_non_ntfy_unchanged(self) -> None:
        """Non-ntfy URLs pass through unchanged."""
        url = "discord://webhook/token"
        result = Notifier._maybe_upgrade_ntfy(url)
        assert result == url
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_notifications.py::TestFormatLink tests/unit/test_notifications.py::TestMarkdownClassify -v
```

Expected: FAIL (AttributeError — methods not defined yet)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_notifications.py
git commit -m "test: add failing tests for _format_link and markdown classification"
```

---

### Task 2: Implement `_format_link()` + markdown helpers (GREEN phase)

**Files:**
- Modify: `src/gamarr/notifications.py`

- [ ] **Step 1: Add module-level constant and three static methods to `Notifier`**

Add the `_MARKDOWN_SCHEMES` constant just below the imports:

```python
"""Notification dispatch for gamarr using Apprise."""

from __future__ import annotations

import urllib.parse
from typing import Any

from loguru import logger

_MARKDOWN_SCHEMES = frozenset({"ntfy", "ntfys", "discord", "slack", "tgram", "tg", "matrix", "matrixs"})
```

Add these three static methods inside the `Notifier` class, right after `_youtube_search_url` (after line 66):

```python
    @staticmethod
    def _is_markdown_service(url: str) -> bool:
        """Return True if *url* uses a scheme that supports markdown formatting."""
        try:
            scheme = url.split("://", 1)[0].lower()
        except (ValueError, AttributeError):
            return False
        return scheme in _MARKDOWN_SCHEMES

    @staticmethod
    def _maybe_upgrade_ntfy(url: str) -> str:
        """Append ``?format=markdown`` to ntfy URLs that don't already have it."""
        try:
            scheme = url.split("://", 1)[0].lower()
        except (ValueError, AttributeError):
            return url
        if scheme not in ("ntfy", "ntfys"):
            return url
        if "format=markdown" in url:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}format=markdown"

    @staticmethod
    def _format_link(label: str, url: str, *, use_markdown: bool) -> str:
        """Format a clickable link line.

        Args:
            label: Display text for the link (e.g. ``"Metacritic"``).
            url: The full URL.
            use_markdown: If True, produce ``[label](url)``; otherwise ``label: url``.

        Returns:
            A single-line string suitable for the notification body.
        """
        if use_markdown:
            return f"[{label}]({url})"
        return f"{label}: {url}"
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_notifications.py::TestFormatLink tests/unit/test_notifications.py::TestMarkdownClassify -v
```

Expected: 11 passed

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/notifications.py
git commit -m "feat: add _format_link, _is_markdown_service, _maybe_upgrade_ntfy helpers"
```

---

### Task 3: Wire dual Apprise instances in `Notifier.__init__` + `_send`

**Files:**
- Modify: `src/gamarr/notifications.py`
- Modify: `tests/unit/test_notifications.py` (update affected tests)

- [ ] **Step 1: Update `__init__` to split URLs into two Apprise instances**

Replace the existing `__init__` (lines 14-27) with:

```python
    def __init__(
        self,
        apprise_urls: list[str] | None = None,
        on_download: bool = True,
        on_failure: bool = False,
        on_error: bool = False,
        on_scrape_failure: bool = True,
    ) -> None:
        self._urls = apprise_urls or []
        self._on_download = on_download
        self._on_failure = on_failure
        self._on_error = on_error
        self._on_scrape_failure = on_scrape_failure

        # Split URLs into markdown-capable and text-only groups
        self._md_urls: list[str] = []
        self._text_urls: list[str] = []
        for url in self._urls:
            if Notifier._is_markdown_service(url):
                self._md_urls.append(Notifier._maybe_upgrade_ntfy(url))
            else:
                self._text_urls.append(url)

        self._apprise_md = self._init_apprise(self._md_urls)
        self._apprise_text = self._init_apprise(self._text_urls)
```

Replace `_init_apprise` (lines 29-40) with:

```python
    @staticmethod
    def _init_apprise(urls: list[str]) -> Any:
        if not urls:
            return None
        try:
            import apprise

            apobj = apprise.Apprise()
            for url in urls:
                apobj.add(url)
            return apobj
        except Exception as exc:
            logger.warning("Failed to initialise Apprise: {}", exc)
            return None
```

- [ ] **Step 2: Update `_send` to support dual instances**

Replace the existing `_send` (lines 153-158) with:

```python
    def _send(self, title: str, body: str, *, body_markdown: str | None = None) -> None:
        """Send notification to both markdown and text instances.

        Args:
            title: Notification title (shared across all instances).
            body: Plain-text body for the text instance (label: url format).
            body_markdown: Markdown body for the markdown instance
                ([label](url) format). If None, markdown instance is skipped.
        """
        if self._apprise_md and body_markdown is not None:
            try:
                self._apprise_md.notify(title=title, body=body_markdown)
            except Exception as exc:
                logger.warning("Failed to send markdown notification '{}': {}", title, exc)
        if self._apprise_text:
            try:
                self._apprise_text.notify(title=title, body=body)
            except Exception as exc:
                logger.warning("Failed to send text notification '{}': {}", title, exc)
```

- [ ] **Step 3: Update guard clauses in `send_download_notification`**

Replace the guard clauses at lines 79-80:

```python
        if not self._on_download:
            return
        if not self._apprise_md and not self._apprise_text:
            return
```

Also update `send_failure_notification` guard (line 129), `send_error_notification` guard (line 135), `send_scrape_notification` guard (line 148) — replace `not self._apprise` with `not self._apprise_md and not self._apprise_text`:

In `send_failure_notification`:
```python
        if not self._on_failure:
            return
        if not self._apprise_md and not self._apprise_text:
            return
```

In `send_error_notification`:
```python
        if not self._on_error:
            return
        if not self._apprise_md and not self._apprise_text:
            return
```

In `send_scrape_notification`:
```python
        if not self._on_scrape_failure:
            return
        if not self._apprise_md and not self._apprise_text:
            return
```

- [ ] **Step 4: Update existing failing tests**

The existing tests mock `_init_apprise` which now takes a `urls` parameter but as a `@staticmethod`. The mock patches need to be updated since `_init_apprise` is now a `@staticmethod` — the `patch.object` call `patch.object(Notifier, "_init_apprise", ...)` works the same. No mock signature changes needed because the method is patched entirely.

The guard clause tests that check `notifier._apprise` should be updated to check the new attributes. However, these tests don't access `_apprise` directly — they just verify no exception is raised.

Run the full test suite to identify any failing tests:

```bash
cd /data/gamarr && uv run pytest tests/unit/test_notifications.py -v 2>&1 | tail -40
```

Expected: existing tests may fail due to the guard clause changes; the `_send` method no longer checks a single `_apprise` attribute.

- [ ] **Step 5: Fix any broken existing tests**

The test `test_init_apprise_catches_import_error` checks `notifier._apprise is None` — change to `notifier._apprise_md is None and notifier._apprise_text is None`:

```python
    def test_init_apprise_catches_import_error(self) -> None:
        """Exception during Apprise initialisation is caught and returns None."""
        with patch("apprise.Apprise", side_effect=ImportError("No module named 'apprise'")):
            notifier = Notifier(apprise_urls=["json://localhost"])
            assert notifier._apprise_md is None
            assert notifier._apprise_text is None
```

The test `test_init_apprise_failure_logs_warning` patches `_init_apprise` to return None — it constructs a Notifier with `json://localhost` which goes to text instance. No change needed.

- [ ] **Step 6: Verify all existing tests pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_notifications.py -v
```

Expected: all existing tests pass (with guard clause + attribute updates)

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/notifications.py tests/unit/test_notifications.py
git commit -m "refactor: dual Apprise instances for markdown/text link formatting"
```

---

### Task 4: Add source link to notification body

**Files:**
- Modify: `tests/unit/test_notifications.py` (add tests first)
- Modify: `src/gamarr/notifications.py`

- [ ] **Step 1: Write failing tests for source link in notification body**

Add after the `TestMarkdownClassify` class:

```python
class TestSourceLink:
    """Tests for download-source link in notification body."""

    def test_source_link_fitgirl_appears_in_body(self) -> None:
        """When source_name='fitgirl' and source_url provided, FitGirl link appears."""
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="PRAGMATA",
                platform="pc",
                metascore=85.0,
                user_score=8.8,
                slug="pragmata",
                add_paused=False,
                source_name="fitgirl",
                source_url="https://fitgirl-repacks.site/pragmata",
            )
            body = mock_apobj.notify.call_args[1]["body"]
            assert "FitGirl: https://fitgirl-repacks.site/pragmata" in body

    def test_source_link_freegog_appears_in_body(self) -> None:
        """source_name='freegog' → FreeGog label."""
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="PRAGMATA",
                platform="pc",
                metascore=85.0,
                user_score=8.8,
                slug="pragmata",
                add_paused=False,
                source_name="freegog",
                source_url="https://freegogpcgames.com/pragmata",
            )
            body = mock_apobj.notify.call_args[1]["body"]
            assert "FreeGog: https://freegogpcgames.com/pragmata" in body

    def test_source_link_omitted_when_none(self) -> None:
        """When source_name and source_url are None, no source line in body."""
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="PRAGMATA",
                platform="pc",
                metascore=85.0,
                user_score=8.8,
                slug="pragmata",
                add_paused=False,
                source_name=None,
                source_url=None,
            )
            body = mock_apobj.notify.call_args[1]["body"]
            assert "FitGirl" not in body
            assert "FreeGog" not in body

    def test_source_link_omitted_when_only_name(self) -> None:
        """When only source_name provided (no URL), line is omitted."""
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="PRAGMATA",
                platform="pc",
                metascore=85.0,
                user_score=8.8,
                slug="pragmata",
                add_paused=False,
                source_name="fitgirl",
                source_url=None,
            )
            body = mock_apobj.notify.call_args[1]["body"]
            assert "FitGirl" not in body

    def test_source_link_ordering_metacritic_source_youtube(self) -> None:
        """Source link appears between Metacritic and YouTube links in body."""
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="PRAGMATA",
                platform="pc",
                metascore=85.0,
                user_score=8.8,
                slug="pragmata",
                add_paused=False,
                source_name="fitgirl",
                source_url="https://fitgirl-repacks.site/pragmata",
            )
            body = mock_apobj.notify.call_args[1]["body"]
            metacritic_pos = body.index("Metacritic:")
            source_pos = body.index("FitGirl:")
            youtube_pos = body.index("YouTube:")
            assert metacritic_pos < source_pos < youtube_pos
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_notifications.py::TestSourceLink -v
```

Expected: FAIL (source line not in body)

- [ ] **Step 3: Add `source_name` and `source_url` params to `send_download_notification` + `_format_download_body`**

Update `send_download_notification` signature (lines 67-87) — add two new params after `release_date`:

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
        source_name: str | None = None,
        source_url: str | None = None,
    ) -> None:
        if not self._on_download:
            return
        if not self._apprise_md and not self._apprise_text:
            return
        text_body = self._format_download_body(
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
            source_name=source_name,
            source_url=source_url,
            use_markdown=False,
        )
        md_body = None
        if self._apprise_md:
            md_body = self._format_download_body(
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
                source_name=source_name,
                source_url=source_url,
                use_markdown=True,
            )
        self._send(f"gamarr - {title} ({platform})", text_body, body_markdown=md_body)
```

- [ ] **Step 4: Update `_format_download_body` for new params + link formatting**

Replace the existing `_format_download_body` (lines 92-129) with:

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
        source_name: str | None = None,
        source_url: str | None = None,
        use_markdown: bool = False,
    ) -> str:
        """Build the notification body string for a game download."""
        link = Notifier._format_link
        mk = use_markdown
        parts = [f"Status: {'Paused' if add_paused else 'Downloading'}"]
        parts.extend(
            [
                Notifier._format_score_line("Critic Score", metascore, metascore_reviews),
                Notifier._format_score_line("User Score", user_score, user_reviews),
            ]
        )
        if must_play is not None:
            parts.append(f"Must Play: {'Yes' if must_play else 'No'}")
        if genres:
            parts.append(f"Genre: {', '.join(genres)}")
        if release_date:
            parts.append(f"Release: {release_date}")
        parts.append(link("Metacritic", f"https://www.metacritic.com/game/{slug or 'unknown'}", use_markdown=mk))
        if source_name and source_url:
            parts.append(link(source_name.title(), source_url, use_markdown=mk))
        parts.append(link("YouTube", Notifier._youtube_search_url(title), use_markdown=mk))
        return "\n".join(parts)
```

Note: the `link = Notifier._format_link` alias is for readability; it avoids 80+ character lines.

- [ ] **Step 5: Update existing full-body assertions to match new format**

In `test_download_notification_format` (line ~130), the trailing slash is dropped and links use `label: url` format (since `json://` is text-only). Update the assertion:

```python
            mock_apobj.notify.assert_called_once_with(
                title="gamarr - PRAGMATA (pc)",
                body=(
                    "Status: Downloading\n"
                    "Critic Score: 85.0 (50 reviews)\n"
                    "User Score: 8.8 (100 reviews)\n"
                    "Genre: Action, Adventure\n"
                    "Metacritic: https://www.metacritic.com/game/pragmata\n"
                    "YouTube: https://www.youtube.com/results?search_query=PRAGMATA+review"
                ),
            )
```

In `test_download_notification_when_paused`, same update (drop trailing slash):

```python
            mock_apobj.notify.assert_called_once_with(
                title="gamarr - Elden Ring (ps5)",
                body=(
                    "Status: Paused\n"
                    "Critic Score: N/A\n"
                    "User Score: N/A\n"
                    "Metacritic: https://www.metacritic.com/game/elden-ring\n"
                    "YouTube: https://www.youtube.com/results?search_query=Elden+Ring+review"
                ),
            )
```

In `test_download_notification_with_must_play_and_release`, same update:

```python
            mock_apobj.notify.assert_called_once_with(
                title="gamarr - PRAGMATA (pc)",
                body=(
                    "Status: Downloading\n"
                    "Critic Score: 85.0 (50 reviews)\n"
                    "User Score: 8.8 (100 reviews)\n"
                    "Must Play: No\n"
                    "Genre: Action, Adventure\n"
                    "Release: 2026-06-15\n"
                    "Metacritic: https://www.metacritic.com/game/pragmata\n"
                    "YouTube: https://www.youtube.com/results?search_query=PRAGMATA+review"
                ),
            )
```

Also update the docstring in `test_download_notification_format` to remove trailing slashes:

```python
        """send_download_notification should format with:

        Apprise title: gamarr - <game title> (<platform>)
        Body:
            Status: Downloading (or Paused)
            Critic Score: <score> (<reviews> reviews)
            User Score: <score> (<reviews> reviews)
            Must Play: Yes/No (when provided)
            Genre: <genre1>, <genre2> (when provided)
            Release: <YYYY-MM-DD> (when provided)
            Metacritic: https://www.metacritic.com/game/<slug>
            YouTube: https://www.youtube.com/results?search_query=<title>+review
        """
```

- [ ] **Step 6: Run all notification tests**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_notifications.py -v
```

Expected: all tests pass (~30 tests)

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat: add download-source link to notification body"
```

---

### Task 5: Add markdown-format notification tests

**Files:**
- Modify: `tests/unit/test_notifications.py`

- [ ] **Step 1: Write tests for markdown link format in notification body**

Add after the `TestSourceLink` class:

```python
class TestMarkdownNotification:
    """Tests for markdown-formatted notification bodies."""

    def test_markdown_links_use_bracket_syntax(self) -> None:
        """When a markdown-capable URL is used, links use [label](url)."""
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["discord://webhook/token"])
            notifier.send_download_notification(
                title="PRAGMATA",
                platform="pc",
                metascore=85.0,
                user_score=8.8,
                slug="pragmata",
                add_paused=False,
                source_name="fitgirl",
                source_url="https://fitgirl-repacks.site/pragmata",
            )
            body = mock_apobj.notify.call_args[1]["body"]
            assert "[Metacritic](https://www.metacritic.com/game/pragmata)" in body
            assert "[FitGirl](https://fitgirl-repacks.site/pragmata)" in body
            assert "[YouTube](https://www.youtube.com/results?search_query=PRAGMATA+review)" in body
            assert "Metacritic: http" not in body  # no bare text link for metacritic

    def test_markdown_omits_source_when_none(self) -> None:
        """Markdown mode omits source line when source_name is None."""
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["discord://webhook/token"])
            notifier.send_download_notification(
                title="PRAGMATA",
                platform="pc",
                metascore=85.0,
                user_score=8.8,
                slug="pragmata",
                add_paused=False,
            )
            body = mock_apobj.notify.call_args[1]["body"]
            assert "FitGirl" not in body
            assert "FreeGog" not in body

    def test_ntfy_url_gets_format_upgraded(self) -> None:
        """ntfy URL without format=markdown gets it appended on init."""
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["ntfy://host/topic"])
            # The ntfy URL in md_urls should have format=markdown appended
            assert any("format=markdown" in u for u in notifier._md_urls)

    def test_sends_to_both_instances(self) -> None:
        """When both markdown and text URLs are configured, both instances receive body."""
        mock_md = MagicMock()
        mock_text = MagicMock()

        def _fake_init(urls: list[str]) -> MagicMock:
            if urls and urls[0].startswith("discord"):
                return mock_md
            if urls:
                return mock_text
            return MagicMock()

        with patch.object(Notifier, "_init_apprise", side_effect=_fake_init):
            notifier = Notifier(apprise_urls=["discord://webhook/token", "json://localhost"])
            notifier.send_download_notification(
                title="PRAGMATA",
                platform="pc",
                metascore=85.0,
                user_score=8.8,
                slug="pragmata",
                add_paused=False,
                source_name="fitgirl",
                source_url="https://fitgirl-repacks.site/pragmata",
            )
            mock_md.notify.assert_called_once()
            mock_text.notify.assert_called_once()
            # Markdown instance gets bracket syntax
            md_body = mock_md.notify.call_args[1]["body"]
            assert "[Metacritic]" in md_body
            # Text instance gets bare URLs
            text_body = mock_text.notify.call_args[1]["body"]
            assert "Metacritic: https://" in text_body
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_notifications.py::TestMarkdownNotification -v
```

Expected: 4 passed

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_notifications.py
git commit -m "test: add markdown-format notification body tests"
```

---

### Task 6: Thread `source_name` through pipeline to notification

**Files:**
- Modify: `src/gamarr/pipeline.py`

- [ ] **Step 1: Add `source_name` param to `_deliver_with_jit_verify`**

At line 1423, add `source_name: str` to the parameter list after `game_release_date`:

```python
def _deliver_with_jit_verify(
    db: Database,
    mc: Any,
    thresholds: dict[str, Any] | None,
    game_title: str,
    game_slug: str,
    game_platform: str,
    game_metascore: float | None,
    game_user_score: float | None,
    game_metascore_reviews: int | None,
    game_user_reviews: int | None,
    game_release_date: str | None,
    *,
    qbt: Any,
    magnet_fetcher: Callable[[str], str | None],
    notifier: Any,
    best: dict[str, Any],
    source_name: str = "fitgirl",
) -> dict[str, Any] | None:
```

- [ ] **Step 2: Pass `source_name` through to `_deliver_match`**

At lines 1470-1486, add `source_name=source_name` to the `_deliver_match()` call:

```python
    return _deliver_match(
        db,
        qbt=qbt,
        magnet_fetcher=magnet_fetcher,
        notifier=notifier,
        best=best,
        game_slug=game_slug,
        game_title=game_title,
        game_platform=game_platform,
        game_metascore=game_metascore,
        game_user_score=game_user_score,
        game_metascore_reviews=game_metascore_reviews,
        game_user_reviews=game_user_reviews,
        game_genres=game_genres,
        game_must_play=game_must_play,
        game_release_date=game_release_date,
        source_name=source_name,
    )
```

- [ ] **Step 3: Add `source_name` param to `_deliver_match`**

At line 1578, add `source_name: str = "fitgirl"` to the parameter list after `game_release_date`:

```python
def _deliver_match(
    db: Database,
    *,
    qbt: Any,
    magnet_fetcher: Callable[[str], str | None],
    notifier: Any,
    best: dict[str, Any],
    game_slug: str,
    game_title: str,
    game_platform: str,
    game_metascore: float | None,
    game_user_score: float | None,
    game_metascore_reviews: int | None = None,
    game_user_reviews: int | None = None,
    game_genres: list[str] | None = None,
    game_must_play: bool | None = None,
    game_release_date: str | None = None,
    source_name: str = "fitgirl",
) -> dict[str, Any]:
```

- [ ] **Step 4: Pass `source_name` + `source_url` to notification call**

At lines 1685-1696, add `source_name=source_name, source_url=best["url"]` to the `_safe_notify` call:

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
        source_name=source_name,
        source_url=best["url"],
    )
```

- [ ] **Step 5: Pass `source_name` from `_process_single_pending_match` to `_deliver_with_jit_verify`**

At lines 1807-1825, add `source_name=source_name` to the `_deliver_with_jit_verify()` call:

```python
        result_dict = _deliver_with_jit_verify(
            db,
            mc,
            thresholds,
            game_title,
            game_slug,
            game_platform,
            game_metascore,
            game_user_score,
            game_metascore_reviews,
            game_user_reviews,
            game_release_date,
            qbt=qbt,
            magnet_fetcher=magnet_fetcher,
            notifier=notifier,
            best=best,
            source_name=source_name,
        )
```

- [ ] **Step 6: Run full test suite to verify no regressions**

```bash
cd /data/gamarr && uv run pytest -v 2>&1 | tail -30
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: thread source_name/source_url through pipeline to notification"
```

---

### Task 7: Quality gates

**Files:**
- None (verification only)

- [ ] **Step 1: Ruff lint + format**

```bash
cd /data/gamarr && uv run ruff check --fix . && uv run ruff format .
```

Expected: All checks passed, no changes needed (or minor formatting applied)

- [ ] **Step 2: Mypy type check**

```bash
cd /data/gamarr && uv run mypy .
```

Expected: No issues

- [ ] **Step 3: Full test suite**

```bash
cd /data/gamarr && uv run pytest -v
```

Expected: all tests pass

- [ ] **Step 4: Coverage check**

```bash
cd /data/gamarr && uv run pytest --cov=gamarr --cov-fail-under=80 -v 2>&1 | tail -20
```

Expected: coverage ≥ 80%

- [ ] **Step 5: Commit (if any QC changes)**

```bash
git add -A && git commit -m "chore: quality gate fixes"
```

Only if ruff/format/mypy produced changes.
