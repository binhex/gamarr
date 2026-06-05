"""Tests for gamarr notifications module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


class TestNotifierSend:
    """Test notification dispatch with mocked Apprise."""

    def test_send_with_mocked_apprise(self) -> None:
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="Test Game", platform="pc",
                metascore=85.0, user_score=8.0,
                magnet_url="magnet:?xt=urn:btih:abc",
            )
            mock_apobj.notify.assert_called_once()

    def test_send_error_notification_with_mock(self) -> None:
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"], on_error=True)
            notifier.send_error_notification(error_message="Test error")
            mock_apobj.notify.assert_called_once()

    def test_init_apprise_failure_logs_warning(self) -> None:
        with patch.object(Notifier, "_init_apprise", return_value=None):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="Test", platform="pc",
                metascore=80.0, user_score=7.5,
                magnet_url="magnet:?xt=urn:btih:abc",
            )

    def test_send_exception_caught(self) -> None:
        mock_apobj = MagicMock()
        mock_apobj.notify.side_effect = Exception("send failure")
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="Test", platform="pc",
                metascore=80.0, user_score=7.5,
                magnet_url="magnet:?xt=urn:btih:abc",
            )
