"""Tests for gamarr FitGirl RSS source."""

from __future__ import annotations

from typing import Any

from gamarr.sources.fitgirl import FitGirlSource, _clean_title


class TestTitleCleaning:
    """RSS title cleansing logic."""

    def test_clean_simple_title(self) -> None:
        assert _clean_title("Hades II [Repack]") == "Hades II"

    def test_clean_title_with_version(self) -> None:
        result = _clean_title("Elden Ring (v1.12 + DLCs, MULTi13) [Repack]")
        assert result == "Elden Ring"

    def test_clean_title_with_complex_version(self) -> None:
        result = _clean_title("Baldur's Gate 3 (v4.1.1.4.64194 Hotfix 28, MULTi17) [Repack]")
        assert result == "Baldur's Gate 3"

    def test_clean_title_with_multi_only(self) -> None:
        result = _clean_title("Some Game (MULTi5) [Repack]")
        assert result == "Some Game"

    def test_clean_title_no_repack(self) -> None:
        result = _clean_title("Game Name (v1.0) [Repack]")
        assert result == "Game Name"

    def test_clean_title_repack_no_version(self) -> None:
        result = _clean_title("Cyberpunk 2077 [Repack]")
        assert result == "Cyberpunk 2077"

    def test_clean_title_preserves_ampersand(self) -> None:
        result = _clean_title("Crash & Spyro [Repack]")
        assert result == "Crash & Spyro"

    def test_clean_title_apostrophe(self) -> None:
        result = _clean_title("Assassin's Creed [Repack]")
        assert result == "Assassin's Creed"

    def test_clean_title_strips_selective_download(self) -> None:
        result = _clean_title("Game Name (Selective Download) [Repack]")
        assert result == "Game Name"


class TestTitleCleaningRegression:
    """Regression tests for comma-separated and dash-separated metadata."""

    def test_clean_title_comma_version(self) -> None:
        """Strip comma-separated version metadata."""
        result = _clean_title(
            "Need for Speed: Payback – Deluxe Edition, v1.0.51.41148 HV / v1.0.51.15364 Non_HV + DLCs [Repack]"
        )
        assert result == "Need for Speed: Payback"

    def test_clean_title_deluxe_edition(self) -> None:
        """Strip Deluxe Edition suffix after comma."""
        result = _clean_title("FINAL FANTASY VII REBIRTH: Digital Deluxe Edition, v1.005 + 15 DLCs/Bonuses [Repack]")
        assert result == "FINAL FANTASY VII REBIRTH"

    def test_clean_title_edition_after_dash(self) -> None:
        """Strip edition info after dash."""
        result = _clean_title("Cyberpunk 2077 – Phantom Liberty Edition, v2.1 + DLC [Repack]")
        assert result == "Cyberpunk 2077"

    def test_clean_title_preserves_hyphenated_name(self) -> None:
        """Game names with hyphens should be preserved."""
        result = _clean_title("Assassin\x27s Creed Valhalla – Complete Edition, v1.7 + DLC [Repack]")
        assert result == "Assassin\x27s Creed Valhalla"

    def test_clean_title_enhanced_edition(self) -> None:
        """Strip Enhanced Edition suffix."""
        result = _clean_title("Game – Enhanced Edition, v1.0 + DLC [Repack]")
        assert result == "Game"

    def test_clean_title_collectors_edition(self) -> None:
        """Strip Collector\x27s Edition with apostrophe."""
        result = _clean_title("Game – Collector\x27s Edition, v1.0 + DLCs [Repack]")
        assert result == "Game"

    def test_clean_title_non_hv_hyphen(self) -> None:
        """Strip Non-HV (hyphen variant)."""
        result = _clean_title("Game, v1.0 Non-HV [Repack]")
        assert result == "Game"

    def test_clean_title_essence_edition(self) -> None:
        """Strip '– Essence Edition' suffix (not in original edition list)."""
        result = _clean_title("Magin: The Rat Project Stories \u2013 Essence Edition (v1.0, MULTi13) [Repack]")
        assert result == "Magin: The Rat Project Stories"

    def test_clean_title_bare_version_after_edition(self) -> None:
        """Strip bare version after edition (no trailing keywords)."""
        result = _clean_title("Game – Deluxe Edition, v1.0")
        assert result == "Game"


