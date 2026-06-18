"""Tests for the DODI source module."""

from __future__ import annotations

from gamarr.sources.dodi import _clean_dodi_title

# ── Title cleaning ──


def test_clean_dodi_title() -> None:
    """Strip DODI repack metadata from torrent titles."""
    assert _clean_dodi_title("Elden Ring-DODI") == "Elden Ring"
    assert _clean_dodi_title("Hades II-DODI") == "Hades II"
    assert _clean_dodi_title("Spider-Man.Remastered-DODI") == "Spider-Man Remastered"


# ── Source protocol ──


def test_dodi_source_implements_base_source() -> None:
    """DODISource implements the BaseSource protocol."""
    from gamarr.database import Database
    from gamarr.sources import BaseSource
    from gamarr.sources.dodi import DODISource

    source = DODISource(platform="pc", db=Database(":memory:"))
    assert isinstance(source, BaseSource)
    assert source.source_name == "dodi"
    assert source.platform == "pc"


def test_dodi_close() -> None:
    """DODISource.close() closes the underlying database."""
    from unittest.mock import MagicMock

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = MagicMock(spec=Database)
    source = DODISource(platform="pc", db=db)
    source.close()
    db.close.assert_called_once()


# ── Default URL ──


def test_dodi_default_url_is_hydralinks() -> None:
    """DODISource default feed_url now points to hydralinks.cloud JSON."""
    from gamarr.sources.dodi import DODISource

    source = DODISource()
    assert "hydralinks.cloud" in source._feed_url, f"Expected hydralinks.cloud, got {source._feed_url}"
    assert "1337x.to" not in source._feed_url and "x1337x" not in source._feed_url, (
        f"Torrent site URL should not be default: {source._feed_url}"
    )


# ── Playwright fetch ──


def test_fetch_sitemap_calls_parse_hydra_json() -> None:
    """fetch_sitemap parses JSON via _parse_hydra_json and stores results."""
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    source = DODISource(platform="pc", db=db, cache_pages_hours=0)

    mock_json = '{"downloads": [{"title": "Test-DODI", "url": "https://test/t", "uris": ["magnet:?test"]}]}'

    with patch.object(source, "_fetch_json_via_playwright", return_value=mock_json) as mock_fetch:
        source.fetch_sitemap(db)

        mock_fetch.assert_called_once()
        titles = db.get_all_source_titles("dodi")
        assert len(titles) == 1
        assert titles[0]["title"] == "Test"
        assert titles[0]["magnet"] == "magnet:?test"

    db.close()


def test_fetch_sitemap_cancelled_at_entry() -> None:
    """fetch_sitemap returns early when cancel_event is pre-set."""
    import threading
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    source = DODISource(platform="pc", db=db, cache_pages_hours=6)

    cancel_event = threading.Event()
    cancel_event.set()

    with patch.object(source, "_fetch_json_via_playwright") as mock_fetch:
        source.fetch_sitemap(db, cancel_event=cancel_event)
        mock_fetch.assert_not_called()

    db.close()


# ── JSON parsing ──

HYDRA_SAMPLE_JSON = """
{
  "downloads": [
    {
      "title": "Elden Ring-DODI",
      "url": "https://dodi-repacks.site/elden-ring",
      "uris": ["magnet:?xt=urn:btih:abc&dn=Elden+Ring+DODI"],
      "fileSize": "50 GB",
      "uploadDate": "2024-06-01T12:00:00.000Z"
    },
    {
      "title": "Hades II-DODI",
      "url": "https://dodi-repacks.site/hades-2",
      "uris": ["magnet:?xt=urn:btih:def&dn=Hades+II+DODI"],
      "fileSize": "15 GB",
      "uploadDate": "2024-06-15T12:00:00.000Z"
    }
  ]
}
"""


