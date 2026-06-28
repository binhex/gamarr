"""Tests for gamarr FitGirl RSS source."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gamarr.sources.fitgirl import FitGirlSource, _clean_title

if TYPE_CHECKING:
    from pathlib import Path


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

    def test_accepts_shared_database(self) -> None:
        """FitGirlSource must accept a pre-existing Database instance.

        When run_acquisition creates its own Database('db') and then
        FitGirlSource creates another, two engines contend for the same
        SQLite file.  FitGirlSource should accept a shared Database
        instance so only one engine exists per database file.
        """
        from gamarr.database import Database
        from gamarr.sources.fitgirl import FitGirlSource

        shared_db = Database(":memory:")
        source = FitGirlSource(
            feed_url="http://example.com/feed.xml",
            platform="pc",
            db=shared_db,
        )
        # The source should use the provided instance, not create its own
        assert source._db is shared_db
        source.close()
        shared_db.close()


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

        source = FitGirlSource(db_path=":memory:", feed_url="http://example.com/feed")
        # Manually create a mock DB that stores what gets indexed
        mock_db = MagicMock()
        mock_db.get_sitemap_cache.return_value = False  # Force cache miss

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

    def test_resolve_sitemap_deduplicates_urls(self) -> None:
        """Reproduce the UNIQUE constraint crash.

        When multiple child sitemaps contain the same game URL,
        _resolve_sitemap must deduplicate to prevent an IntegrityError
        on INSERT into the source_titles table.
        """
        from gamarr.sources.fitgirl import _resolve_sitemap

        index_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://fitgirl-repacks.site/post-sitemap1.xml</loc>
  </sitemap>
  <sitemap>
    <loc>https://fitgirl-repacks.site/post-sitemap2.xml</loc>
  </sitemap>
</sitemapindex>"""
        child_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://fitgirl-repacks.site/game-one/</loc></url>
  <url><loc>https://fitgirl-repacks.site/shared-game/</loc></url>
</urlset>"""

        class FakeResponse:
            def __init__(self, url: str) -> None:
                self.content = child_xml
                self.url = url

            def raise_for_status(self) -> None:
                pass

        results = _resolve_sitemap(index_xml, fetcher=FakeResponse)

        # Both child sitemaps return the same 2 URLs.  Without
        # deduplication the result would have 4 entries.  With
        # deduplication it should have 2 (no duplicates).
        urls = [r["url"] for r in results]
        assert len(urls) == 2, f"Expected 2 deduplicated URLs, got {len(urls)}: {urls}"
        assert len(set(urls)) == 2, f"Duplicate URLs still present: {urls}"


class TestFilterGameUrls:
    """_filter_game_urls strips non-game entries from sitemap lists."""

    def test_filter_excludes_homepage_tags_and_authors(self) -> None:
        """Homepage, tag, author and category URLs should be removed."""
        from gamarr.sources.fitgirl import _filter_game_urls

        entries = [
            {"title": "fitgirl-repacks.site", "url": "https://fitgirl-repacks.site/"},
            {"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"},
            {"title": "Action", "url": "https://fitgirl-repacks.site/tag/action/"},
            {"title": "Admin Fitgirl", "url": "https://fitgirl-repacks.site/author/admin-fitgirl/"},
            {"title": "Cyberpunk 2077", "url": "https://fitgirl-repacks.site/cyberpunk-2077/"},
            {"title": "RPG", "url": "https://fitgirl-repacks.site/category/rpg/"},
            {"title": "Baldur's Gate 3", "url": "https://fitgirl-repacks.site/baldurs-gate-3/"},
        ]

        filtered = _filter_game_urls(entries)

        # Game entries should be kept
        urls = [e["url"] for e in filtered]
        assert "https://fitgirl-repacks.site/elden-ring/" in urls
        assert "https://fitgirl-repacks.site/cyberpunk-2077/" in urls
        assert "https://fitgirl-repacks.site/baldurs-gate-3/" in urls

        # Non-game entries should be removed
        assert "https://fitgirl-repacks.site/" not in urls
        assert "https://fitgirl-repacks.site/tag/action/" not in urls
        assert "https://fitgirl-repacks.site/author/admin-fitgirl/" not in urls
        assert "https://fitgirl-repacks.site/category/rpg/" not in urls

        assert len(filtered) == 3, f"Expected 3 game entries, got {len(filtered)}"


class TestSitemapFetchOnEmpty:
    """fetch_sitemap should re-fetch when source_titles is empty despite valid cache."""

    def test_fetch_sitemap_when_cache_valid_but_titles_empty(self, tmp_path: Path) -> None:
        """When source_titles is empty, fetch_sitemap should re-fetch even if cache is valid."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.sources.fitgirl import FitGirlSource

        db = Database(str(tmp_path / "test.db"))
        source = FitGirlSource(
            feed_url="http://example.com/feed",
            db=db,
            cache_pages_hours=6,
        )

        # Set a valid sitemap cache entry (simulating a previous fetch)
        db.set_sitemap_cache("fitgirl")

        # Verify source_titles is empty
        titles_before = db.get_all_source_titles("fitgirl")
        assert len(titles_before) == 0, "source_titles should start empty"

        # Mock the HTTP request to return a valid sitemap
        sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://fitgirl-repacks.site/elden-ring/</loc>
  </url>
  <url>
    <loc>https://fitgirl-repacks.site/baldurs-gate-3/</loc>
  </url>
