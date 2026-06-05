"""Tests for gamarr notifications module."""

from __future__ import annotations

from gamarr.notifications import Notifier


class TestNotifier:
    """Notifier construction and basic dispatch."""

    def test_no_urls_does_not_error(self) -> None:
        notifier = Notifier(apprise_urls=[])
        notifier.send_download_notification(
            title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            magnet_url="magnet:?xt=urn:btih:abc",
        )

    def test_single_url_constructs(self) -> None:
        notifier = Notifier(apprise_urls=["json://localhost"])
        assert notifier is not None

    def test_error_notification_does_not_error(self) -> None:
        notifier = Notifier(apprise_urls=[])
        notifier.send_error_notification(error_message="Test error")

    def test_failure_notification_when_disabled(self) -> None:
        """When on_failure is False, no notification is sent."""
        notifier = Notifier(apprise_urls=[], on_failure=False)
        notifier.send_failure_notification(title="Game", reason="Low score")

    def test_download_notification_when_disabled(self) -> None:
        notifier = Notifier(apprise_urls=[], on_download=False)
        notifier.send_download_notification(
            title="Game", platform="pc", metascore=80.0, user_score=8.0, magnet_url="magnet:?xt=urn:btih:abc"
        )
