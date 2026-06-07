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
    ) -> None:
        self._urls = apprise_urls or []
        self._on_download = on_download
        self._on_failure = on_failure
        self._on_error = on_error
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

    def send_download_notification(
        self,
        title: str,
        platform: str,
        metascore: float | None,
        user_score: float | None,
        magnet_url: str,
    ) -> None:
        if not self._on_download or not self._apprise:
            return
        body = (
            f"gamarr: {title} ({platform})\n"
            f"Metascore: {metascore or 'N/A'}\n"
            f"User Score: {user_score or 'N/A'}\n"
            f"Magnet: {magnet_url}"
        )
        self._send("gamarr - Download", body)

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

    def _send(self, title: str, body: str) -> None:
        if not self._apprise:
            return
        try:
            self._apprise.notify(title=title, body=body)
        except Exception as exc:
            logger.warning("Failed to send notification '{}': {}", title, exc)