def test_parse_hydra_json_converts_to_source_titles() -> None:
    """_parse_hydra_json converts hydralinks.cloud JSON to source_titles format."""
    from gamarr.sources.dodi import _parse_hydra_json

    entries = _parse_hydra_json(HYDRA_SAMPLE_JSON)

    assert len(entries) == 2
    assert entries[0]["title"] == "Elden Ring"  # DODI suffix stripped
    assert entries[0]["magnet"] == "magnet:?xt=urn:btih:abc&dn=Elden+Ring+DODI"
    assert entries[0]["url"] == "https://dodi-repacks.site/elden-ring"
    assert entries[1]["title"] == "Hades II"
    assert entries[1]["magnet"] == "magnet:?xt=urn:btih:def&dn=Hades+II+DODI"
    assert entries[1]["url"] == "https://dodi-repacks.site/hades-2"


def test_parse_hydra_json_handles_missing_fields() -> None:
    """_parse_hydra_json skips entries with missing title and handles missing url/uris."""
    from gamarr.sources.dodi import _parse_hydra_json

    json_str = """
    {
      "downloads": [
        {"title": "Good Game-DODI", "url": "https://dodi/good", "uris": ["magnet:?good"]},
        {"url": "https://dodi/no-title", "uris": ["magnet:?no-title"]},
        {"title": "No URL-DODI", "uris": ["magnet:?no-url"]},
        {"title": "No Magnet-DODI", "url": "https://dodi/no-magnet"},
        {},
        {"title": "Bad URIS-DODI", "url": "https://dodi/bad-uris", "uris": "magnet:?bad"}
      ]
    }
    """
    entries = _parse_hydra_json(json_str)
    # 3 skipped: entry with no title, entry with no url, empty object
    # Bad URIS entry kept but with magnet=None (string uris rejected)
    assert len(entries) == 3
    assert entries[0]["title"] == "Good Game"
    assert entries[0]["url"] == "https://dodi/good"
    assert entries[0]["magnet"] == "magnet:?good"
    assert entries[1]["title"] == "No Magnet"
    assert entries[1]["url"] == "https://dodi/no-magnet"
    assert entries[1]["magnet"] is None
    assert entries[2]["title"] == "Bad URIS"
    assert entries[2]["magnet"] is None


def test_parse_hydra_json_returns_empty_for_invalid_input() -> None:
    """_parse_hydra_json returns [] for invalid JSON, non-dict, or missing downloads."""
    from gamarr.sources.dodi import _parse_hydra_json

    assert _parse_hydra_json("not valid json") == []
    assert _parse_hydra_json("[]") == []  # non-dict top-level
    assert _parse_hydra_json("null") == []
    assert _parse_hydra_json('{"downloads": null}') == []
    assert _parse_hydra_json('{"downloads": [null, "string", 42]}') == []


def test_fetch_sitemap_cache_hit_skips_fetch() -> None:
    """fetch_sitemap skips fetching when cache is valid and titles exist."""
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    db.set_sitemap_cache("dodi")
    db.rebuild_source_titles("dodi", [{"title": "Cached", "url": "https://cached/t", "magnet": None}])

    source = DODISource(platform="pc", db=db, cache_pages_hours=6)

    with patch.object(source, "_fetch_json_via_playwright") as mock_fetch:
        source.fetch_sitemap(db)
        mock_fetch.assert_not_called()

    db.close()


def test_fetch_sitemap_cache_empty_re_fetches() -> None:
    """fetch_sitemap re-fetches when cache is valid but titles are empty."""
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    db.set_sitemap_cache("dodi")  # cache valid but no source_titles rows

    source = DODISource(platform="pc", db=db, cache_pages_hours=6)

    mock_json = '{"downloads": [{"title": "Fetched-DODI", "url": "https://fetched/t", "uris": ["magnet:?fetched"]}]}'
    with patch.object(source, "_fetch_json_via_playwright", return_value=mock_json) as mock_fetch:
        source.fetch_sitemap(db)
        mock_fetch.assert_called_once()
    titles = db.get_all_source_titles("dodi")
    assert len(titles) == 1

    db.close()
