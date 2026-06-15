"""Notification dispatch for gamarr using Apprise."""

from __future__ import annotations

from typing import Any

from loguru import logger


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
        self._apprise = self._init_apprise()

    def _init_apprise(self) -> Any:
        if not self._urls:
            return None
        try:
            import apprise

            apobj = apprise.Apprise()
            for url in self._urls:
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

    def send_failure_notification(self, title: str, reason: str) -> None:
        if not self._on_failure or not self._apprise:
            return
        body = f"gamarr: {title} failed checks\nReason: {reason}"
        self._send("gamarr - Failed", body)

    def send_error_notification(self, error_message: str) -> None:
        if not self._on_error or not self._apprise:
            return
        body = f"gamarr pipeline error:\n{error_message}"
        self._send("gamarr - Error", body)

    def send_scrape_notification(self, message: str) -> None:
        """Send a notification when Metacritic scraping appears to be broken.

        Controlled by the ``on_scrape_failure`` config option.

        Args:
            message: Description of the scraping issue to include in the body.
        """
        if not self._on_scrape_failure or not self._apprise:
            return
        body = f"{message}\n\nThis may indicate a Metacritic site change or network issue."
        self._send("gamarr - Scraping Issue", body)

    def _send(self, title: str, body: str) -> None:
        if not self._apprise:
            return
        try:
            self._apprise.notify(title=title, body=body)
        except Exception as exc:
            logger.warning("Failed to send notification '{}': {}", title, exc)
