"""Tests for gamarr notifications module."""

from __future__ import annotations

import urllib.parse
from typing import Any
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

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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
            Metacritic: https://www.metacritic.com/game/<slug>
            YouTube: https://www.youtube.com/results?search_query=<title>+review
        """
        mock_apobj = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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
                    "Metacritic: https://www.metacritic.com/game/pragmata\n"
                    "YouTube: https://www.youtube.com/results?search_query=PRAGMATA+review"
                ),
            )

    def test_download_notification_when_paused(self) -> None:
        """When add_paused=True, Status should show Paused."""
        mock_apobj = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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
                    "Metacritic: https://www.metacritic.com/game/elden-ring\n"
                    "YouTube: https://www.youtube.com/results?search_query=Elden+Ring+review"
                ),
            )

    def test_download_notification_with_must_play_and_release(self) -> None:
        """When must_play and release_date are provided, they appear in the body."""
        mock_apobj = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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
                    "Metacritic: https://www.metacritic.com/game/pragmata\n"
                    "YouTube: https://www.youtube.com/results?search_query=PRAGMATA+review"
                ),
            )

    def test_download_notification_must_play_yes(self) -> None:
        """When must_play is True, shows 'Must Play: Yes'."""
        mock_apobj = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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

    def test_init_apprise_catches_import_error(self) -> None:
        """Exception during Apprise initialisation is caught and returns None."""
        with patch("apprise.Apprise", side_effect=ImportError("No module named 'apprise'")):
            notifier = Notifier(apprise_urls=["json://localhost"])
            assert notifier._apprise_md is None
            assert notifier._apprise_text is None

    def test_send_returns_early_when_no_apprise(self) -> None:
        """_send is a no-op when Apprise is not initialised."""
        notifier = Notifier(apprise_urls=[])
        notifier._send("Test", "Body")  # should not raise

    def test_failure_notification_guard_when_no_urls(self) -> None:
        """send_failure_notification returns early when on_failure=True but no URLs."""
        notifier = Notifier(apprise_urls=[], on_failure=True)
        notifier.send_failure_notification(title="Game", reason="Low score")  # no raise

    def test_error_notification_guard_when_no_urls(self) -> None:
        """send_error_notification returns early when on_error=True but no URLs."""
        notifier = Notifier(apprise_urls=[], on_error=True)
        notifier.send_error_notification(error_message="Pipeline error")  # no raise

    def test_scrape_notification_guard_when_no_urls(self) -> None:
        """send_scrape_notification returns early when on_scrape_failure=True but no URLs."""
        notifier = Notifier(apprise_urls=[], on_scrape_failure=True)
        notifier.send_scrape_notification(message="Metacritic is down")  # no raise


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

    def test_is_markdown_service_malformed_url(self) -> None:
        """_is_markdown_service handles values that cannot be split on ://."""
        assert Notifier._is_markdown_service("not-a-url") is False
        mock_val: Any = 123
        assert Notifier._is_markdown_service(mock_val) is False

    def test_maybe_upgrade_ntfy_malformed_url(self) -> None:
        """_maybe_upgrade_ntfy returns the original value when split is impossible."""
        assert Notifier._maybe_upgrade_ntfy("not-a-url") == "not-a-url"
        nfy_val: Any = 123
        assert Notifier._maybe_upgrade_ntfy(nfy_val) == nfy_val


class TestSourceLink:
    """Tests for download-source link in notification body."""

    def test_source_link_fitgirl_appears_in_body(self) -> None:
        """When source_name='fitgirl' and source_url provided, FitGirl link appears."""
        mock_apobj = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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
            assert "Fitgirl: https://fitgirl-repacks.site/pragmata" in body

    def test_source_link_freegog_appears_in_body(self) -> None:
        """source_name='freegog' \u2192 FreeGog label."""
        mock_apobj = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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
            assert "Freegog: https://freegogpcgames.com/pragmata" in body

    def test_source_link_omitted_when_none(self) -> None:
        """When source_name and source_url are None, no source line in body."""
        mock_apobj = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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
            assert "Fitgirl" not in body
            assert "Freegog" not in body

    def test_source_link_omitted_when_only_name(self) -> None:
        """When only source_name provided (no URL), line is omitted."""
        mock_apobj = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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
            assert "Fitgirl" not in body

    def test_source_link_ordering_metacritic_source_youtube(self) -> None:
        """Source link appears between Metacritic and YouTube links in body."""
        mock_apobj = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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
            source_pos = body.index("Fitgirl:")
            youtube_pos = body.index("YouTube:")
            assert metacritic_pos < source_pos < youtube_pos


class TestMarkdownNotification:
    """Tests for markdown-formatted notification bodies."""

    def test_markdown_links_use_bracket_syntax(self) -> None:
        """When a markdown-capable URL is used, links use [label](url)."""
        mock_md = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_md if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
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
            body = mock_md.notify.call_args[1]["body"]
            assert "[Metacritic](https://www.metacritic.com/game/pragmata)" in body
            assert "[Fitgirl](https://fitgirl-repacks.site/pragmata)" in body
            assert "[YouTube](https://www.youtube.com/results?search_query=PRAGMATA+review)" in body
            assert "Metacritic: http" not in body  # no bare text link for metacritic

    def test_markdown_omits_source_when_none(self) -> None:
        """Markdown mode omits source line when source_name is None."""
        mock_md = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_md if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
            notifier = Notifier(apprise_urls=["discord://webhook/token"])
            notifier.send_download_notification(
                title="PRAGMATA",
                platform="pc",
                metascore=85.0,
                user_score=8.8,
                slug="pragmata",
                add_paused=False,
            )
            body = mock_md.notify.call_args[1]["body"]
            assert "Fitgirl" not in body
            assert "Freegog" not in body

    def test_ntfy_url_gets_format_upgraded(self) -> None:
        """ntfy URL without format=markdown gets it appended on init."""
        mock_apobj = MagicMock()

        def _mock_init(urls: list[str]) -> MagicMock | None:
            return mock_apobj if urls else None

        with patch.object(Notifier, "_init_apprise", side_effect=_mock_init):
            notifier = Notifier(apprise_urls=["ntfy://host/topic"])
            # The ntfy URL in md_urls should have format=markdown appended
            assert any("format=markdown" in u for u in notifier._md_urls)

    def test_sends_to_both_instances(self) -> None:
        """When both markdown and text URLs are configured, both instances receive body."""
        mock_md = MagicMock()
        mock_text = MagicMock()

        def _fake_init(urls: list[str]) -> MagicMock | None:
            if urls and urls[0].startswith("discord"):
                return mock_md
            if urls:
                return mock_text
            return None

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