</urlset>"""

        with patch("gamarr.sources.fitgirl.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.content = sitemap_xml
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            source.fetch_sitemap(db)

        # After the fix, source_titles should be populated (re-fetched)
        titles_after = db.get_all_source_titles("fitgirl")
        assert len(titles_after) > 0, (
            "fetch_sitemap should re-fetch when source_titles is empty, "
            f"even if cache is valid. Got {len(titles_after)} titles."
        )
        titles = {t["title"] for t in titles_after}
        assert "Elden Ring" in titles
        assert "Baldurs Gate 3" in titles
        source.close()
        db.close()


def test_fetch_sitemap_accepts_cancel_event() -> None:
    """FitGirlSource.fetch_sitemap accepts cancel_event keyword."""
    import threading
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.fitgirl import FitGirlSource

    db = Database(":memory:")
    source = FitGirlSource(
        feed_url="https://fitgirl-repacks.site/feed/",
        db=db,
        cache_pages_hours=0,
    )

    # The pipeline passes cancel_event=cancel_event — this must not raise
    cancel_event = threading.Event()
    from unittest.mock import MagicMock

    with patch("gamarr.sources.fitgirl.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"<?xml version='1.0' encoding='UTF-8'?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n</urlset>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        # Should not raise TypeError
        source.fetch_sitemap(db, cancel_event=cancel_event)


def test_fetch_sitemap_cancelled_returns_early() -> None:
    """FitGirlSource.fetch_sitemap returns early when cancel_event is set."""
    import threading
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.fitgirl import FitGirlSource

    db = Database(":memory:")
    source = FitGirlSource(
        feed_url="https://fitgirl-repacks.site/feed/",
        db=db,
        cache_pages_hours=0,
    )

    cancel_event = threading.Event()
    cancel_event.set()  # Pre-cancel

    with patch("gamarr.sources.fitgirl.requests.get") as mock_get:
        source.fetch_sitemap(db, cancel_event=cancel_event)
        # Should NOT make any HTTP requests
        mock_get.assert_not_called()


def test_fetch_and_store_sitemap_stores_titles() -> None:
    """_fetch_and_store_sitemap fetches sitemap and stores titles."""
    from unittest.mock import MagicMock, patch

    from gamarr.database import Database
    from gamarr.sources.fitgirl import FitGirlSource

    db = Database(":memory:")
    source = FitGirlSource(
        feed_url="https://fitgirl-repacks.site/feed/",
        db=db,
        cache_pages_hours=0,
    )

    with patch("gamarr.sources.fitgirl.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"""<?xml version='1.0' encoding='UTF-8'?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://fitgirl-repacks.site/game-a/</loc></url>
  <url><loc>https://fitgirl-repacks.site/game-b/</loc></url>