class TestExtractMagnetFromHtml:
    """Magnet link extraction from HTML content."""

    def test_extract_magnet_found(self) -> None:
        from gamarr.sources.fitgirl import _extract_magnet_from_html

        html = '<a href="magnet:?xt=urn:btih:abc123&dn=game">magnet</a>'
        result = _extract_magnet_from_html(html)
        assert result == "magnet:?xt=urn:btih:abc123&dn=game"

    def test_extract_magnet_not_found(self) -> None:
        from gamarr.sources.fitgirl import _extract_magnet_from_html

        html = "<p>No magnet here</p>"
        result = _extract_magnet_from_html(html)
        assert result is None

    def test_extract_magnet_empty(self) -> None:
        from gamarr.sources.fitgirl import _extract_magnet_from_html

        result = _extract_magnet_from_html("")
        assert result is None


class TestGetRssItems:
    """RSS item extraction from parsed XML dict."""

    def test_get_rss_items_multiple(self) -> None:
        from gamarr.sources.fitgirl import _get_rss_items

        feed = {"rss": {"channel": {"item": [{"title": "Game 1"}, {"title": "Game 2"}]}}}
        items = _get_rss_items(feed)
        assert items is not None
        assert len(items) == 2

    def test_get_rss_items_single_as_dict(self) -> None:
        from gamarr.sources.fitgirl import _get_rss_items

        feed = {"rss": {"channel": {"item": {"title": "Single Game"}}}}
        items = _get_rss_items(feed)
        assert items is not None
        assert len(items) == 1

    def test_get_rss_items_no_items(self) -> None:
        from gamarr.sources.fitgirl import _get_rss_items

        feed: dict = {"rss": {"channel": {}}}
        items = _get_rss_items(feed)
        assert items is None

    def test_get_rss_items_malformed(self) -> None:
        from gamarr.sources.fitgirl import _get_rss_items

        feed = {"rss": "not-a-dict"}
        items = _get_rss_items(feed)
        assert items is None


class TestFitGirlSource:
    """FitGirlSource construction and protocol conformance."""

    def test_implements_base_source(self) -> None:
        from gamarr.sources import BaseSource

        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        assert isinstance(source, BaseSource)

    def test_source_name(self) -> None:
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        assert source.source_name == "fitgirl"

    def test_platform(self) -> None:
        source = FitGirlSource("http://example.com/feed.xml", platform="pc", db_path=":memory:")
        assert source.platform == "pc"

    def test_fetch_new_returns_list(self) -> None:
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        entries = source.fetch_new()
        assert isinstance(entries, list)

    def test_fetch_new_http_error(self) -> None:
        """When requests.get raises, fetch_new returns empty list."""
        from unittest.mock import patch

        import requests

        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        with patch(
            "gamarr.sources.fitgirl.requests.get", side_effect=requests.exceptions.ConnectionError("mock error")
        ):
            entries = source.fetch_new()
        assert entries == []

    def test_fetch_new_with_valid_rss(self) -> None:
        """Mock a valid RSS feed response and verify parsing."""
        from unittest.mock import MagicMock, patch

        rss_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            "<item><title>Elden Ring (v1.12 + DLC, MULTi13) [Repack]</title>"
            "<link>https://fitgirl-repacks.site/elden-ring/</link>"
            "<description>magnet:?xt=urn:btih:abc123</description>"
            "</item>"
            "<item><title>Hades II [Repack]</title>"
            "<link>https://fitgirl-repacks.site/hades-ii/</link>"
            "<description>No magnet here</description>"
            "</item>"
            "</channel></rss>"
        )

        mock_resp = MagicMock()
        mock_resp.text = rss_xml
        mock_resp.raise_for_status.return_value = None

        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        with patch("gamarr.sources.fitgirl.requests.get", return_value=mock_resp):
            entries = source.fetch_new()
        assert len(entries) == 2
        assert entries[0].title == "Elden Ring"
        assert entries[0].source == "fitgirl"
        assert entries[0].magnet_url == "magnet:?xt=urn:btih:abc123"
        assert entries[1].title == "Hades II"

    def test_fetch_new_bad_xml(self) -> None:
        """When RSS returns invalid XML, fetch_new returns empty list."""
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.text = "not valid xml"
        mock_resp.raise_for_status.return_value = None

        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        with patch("gamarr.sources.fitgirl.requests.get", return_value=mock_resp):
            entries = source.fetch_new()
        assert entries == []

    def test_fetch_new_empty_channel(self) -> None:
        """When RSS has no items, fetch_new returns empty list."""
        from unittest.mock import MagicMock, patch

        rss_xml = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
        mock_resp = MagicMock()
        mock_resp.text = rss_xml
        mock_resp.raise_for_status.return_value = None

        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        with patch("gamarr.sources.fitgirl.requests.get", return_value=mock_resp):
            entries = source.fetch_new()
        assert entries == []

    def test_extract_magnet_fallback_failure(self) -> None:
        """When RSS description has no magnet and article fetch fails, magnet is empty."""
        from unittest.mock import MagicMock, patch

        import requests

        # RSS with no magnet in description
        rss_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            "<item><title>No Magnet Game [Repack]</title>"
            "<link>https://fitgirl-repacks.site/no-magnet/</link>"
            "<description>No magnet here at all</description>"
            "</item>"
            "</channel></rss>"
        )

        # First call (RSS fetch) succeeds, second call (article fetch) fails
        mock_rss_resp = MagicMock()
        mock_rss_resp.text = rss_xml
        mock_rss_resp.raise_for_status.return_value = None

        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        mock_get = MagicMock()
        mock_get.side_effect = [mock_rss_resp, requests.exceptions.ConnectionError("mock fail")]

        with patch("gamarr.sources.fitgirl.requests.get", mock_get):
            entries = source.fetch_new()
        assert len(entries) == 1
        assert entries[0].title == "No Magnet Game"
        assert entries[0].magnet_url == ""


