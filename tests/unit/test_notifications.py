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
            slug="elden-ring",
            add_paused=False,
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
            title="Game", platform="pc", metascore=80.0, user_score=8.0, slug="game", add_paused=False
        )

    def test_scrape_notification_when_disabled(self) -> None:
        notifier = Notifier(apprise_urls=[], on_scrape_failure=False)
        notifier.send_scrape_notification(message="Metacritic is down")


class TestNotifierSend:
    """Test notification dispatch with mocked Apprise."""

    def test_send_with_mocked_apprise(self) -> None:
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="Test Game",
                platform="pc",
                metascore=85.0,
                user_score=8.0,
                slug="test-game",
                add_paused=False,
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
                title="Test",
                platform="pc",
                metascore=80.0,
                user_score=7.5,
                slug="test",
                add_paused=False,
            )

    def test_send_exception_caught(self) -> None:
        mock_apobj = MagicMock()
        mock_apobj.notify.side_effect = Exception("send failure")
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="Test",
                platform="pc",
                metascore=80.0,
                user_score=7.5,
                slug="test",
                add_paused=False,
            )


class TestNotifierFormat:
    """Notification body and title format tests."""

    def test_download_notification_format(self) -> None:
        """send_download_notification should format with:

        Apprise title: gamarr - <game title> (<platform>)
        Body:
            Status: Downloading (or Paused)
            Critic Score: <score> (<reviews> reviews)
            User Score: <score> (<reviews> reviews)
            Must Play: Yes/No (when provided)
            Genre: <genre1>, <genre2> (when provided)
            Release: <YYYY-MM-DD> (when provided)
            Link: https://www.metacritic.com/game/<slug>/
        """
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
                add_paused=False,
            )
            mock_apobj.notify.assert_called_once_with(
                title="gamarr - PRAGMATA (pc)",
                body=(
                    "Status: Downloading\n"
                    "Critic Score: 85.0 (50 reviews)\n"
                    "User Score: 8.8 (100 reviews)\n"
                    "Genre: Action, Adventure\n"
                    "Link: https://www.metacritic.com/game/pragmata/"
                ),
            )

    def test_download_notification_when_paused(self) -> None:
        """When add_paused=True, Status should show Paused."""
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"])
            notifier.send_download_notification(
                title="Elden Ring",
                platform="ps5",
                metascore=None,
                metascore_reviews=None,
                user_score=None,
                user_reviews=None,
                slug="elden-ring",
                genres=None,
                add_paused=True,
            )
            mock_apobj.notify.assert_called_once_with(
                title="gamarr - Elden Ring (ps5)",
                body=(
                    "Status: Paused\n"
                    "Critic Score: N/A\n"
                    "User Score: N/A\n"
                    "Link: https://www.metacritic.com/game/elden-ring/"
                ),
            )

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
                    "Link: https://www.metacritic.com/game/pragmata/"
                ),
            )

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

    def test_scrape_notification_format(self) -> None:
        """send_scrape_notification should format with gamarr prefix and the message."""
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"], on_scrape_failure=True)
            notifier.send_scrape_notification(message="Metacritic browse returned no games")
            mock_apobj.notify.assert_called_once_with(
                title="gamarr - Scraping Issue",
                body=(
                    "Metacritic browse returned no games\n"
                    "\n"
                    "This may indicate a Metacritic site change or network issue."
                ),
            )


class TestNotifierEdgeCases:
    """Edge cases for notification methods."""

    def test_failure_notification_sends_when_enabled(self) -> None:
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"], on_failure=True)
            notifier.send_failure_notification(title="Test", reason="Low score")
            mock_apobj.notify.assert_called_once()

    def test_error_notification_sends_when_enabled(self) -> None:
        mock_apobj = MagicMock()
        with patch.object(Notifier, "_init_apprise", return_value=mock_apobj):
            notifier = Notifier(apprise_urls=["json://localhost"], on_error=True)
            notifier.send_error_notification(error_message="Pipeline error")
            mock_apobj.notify.assert_called_once()
