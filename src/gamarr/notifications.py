"""Notification dispatch for gamarr using Apprise."""

from __future__ import annotations

import urllib.parse
from typing import Any

from loguru import logger

_MARKDOWN_SCHEMES = frozenset({"ntfy", "ntfys", "discord", "slack", "tgram", "tg", "matrix", "matrixs"})


class Notifier:
    """Sends notifications for gamarr events via Apprise."""

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

    @staticmethod
    def _format_score_line(label: str, score: float | None, reviews: int | None) -> str:
        """Format a score line, optionally appending review count in brackets."""
        line = f"{label}: {'N/A' if score is None else score}"
        if score is not None and reviews is not None:
            line += f" ({reviews} reviews)"
        return line

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

    @staticmethod
    def _append_optional_fields(
        parts: list[str],
        *,
        must_play: bool | None,
        genres: list[str] | None,
        release_date: str | None,
    ) -> None:
        """Append optional metadata fields to the body parts list."""
        if must_play is not None:
            parts.append(f"Must Play: {'Yes' if must_play else 'No'}")
        if genres:
            parts.append(f"Genre: {', '.join(genres)}")
        if release_date:
            parts.append(f"Release: {release_date}")

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
        Notifier._append_optional_fields(parts, must_play=must_play, genres=genres, release_date=release_date)
        parts.append(link("Metacritic", f"https://www.metacritic.com/game/{slug or 'unknown'}", use_markdown=mk))
        if source_name and source_url:
            parts.append(link(source_name.title(), source_url, use_markdown=mk))
        parts.append(link("YouTube", Notifier._youtube_search_url(title), use_markdown=mk))
        return "\n".join(parts)

    def send_failure_notification(self, title: str, reason: str) -> None:
        if not self._on_failure:
            return
        if not self._apprise_md and not self._apprise_text:
            return
        body = f"gamarr: {title} failed checks\nReason: {reason}"
        self._send("gamarr - Failed", body)

    def send_error_notification(self, error_message: str) -> None:
        if not self._on_error:
            return
        if not self._apprise_md and not self._apprise_text:
            return
        body = f"gamarr pipeline error:\n{error_message}"
        self._send("gamarr - Error", body)

    def send_scrape_notification(self, message: str) -> None:
        """Send a notification when Metacritic scraping appears to be broken.

        Controlled by the ``on_scrape_failure`` config option.

        Args:
            message: Description of the scraping issue to include in the body.
        """
        if not self._on_scrape_failure:
            return
        if not self._apprise_md and not self._apprise_text:
            return
        body = f"{message}\n\nThis may indicate a Metacritic site change or network issue."
        self._send("gamarr - Scraping Issue", body)

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