class TestMagnetExtractionEdgeCases:
    """Magnet extraction edge cases."""

    def test_extract_magnet_fallback_fails_silently(self) -> None:
        """When the article page fetch fails, magnet extraction returns None."""
        from gamarr.sources.fitgirl import _extract_magnet_from_html

        result = _extract_magnet_from_html("<html>no magnet</html>")
        assert result is None


class TestFetchNewProccessing:
    """Additional fetch_new edge cases."""

    def test_fetch_new_skips_processed(self) -> None:
        """Entry already processed should be skipped."""
        from unittest.mock import MagicMock, patch

        # Create source, then simulate that the link is already in the DB
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        source._db.record_processed(
            source="fitgirl",
            source_title="http://fitgirl-repacks.site/elden-ring/",
            source_url="http://fitgirl-repacks.site/elden-ring/",
            game_title="Elden Ring",
            result="Passed",
        )

        rss_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            "<item><title>Elden Ring (v1.12 + DLC) [Repack]</title>"
            "<link>http://fitgirl-repacks.site/elden-ring/</link>"
            "<description>magnet:?xt=urn:btih:abc</description>"
            "</item>"
            "</channel></rss>"
        )
        mock_resp = MagicMock()
        mock_resp.text = rss_xml
        mock_resp.raise_for_status.return_value = None

        with patch("gamarr.sources.fitgirl.requests.get", return_value=mock_resp):
            entries = source.fetch_new()
        # The entry should be skipped since it's already in the DB
        assert len(entries) == 0
        source.close()


class TestBuildEntriesCategoryFilter:
    """RSS category-based filtering in _build_entries."""

    def test_build_entries_skips_news_posts(self) -> None:
        """Items with non-game categories like 'Updates Digest' should be skipped."""
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        items: list[dict[str, Any]] = [
            {
                "title": "Updates Digest for June 4",
                "link": "http://example.com/updates-digest",
                "category": "Updates Digest",
            },
            {
                "title": "Game Name [Repack]",
                "link": "http://example.com/game",
                "category": ["Lossless Repack", "3D"],
            },
        ]
        entries = source._build_entries(items)
        assert len(entries) == 1
        assert entries[0].title == "Game Name"

    def test_build_entries_skips_uncategorized(self) -> None:
        """Items with Uncategorized category should be skipped."""
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        items = [
            {
                "title": "Upcoming Repacks",
                "link": "http://example.com/upcoming",
                "category": "Uncategorized",
            },
            {
                "title": "Real Game [Repack]",
                "link": "http://example.com/real-game",
                "category": "Lossless Repack",
            },
        ]
        entries = source._build_entries(items)
        assert len(entries) == 1
        assert entries[0].title == "Real Game"

    def test_build_entries_no_category_still_included(self) -> None:
        """Items without a category should still be included (backward compat)."""
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        items = [
            {"title": "Some Game", "link": "http://example.com/game"},
        ]
        entries = source._build_entries(items)
        assert len(entries) == 1
        assert entries[0].title == "Some Game"

    def test_build_entries_all_news_only(self) -> None:
        """When all items are news posts, return empty list."""
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        items = [
            {"title": "Updates Digest", "link": "http://example.com/upd", "category": "Updates Digest"},
            {"title": "Upcoming Repacks", "link": "http://example.com/upcoming", "category": "Uncategorized"},
        ]
        entries = source._build_entries(items)
        assert len(entries) == 0


