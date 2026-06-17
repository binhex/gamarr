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
    entries, total_pages = _parse_user_page(SAMPLE_USER_PAGE, base_url="https://1337x.to/user/DODI/1/")
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
    assert titles[0]["title"] == "Game A-DODI"
    assert titles[0]["magnet"] == "magnet:?xt=urn:btih:abc"
    assert titles[1]["title"] == "Game B-DODI"
    assert titles[1]["magnet"] == "magnet:?xt=urn:btih:abc"
