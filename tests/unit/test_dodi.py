"""Tests for the DODI source module."""

from __future__ import annotations

from gamarr.sources.dodi import (
    _build_page_url,
    _clean_dodi_title,
    _extract_magnet_from_page,
    _parse_user_page,
)

SAMPLE_USER_PAGE = """<!DOCTYPE html>
<html>
<body>
<div class="box-info">
  <h1>User uploads</h1>
</div>
<table>
  <tbody>
    <tr>
      <td class="coll-1 name">
        <a href="/torrent/1111/Elden-Ring-DODI/">Elden Ring-DODI</a>
      </td>
    </tr>
    <tr>
      <td class="coll-1 name">
        <a href="/torrent/2222/Hades-II-DODI/">Hades II-DODI</a>
      </td>
    </tr>
  </tbody>
</table>
<ul class="pagination">
  <li><a href="/user/DODI/1/">1</a></li>
  <li><a href="/user/DODI/2/">2</a></li>
</ul>
</body>
</html>"""

SAMPLE_DETAIL_PAGE = """<!DOCTYPE html>
<html>
<body>
<div class="torrent-detail-info">
  <h1>Elden Ring-DODI</h1>
  <ul class="download-links">
    <li><a href="magnet:?xt=urn:btih:abc123&amp;dn=Elden+Ring-DODI">Magnet</a></li>
  </ul>
</div>
</body>
</html>"""


def test_parse_user_page() -> None:
    """Parse 1337x user page and extract torrent entries + page count."""
    entries, total_pages = _parse_user_page(SAMPLE_USER_PAGE)
    assert len(entries) == 2
    assert entries[0]["title"] == "Elden Ring-DODI"
    assert entries[0]["url"] == "https://1337x.to/torrent/1111/Elden-Ring-DODI/"
    assert entries[1]["title"] == "Hades II-DODI"
    assert total_pages == 2


def test_extract_magnet_from_page() -> None:
    """Extract magnet URI from 1337x torrent detail page."""
    magnet = _extract_magnet_from_page(SAMPLE_DETAIL_PAGE)
    assert magnet == "magnet:?xt=urn:btih:abc123&dn=Elden+Ring-DODI"


def test_clean_dodi_title() -> None:
    """Strip DODI repack metadata from torrent titles."""
    assert _clean_dodi_title("Elden Ring-DODI") == "Elden Ring"
    assert _clean_dodi_title("Hades II-DODI") == "Hades II"
    assert _clean_dodi_title("Spider-Man.Remastered-DODI") == "Spider-Man Remastered"


def test_build_page_url() -> None:
    """Generate correct 1337x page URLs."""
    url = _build_page_url(1)
    assert url == "https://1337x.to/user/DODI/1/"
    url = _build_page_url(3)
    assert url == "https://1337x.to/user/DODI/3/"


def test_extract_magnet_no_match() -> None:
    """_extract_magnet_from_page returns None when no magnet link present."""
    html = "<html><body><p>No magnet here</p></body></html>"
    assert _extract_magnet_from_page(html) is None


def test_fetch_page_failure_logs_warning() -> None:
    """_fetch_page returns None and logs warning on request failure."""
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    source = DODISource(platform="pc", db=db)

    with patch.object(source, "_fetcher") as mock_fetcher:
        mock_fetcher.get.side_effect = Exception("Connection refused")
        with patch("gamarr.sources.dodi.logger") as mock_logger:
            result = source._fetch_page("https://1337x.to/user/DODI/1/")
            assert result is None
            mock_logger.warning.assert_called_once()


def test_fetch_sitemap_cache_valid_skips_fetch() -> None:
    """DODISource.fetch_sitemap skips fetch when cache is valid and titles exist."""
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    # Pre-populate with a title
    db.rebuild_source_titles(
        "dodi",
        [
            {"title": "Existing Game", "url": "https://1337x.to/torrent/0/"},
        ],
    )
    # Set cache as valid (recent timestamp)
    db.set_sitemap_cache("dodi")

    source = DODISource(platform="pc", db=db, cache_pages_hours=6)

    with patch.object(source, "_fetcher") as mock_fetcher:
        source.fetch_sitemap(db)
        # fetch_page should NOT be called since cache is valid
        mock_fetcher.get.assert_not_called()


def test_fetch_sitemap_cache_valid_but_empty() -> None:
    """DODISource re-fetches when cache is valid but no titles exist."""
    from unittest.mock import MagicMock, patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    # Set cache as valid but DON'T add titles
    db.set_sitemap_cache("dodi")

    source = DODISource(platform="pc", db=db, cache_pages_hours=6)

    with patch.object(source, "_fetcher") as mock_fetcher:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<html><body>