</urlset>"""
        mock_get.return_value = mock_resp

        source._fetch_and_store_sitemap(db)

    titles = db.get_all_source_titles("fitgirl")
    assert len(titles) == 2
    assert titles[0]["title"] == "Game A"
    assert titles[1]["title"] == "Game B"

    db.close()


def test_fetch_sitemap_cancelled_after_cache_check() -> None:
    """fetch_sitemap returns early when cancel_event is set after cache check."""
    import threading
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.fitgirl import FitGirlSource

    db = Database(":memory:")
    source = FitGirlSource(
        feed_url="https://fitgirl-repacks.site/feed/",
        db=db,
        cache_pages_hours=6,
    )

    # Pre-populate a cache entry to force cache-hit path
    db.set_sitemap_cache("fitgirl")
    db.rebuild_source_titles(
        "fitgirl",
        [{"title": "Existing Game", "url": "https://fitgirl-repacks.site/existing-game/", "magnet": None}],
    )

    cancel_event = threading.Event()
    # Cancel should be checked after cache check too
    cancel_event.set()

    with patch("gamarr.sources.fitgirl.requests.get") as mock_get:
        source.fetch_sitemap(db, cancel_event=cancel_event)
        mock_get.assert_not_called()

    db.close()


def test_fetch_and_store_sitemap_passes_verify_false() -> None:
    """_fetch_and_store_sitemap must pass verify=False to bypass self-signed cert."""
    from unittest.mock import MagicMock, patch

    from gamarr.database import Database
    from gamarr.sources.fitgirl import FitGirlSource

    db = Database(":memory:")
    source = FitGirlSource(
        feed_url="https://fitgirl-repacks.site/feed/",
        db=db,
        cache_pages_hours=0,
    )

    with patch("gamarr.sources.fitgirl.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"""<?xml version='1.0' encoding='UTF-8'?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://fitgirl-repacks.site/game-a/</loc></url>
</urlset>"""
        mock_get.return_value = mock_resp

        source._fetch_and_store_sitemap(db)

    # The main sitemap request must pass verify=False
    assert mock_get.call_count >= 1, f"Expected at least 1 call to requests.get, got {mock_get.call_count}"
    first_call_kwargs = mock_get.call_args_list[0][1]
    assert first_call_kwargs.get("verify") is False, (
        f"Expected verify=False in sitemap request, got {first_call_kwargs}"
    )

    db.close()


def test_fetch_child_sitemaps_handles_exception() -> None:
    """_fetch_child_sitemaps logs warning when one child sitemap fetch fails."""
    from unittest.mock import MagicMock, patch

    from gamarr.sources.fitgirl import _fetch_child_sitemaps

    child_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://fitgirl-repacks.site/game-a/</loc></url>
</urlset>"""

    # Fetcher that fails for the first URL, succeeds for the second
    class PartialFetcher:
        def __init__(self) -> None:
            self.call_count = 0

        def __call__(self, url: str) -> MagicMock:
            self.call_count += 1
            if self.call_count == 1:
                raise ConnectionError("timeout")
            resp = MagicMock()
            resp.content = child_xml
            resp.raise_for_status = MagicMock()
            return resp

    with patch("gamarr.sources.fitgirl.logger") as mock_logger:
        results = _fetch_child_sitemaps(["http://fail.com", "http://ok.com"], PartialFetcher())

    # Should still get results from the successful child sitemap
    assert len(results) == 1
    assert results[0]["url"] == "https://fitgirl-repacks.site/game-a/"
    # Should have logged the failure warning
    mock_logger.warning.assert_called_once()
    assert "Failed to fetch child sitemap" in mock_logger.warning.call_args[0][0]


def test_resolve_sitemap_index_without_fetcher() -> None:
    """_resolve_sitemap returns [] for sitemapindex when fetcher is None."""
    from gamarr.sources.fitgirl import _resolve_sitemap

    index_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/child.xml</loc></sitemap>
</sitemapindex>"""
    results = _resolve_sitemap(index_xml, fetcher=None)
    assert results == []


def test_resolve_sitemap_unrecognized_root_tag() -> None:
    """_resolve_sitemap returns [] when the root tag is unrecognized."""
    from gamarr.sources.fitgirl import _resolve_sitemap

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<notasitemap xmlns="http://example.com/ns">
  <something/>
</notasitemap>"""
    results = _resolve_sitemap(xml)
    assert results == []


