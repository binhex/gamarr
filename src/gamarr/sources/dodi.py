"""DODI repacks source for gamarr.

Scrapes 1377x.to/user/DODI/ for magnet links and stores them in the
source_titles database table for Metacritic-first matching.
"""

from __future__ import annotations

import re
import time
from typing import Any, cast

from loguru import logger

from gamarr.database import Database

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://1377x.to/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Priority": "u=0, i",
}

_DODI_SUFFIX_PATTERN = re.compile(r"[-.]DODI\s*$", re.IGNORECASE)
_DOT_TO_SPACE_PATTERN = re.compile(r"\.")
_MAGNET_PATTERN = re.compile(r'href="(magnet:\?xt=urn:btih:[^"]+)"')


def _build_page_url(feed_url: str, page: int) -> str:
    """Build a DODI user page URL for a given page number.

    Args:
        feed_url: Base feed URL (e.g. ``"https://1377x.to/user/DODI/"``).
        page: Page number (1-indexed).

    Returns:
        The full URL to the user page for DODI at the given page.
    """
    base = feed_url.rstrip("/")
    return f"{base}/{page}/"


def _extract_page_count(soup: Any) -> int:
    """Extract the total page count from a parsed 1377x user page.

    Args:
        soup: A BeautifulSoup object of the user page.

    Returns:
        The highest page number found in the pagination, or 1 if none.
    """
    total_pages = 1
    pagination = soup.select_one("div.pagination")
    if pagination:
        for link in pagination.find_all("a"):
            href = str(link.get("href", ""))
            match = re.search(r"/user/DODI/(\d+)/", href)
            if match:
                page_num = int(match.group(1))
                if page_num > total_pages:
                    total_pages = page_num
    return total_pages