<table><tbody>
<tr><td class="coll-1 name"><a href="/torrent/1/Test-DODI/">Test-DODI</a></td></tr>
</tbody></table>
</body></html>"""
        mock_detail = MagicMock()
        mock_detail.status_code = 200
        mock_detail.text = """<html><body><a href="magnet:?xt=urn:btih:test">Magnet</a></body></html>"""
        mock_fetcher.get.side_effect = [mock_resp, mock_detail]

        source.fetch_sitemap(db)
        # Should re-fetch, then get detail page
        assert mock_fetcher.get.call_count == 2


def test_fetch_sitemap_first_page_fails() -> None:
    """DODISource.fetch_sitemap handles first page fetch failure gracefully."""
    from unittest.mock import patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    source = DODISource(platform="pc", db=db)

    with patch.object(source, "_fetcher") as mock_fetcher:
        mock_fetcher.get.side_effect = Exception("Network error")
        source.fetch_sitemap(db)

    # No titles should be stored
    titles = db.get_all_source_titles("dodi")
    assert len(titles) == 0


def test_fetch_sitemap_no_entries_found() -> None:
    """DODISource.fetch_sitemap handles empty torrent list gracefully."""
    from unittest.mock import MagicMock, patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    source = DODISource(platform="pc", db=db)

    with patch.object(source, "_fetcher") as mock_fetcher:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><p>No torrents</p></body></html>"
        mock_fetcher.get.return_value = mock_resp

        source.fetch_sitemap(db)

    # No titles should be stored
    titles = db.get_all_source_titles("dodi")
    assert len(titles) == 0


def test_dodi_source_implements_base_source() -> None:
    """DODISource implements the BaseSource protocol."""
    from gamarr.database import Database
    from gamarr.sources import BaseSource
    from gamarr.sources.dodi import DODISource

    source = DODISource(platform="pc", db=Database(":memory:"))
    assert isinstance(source, BaseSource)
    assert source.source_name == "dodi"
    assert source.platform == "pc"


def test_fetch_magnets_no_magnet_warning() -> None:
    """_fetch_magnets_for_entries logs warning when detail page has no magnet."""
    from unittest.mock import MagicMock, patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    source = DODISource(platform="pc", db=db)
    entries = [{"title": "NoMagnet-DODI", "url": "https://1337x.to/torrent/1/"}]

    with patch.object(source, "_fetcher") as mock_fetcher:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><p>No magnet link here</p></body></html>"
        mock_fetcher.get.return_value = mock_resp

        with patch("gamarr.sources.dodi.logger") as mock_logger:
            results = source._fetch_magnets_for_entries(entries)
            assert len(results) == 1
            assert results[0]["magnet"] is None
            mock_logger.warning.assert_called_once()


def test_dodi_close() -> None:
    """DODISource.close() closes the underlying database."""
    from unittest.mock import MagicMock

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = MagicMock(spec=Database)
    source = DODISource(platform="pc", db=db)
    source.close()
    db.close.assert_called_once()


def test_fetch_sitemap_success() -> None:
    """DODISource.fetch_sitemap stores scraped entries in the DB."""
    from unittest.mock import MagicMock, patch

    from gamarr.database import Database
    from gamarr.sources.dodi import DODISource

    db = Database(":memory:")
    source = DODISource(platform="pc", db=db)

    with patch.object(source, "_fetcher") as mock_fetcher:
        # Mock two pages: page 1 has 1 entry + pagination showing 2 pages
        mock_resp1 = MagicMock()
        mock_resp1.status_code = 200
        mock_resp1.text = """<html><body>
<table><tbody>
<tr><td class="coll-1 name"><a href="/torrent/1/Game-A-DODI/">Game A-DODI</a></td></tr>
</tbody></table>
<ul class="pagination"><li><a href="/user/DODI/1/">1</a></li><li><a href="/user/DODI/2/">2</a></li></ul>
</body></html>"""
        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.text = """<html><body>
<table><tbody>
<tr><td class="coll-1 name"><a href="/torrent/2/Game-B-DODI/">Game B-DODI</a></td></tr>
</tbody></table>
<ul class="pagination"><li><a href="/user/DODI/1/">1</a></li><li><a href="/user/DODI/2/">2</a></li></ul>
</body></html>"""
        # Mock detail pages for magnets
        mock_resp_detail = MagicMock()
        mock_resp_detail.status_code = 200
        mock_resp_detail.text = """<html><body><a href="magnet:?xt=urn:btih:abc">Magnet</a></body></html>"""

        # Call order: page 1, page 2, detail for entry 1, detail for entry 2
        mock_fetcher.get.side_effect = [mock_resp1, mock_resp2, mock_resp_detail, mock_resp_detail]

        source.fetch_sitemap(db)

    titles = db.get_all_source_titles("dodi")
    assert len(titles) == 2
    assert titles[0]["title"] == "Game A"
    assert titles[0]["magnet"] == "magnet:?xt=urn:btih:abc"
    assert titles[1]["title"] == "Game B"
    assert titles[1]["magnet"] == "magnet:?xt=urn:btih:abc"