def test_fetch_and_store_sitemap_handles_request_exception() -> None:
    """_fetch_and_store_sitemap logs warning when requests.get fails."""
    from unittest.mock import patch

    import requests

    from gamarr.database import Database
    from gamarr.sources.fitgirl import FitGirlSource

    db = Database(":memory:")
    source = FitGirlSource(
        feed_url="https://fitgirl-repacks.site/feed/",
        db=db,
        cache_pages_hours=0,
    )

    with (
        patch("gamarr.sources.fitgirl.requests.get", side_effect=requests.exceptions.ConnectionError("nope")),
        patch("gamarr.sources.fitgirl.logger") as mock_logger,
    ):
        source._fetch_and_store_sitemap(db)

    # Should log the warning
    mock_logger.warning.assert_called_once()
    assert "Failed to fetch FitGirl sitemap" in mock_logger.warning.call_args[0][0]
    # No titles should have been stored
    assert len(db.get_all_source_titles("fitgirl")) == 0

    db.close()


def test_fetch_sitemap_cache_hit_skips() -> None:
    """fetch_sitemap returns early when cache is valid and titles exist."""
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.fitgirl import FitGirlSource

    db = Database(":memory:")
    source = FitGirlSource(
        feed_url="https://fitgirl-repacks.site/feed/",
        db=db,
        cache_pages_hours=6,
    )

    # Pre-populate cache and titles
    db.set_sitemap_cache("fitgirl")
    db.rebuild_source_titles(
        "fitgirl",
        [{"title": "Existing Game", "url": "https://fitgirl-repacks.site/existing-game/", "magnet": None}],
    )

    with patch("gamarr.sources.fitgirl.requests.get") as mock_get:
        source.fetch_sitemap(db)
        # Should NOT make any HTTP requests — cache hit
        mock_get.assert_not_called()

    db.close()


def test_fetch_sitemap_updates_cache_on_failure() -> None:
    """Cache must be refreshed after a failed fetch to prevent infinite retry loop.

    Bug: when the sitemap cache expires and the upstream is unreachable,
    _fetch_and_store_sitemap catches the exception but never calls
    set_sitemap_cache. The stale timestamp remains expired, so every
    subsequent cycle retries the fetch — hitting the offline site forever.
    """
    import datetime
    from unittest.mock import patch

    import requests

    from gamarr.database import Database, SitemapCache
    from gamarr.sources.fitgirl import FitGirlSource

    db = Database(":memory:")
    source = FitGirlSource(
        feed_url="https://fitgirl-repacks.site/feed/",
        db=db,
        cache_pages_hours=6,
    )

    # Pre-populate titles so the "no titles indexed" re-fetch path won't
    # trigger after the cache is refreshed.
    db.rebuild_source_titles(
        "fitgirl",
        [{"title": "Existing Game", "url": "https://fitgirl-repacks.site/existing/", "magnet": None}],
    )

    # Set an expired cache (7 hours ago, TTL = 6 hours).
    old_time = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=7)
    with db._session() as session:
        row = session.get(SitemapCache, "fitgirl")
        if row is None:
            session.add(SitemapCache(source="fitgirl", cached_at=old_time.isoformat()))
        else:
            row.cached_at = old_time.isoformat()
        session.commit()

    # Verify cache is expired before we start.
    assert not db.get_sitemap_cache("fitgirl", 6), "Cache must be expired before test"

    # First call: cache expired → tries fetch → fails.
    with patch("gamarr.sources.fitgirl.requests.get", side_effect=requests.exceptions.ConnectionError("offline")):
        source.fetch_sitemap(db)

    # KEY ASSERTION — the fix: cache must now be fresh, so the next cycle
    # skips the fetch instead of hammering the offline site.
    assert db.get_sitemap_cache("fitgirl", 6), (
        "BUG: Cache timestamp was not updated after a failed fetch attempt. "
        "When the site is offline, this causes an infinite retry loop — "
        "every scheduler cycle hits the unreachable upstream forever."
    )

    # Second call within TTL must NOT make HTTP requests.
    with patch("gamarr.sources.fitgirl.requests.get") as mock_get:
        source.fetch_sitemap(db)
        mock_get.assert_not_called()

    source.close()
    db.close()