def _parse_user_page(html: str, base_domain: str = "https://1377x.to") -> tuple[list[dict[str, str]], int]:
    """Parse a 1377x user page HTML and extract torrent entries + total pages.

    Args:
        html: Raw HTML content of the user page.
        base_domain: Base domain for prepending to relative URLs.

    Returns:
        Tuple of (entries, total_pages) where each entry has "title" and "url" keys.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    entries: list[dict[str, str]] = []

    for row in soup.select("table tbody tr"):
        # The name cell has two links: first is the category icon,
        # second is the actual torrent link (href contains /torrent/).
        name_cell = row.select_one("td.coll-1.name a[href*='/torrent/']")
        if name_cell and name_cell.get("href"):
            href = str(name_cell["href"])
            title = name_cell.get_text(strip=True)
            if href.startswith("/"):
                href = f"{base_domain}{href}"
            entries.append({"title": title, "url": href})

    return entries, _extract_page_count(soup)


def _extract_magnet_from_page(html: str) -> str | None:
    """Extract the magnet URI from a 1377x torrent detail page HTML.

    Args:
        html: Raw HTML content of the torrent detail page.

    Returns:
        The magnet URI, or None if not found.
    """
    match = _MAGNET_PATTERN.search(html)
    if match:
        return match.group(1).replace("&amp;", "&")
    return None


def _clean_dodi_title(raw_title: str) -> str:
    """Strip DODI repack metadata from a torrent title.

    Removes the trailing ``-DODI`` or ``.DODI`` suffix and normalizes
    dots to spaces.

    Args:
        raw_title: Raw torrent title, e.g. ``"Elden.Ring-DODI"``.

    Returns:
        Cleaned game name, e.g. ``"Elden Ring"``.
    """
    title = raw_title.strip()
    title = _DODI_SUFFIX_PATTERN.sub("", title)
    title = _DOT_TO_SPACE_PATTERN.sub(" ", title)
    return title.strip()


class DODISource:
    """DODI repacks source implementation.

    Uses the configured ``feed_url`` to scrape torrent listings and
    magnets.  The feed URL points to a DODI uploader page on a
    torrent site (e.g. 1377x.to/user/DODI/).
    """

    def __init__(
        self,
        platform: str = "pc",
        db: Database | None = None,
        cache_pages_hours: int = 6,
        feed_url: str = "https://1377x.to/user/DODI/",
    ) -> None:
        self._platform = platform
        self._cache_pages_hours = cache_pages_hours
        self._feed_url = feed_url.rstrip("/")
        self._base_domain = (
            self._feed_url[: self._feed_url.index("/", 9)] if "/" in self._feed_url[9:] else self._feed_url
        )
        self._fetcher = self._make_fetcher()
        self._db = db if db is not None else Database(":memory:")

    @staticmethod
    def _make_fetcher() -> Any:
        """Create a curl_cffi session for fetching pages.

        Uses curl's TLS fingerprint impersonation to bypass
        Cloudflare challenges that cloudscraper cannot handle.
        """
        import curl_cffi

        return curl_cffi.Session(impersonate="chrome131")

    @property
    def source_name(self) -> str:
        """Return ``\"dodi\"`` as the source identifier."""
        return "dodi"

    @property
    def platform(self) -> str:
        """Return the platform this source targets."""
        return self._platform

    def _fetch_page(self, url: str) -> str | None:
        """Fetch a page and return its text content, or None on failure.

        Args:
            url: The URL to fetch.

        Returns:
            The response text, or None if the request failed.
        """
        try:
            resp = self._fetcher.get(url, timeout=30, headers=_BROWSER_HEADERS)
            resp.raise_for_status()
            return cast("str", resp.text)
        except Exception as exc:
            logger.warning("Failed to fetch '{}': {}", url, exc)
            return None

    def _fetch_magnets_for_entries(self, entries: list[dict[str, str]]) -> list[dict[str, str | None]]:
        """Fetch detail pages for torrent entries and extract magnets.

        Args:
            entries: List of dicts with ``\"title\"`` and ``\"url\"`` keys.

        Returns:
            Entries with an added ``\"magnet\"`` key (may be None for
            torrents whose detail page couldn't be fetched).
        """
        results: list[dict[str, str | None]] = []
        for entry in entries:
            html = self._fetch_page(entry["url"])
            magnet = _extract_magnet_from_page(html) if html else None
            if magnet is None:
                logger.warning("No magnet found for '{}' at {}", entry["title"], entry["url"])
            results.append(
                {
                    "title": _clean_dodi_title(entry["title"]),
                    "url": entry["url"],
                    "magnet": magnet,
                }
            )
            time.sleep(1.5)
        return results

    def _fetch_remaining_pages(
        self,
        entries: list[dict[str, str]],
        total_pages: int,
    ) -> None:
        """Fetch remaining user pages beyond page 1 and append their entries.

        Mutates *entries* in-place by appending entries from subsequent pages.

        Args:
            entries: Entries already collected from page 1 (mutated in-place).
            total_pages: Total number of pages to fetch.
        """
        for page in range(2, total_pages + 1):
            page_html = self._fetch_page(_build_page_url(self._feed_url, page))
            if page_html:
                more_entries, _ = _parse_user_page(page_html, self._base_domain)
                entries.extend(more_entries)
            time.sleep(1.0)

    def fetch_sitemap(self, db: Database) -> None:
        """Scrape 1377x.to/user/DODI/ and rebuild the source_titles index.

        Handles pagination: fetches all pages on first run, checking the
        cache TTL first. When cache is valid and titles exist, skips the
        fetch entirely.

        Args:
            db: The database instance to store results in.
        """
        if self._cache_pages_hours > 0 and db.get_sitemap_cache("dodi", self._cache_pages_hours):
            if len(db.get_all_source_titles("dodi")) > 0:
                logger.info(
                    "DODI cache is still valid (TTL: {} hours) — skipping fetch",
                    self._cache_pages_hours,
                )
                return
            logger.info("DODI cache is valid but no titles indexed — re-fetching")

        first_page_url = _build_page_url(self._feed_url, 1)
        html = self._fetch_page(first_page_url)
        if html is None:
            logger.warning("Failed to fetch DODI user page — skipping")
            return

        entries, total_pages = _parse_user_page(html, self._base_domain)

        if total_pages > 1:
            self._fetch_remaining_pages(entries, total_pages)

        if not entries:
            logger.warning("No DODI torrent entries found — keeping existing cache")
            return

        magnet_entries = self._fetch_magnets_for_entries(entries)
        db.rebuild_source_titles("dodi", magnet_entries)
        db.set_sitemap_cache("dodi")
        logger.info("DODI index rebuilt: {} torrents indexed", len(magnet_entries))

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()