class TestCleanTitleDashVersion:
    """Regression tests for version numbers after en-dash."""

    def test_clean_dash_version_basic(self) -> None:
        """Version after en-dash should be stripped."""
        result = _clean_title("The 7th Guest Remake – v1.2.14011.0")
        assert result == "The 7th Guest Remake"

    def test_clean_dash_version_dlc(self) -> None:
        """Version + DLCs after en-dash should be stripped."""
        result = _clean_title("CarX Street: Deluxe Edition – v1.13.0 + 5 DLCs")
        assert result == "CarX Street"

    def test_clean_dash_version_simple(self) -> None:
        """Simple version after en-dash should be stripped."""
        result = _clean_title("A Bumpy Ride – v1.0.5")
        assert result == "A Bumpy Ride"

    def test_clean_dash_version_leading_zero(self) -> None:
        """Version with leading zero after en-dash should be stripped."""
        result = _clean_title("Realm of Ink – v0.18.04")
        assert result == "Realm of Ink"


class TestFitGirlSitemap:
    """FitGirl sitemap.xml indexing."""

    def test_parse_sitemap_extracts_titles(self) -> None:
        from gamarr.sources.fitgirl import _parse_sitemap

        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://fitgirl-repacks.site/elden-ring/</loc>
  </url>
  <url>
    <loc>https://fitgirl-repacks.site/baldurs-gate-3/</loc>
  </url>
</urlset>"""
        result = _parse_sitemap(xml)
        assert len(result) == 2
        assert result[0]["title"] == "Elden Ring"  # from URL slug
        assert result[0]["url"] == "https://fitgirl-repacks.site/elden-ring/"
        assert result[1]["title"] == "Baldurs Gate 3"

    def test_title_from_url_non_conforming_slug(self) -> None:
        """Non-conforming slugs should be returned as-is."""
        from gamarr.sources.fitgirl import _title_from_url

        # Slug with underscore — doesn't match [a-z0-9][a-z0-9-]*
        result = _title_from_url("https://fitgirl-repacks.site/some_underscore_game/")
        assert result == "some_underscore_game"

    def test_parse_sitemap_no_namespace(self) -> None:
        """Handle sitemap without namespace prefix gracefully."""
        from gamarr.sources.fitgirl import _parse_sitemap

        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset>
  <url>
    <loc>https://fitgirl-repacks.site/test-game/</loc>
  </url>
</urlset>"""
        result = _parse_sitemap(xml)
        # Without namespace, the xpath won't match — returns empty
        assert result == []

    def test_fetch_sitemap_resolves_index(self) -> None:
        """Reproduce: main sitemap is a <sitemapindex> with child sitemaps.

        The current code only parses <urlset> and returns 0 for
        <sitemapindex>. The fix must follow child sitemap references
        and parse their URLs.
        """
        from unittest.mock import MagicMock, patch

        from gamarr.sources.fitgirl import FitGirlSource

        index_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://fitgirl-repacks.site/post-sitemap.xml</loc>
  </sitemap>
</sitemapindex>"""
        child_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://fitgirl-repacks.site/elden-ring/</loc></url>
  <url><loc>https://fitgirl-repacks.site/baldurs-gate-3/</loc></url>
</urlset>"""

        source = FitGirlSource(db_path=":memory:", rss_url="http://example.com/feed")
        # Manually create a mock DB that stores what gets indexed
        mock_db = MagicMock()

        with patch("gamarr.sources.fitgirl.requests.get") as mock_get:

            def side_effect(url: str, **kwargs: object) -> MagicMock:
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                if "post-sitemap" in url:
                    resp.content = child_xml
                else:
                    resp.content = index_xml
                return resp

            mock_get.side_effect = side_effect

            source.fetch_sitemap(mock_db)

        # Should have indexed the child sitemap's URLs
        mock_db.rebuild_source_titles.assert_called_once()
        args = mock_db.rebuild_source_titles.call_args[0]
        assert args[0] == "fitgirl"
        assert len(args[1]) == 2
        assert args[1][0]["title"] == "Elden Ring"
