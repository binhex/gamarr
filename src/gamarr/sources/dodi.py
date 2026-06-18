"""DODI repacks source for gamarr.

Scrapes 1377x.to/user/DODI/ for magnet links and stores them in the
source_titles database table for Metacritic-first matching.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

if TYPE_CHECKING:
    import threading

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


def _extract_page_count(soup: Any, feed_url: str = "https://1377x.to/user/DODI/") -> int:
    """Extract the total page count from a parsed 1377x user page.

    Derives the pagination path from *feed_url* so that custom feed
    URLs with different paths work correctly.

    Args:
        soup: A BeautifulSoup object of the user page.
        feed_url: Base feed URL used to derive the pagination pattern.

    Returns:
        The highest page number found in the pagination, or 1 if none.
    """
    total_pages = 1
    pagination = soup.select_one("div.pagination")
    if pagination:
        # Build page pattern from feed_url: https://host/path/ → /path/(\d+)/
        parsed = urlparse(feed_url.rstrip("/") + "/")
        path_pattern = re.escape(parsed.path) + r"(\d+)/"
        for link in pagination.find_all("a"):
            href = str(link.get("href", ""))
            match = re.search(path_pattern, href)
            if match:
                page_num = int(match.group(1))
                if page_num > total_pages:
                    total_pages = page_num
    return total_pages


def _parse_user_page(
    html: str,
    base_domain: str = "https://1377x.to",
    feed_url: str = "https://1377x.to/user/DODI/",
) -> tuple[list[dict[str, str]], int]:
    """Parse a 1377x user page HTML and extract torrent entries + total pages.

    Args:
        html: Raw HTML content of the user page.
        base_domain: Base domain for prepending to relative URLs.
        feed_url: Base feed URL for pagination extraction.

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

    return entries, _extract_page_count(soup, feed_url)


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
        """Create a requests Session for fetching pages.

        1377x.to has no Cloudflare protection, so a plain
        requests session is sufficient. No impersonation needed.
        """
        import requests

        return requests.Session()

    @property
    def source_name(self) -> str:
        """Return ``\"dodi\"`` as the source identifier."""
        return "dodi"

    @property
    def platform(self) -> str:
        """Return the platform this source targets."""
        return self._platform

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return True if *exc* is a transient error worth retrying.

        Retries on:
        - Timeouts and connection errors (network issues)
        - HTTP 5xx status codes (server errors)

        Does NOT retry on:
        - HTTP 4xx (client errors like 403, 404 — permanent)
        """
        exc_name = type(exc).__name__
        if exc_name in ("Timeout", "ConnectionError", "ConnectTimeout", "ReadTimeout"):
            return True
        if hasattr(exc, "response") and exc.response is not None:
            return bool(exc.response.status_code >= 500)
        return False

    @staticmethod
    def _wait_or_cancelled(seconds: float, cancel_event: threading.Event | None) -> bool:
        """Wait for *seconds* or until *cancel_event* is set.

        Uses ``cancel_event.wait(timeout=seconds)`` when available so
        that the wait is immediately interruptible on shutdown.

        Returns:
            True if cancelled (cancel_event was set), False otherwise.
        """
        if cancel_event is not None:
            return cancel_event.wait(timeout=seconds)
        time.sleep(seconds)
        return False

    def _fetch_page(
        self,
        url: str,
        max_retries: int = 3,
        cancel_event: threading.Event | None = None,
    ) -> str | None:
        """Fetch a page and return its text content, or None on failure.

        Retries up to *max_retries* times with exponential backoff to
        handle transient HTTP errors (502, timeouts) that 1377x.to
        intermittently returns.  Permanent errors (403, 404, etc.)
        are not retried.  Checks *cancel_event* before each attempt
        and during backoff for prompt shutdown.

        Args:
            url: The URL to fetch.
            max_retries: Max retry attempts (default 3).
            cancel_event: Optional event to signal cancellation.

        Returns:
            The response text, or None if cancelled or all retries failed.
        """
        for attempt in range(max_retries):
            if cancel_event is not None and cancel_event.is_set():
                return None
            try:
                resp = self._fetcher.get(url, timeout=30, headers=_BROWSER_HEADERS)
                resp.raise_for_status()
                return cast("str", resp.text)
            except Exception as exc:
                if self._is_retryable(exc) and attempt < max_retries - 1:
                    wait = 2**attempt
                    logger.debug("Retry {}/{} for '{}' in {}s: {}", attempt + 1, max_retries, url, wait, exc)
                    if self._wait_or_cancelled(wait, cancel_event):
                        return None
                else:
                    logger.warning("Failed to fetch '{}' after {} attempt(s): {}", url, attempt + 1, exc)
                    return None
        return None  # pragma: no cover (unreachable — all loop paths return)

    def _fetch_magnets_for_entries(
        self,
        entries: list[dict[str, str]],
        cancel_event: threading.Event | None = None,
    ) -> list[dict[str, str | None]]:
        """Fetch detail pages for torrent entries and extract magnets.

        Args:
            entries: List of dicts with ``\"title\"`` and ``\"url\"`` keys.
            cancel_event: Optional event to signal cancellation.

        Returns:
            Entries with an added ``\"magnet\"`` key (may be None for
            torrents whose detail page couldn't be fetched).
        """
        results: list[dict[str, str | None]] = []
        for entry in entries:
            if cancel_event is not None and cancel_event.is_set():
                break
            html = self._fetch_page(entry["url"], cancel_event=cancel_event)
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
            if self._wait_or_cancelled(1.5, cancel_event):
                break
        return results

    def _fetch_remaining_pages(
        self,
        entries: list[dict[str, str]],
        total_pages: int,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Fetch remaining user pages beyond page 1 and append their entries.

        Mutates *entries* in-place by appending entries from subsequent pages.
        Checks *cancel_event* between pages for prompt shutdown.

        Args:
            entries: Entries already collected from page 1 (mutated in-place).
            total_pages: Total number of pages to fetch.
            cancel_event: Optional event to signal cancellation.
        """
        for page in range(2, total_pages + 1):
            if cancel_event is not None and cancel_event.is_set():
                break
            page_html = self._fetch_page(_build_page_url(self._feed_url, page), cancel_event=cancel_event)
            if page_html:
                more_entries, _ = _parse_user_page(page_html, self._base_domain, self._feed_url)
                entries.extend(more_entries)
            if self._wait_or_cancelled(1.0, cancel_event):
                break

    def fetch_sitemap(self, db: Database, cancel_event: threading.Event | None = None) -> None:
        """Scrape 1377x.to/user/DODI/ and rebuild the source_titles index.

        Handles pagination: fetches all pages on first run, checking the
        cache TTL first. When cache is valid and titles exist, skips the
        fetch entirely.  Checks *cancel_event* between phases for prompt
        shutdown.

        Args:
            db: The database instance to store results in.
            cancel_event: Optional event to signal cancellation.
        """
        if cancel_event is not None and cancel_event.is_set():
            return
        if self._cache_pages_hours > 0 and db.get_sitemap_cache("dodi", self._cache_pages_hours):
            if len(db.get_all_source_titles("dodi")) > 0:
                logger.info(
                    "DODI cache is still valid (TTL: {} hours) — skipping fetch",
                    self._cache_pages_hours,
                )
                return
            logger.info("DODI cache is valid but no titles indexed — re-fetching")

        if cancel_event is not None and cancel_event.is_set():
            return
        first_page_url = _build_page_url(self._feed_url, 1)
        html = self._fetch_page(first_page_url, cancel_event=cancel_event)
        if html is None:
            logger.warning("Failed to fetch DODI user page — skipping")
            return

        if cancel_event is not None and cancel_event.is_set():
            return
        entries, total_pages = _parse_user_page(html, self._base_domain, self._feed_url)

        if total_pages > 1:
            self._fetch_remaining_pages(entries, total_pages, cancel_event=cancel_event)

        if not entries:
            logger.warning("No DODI torrent entries found — keeping existing cache")
            return

        if cancel_event is not None and cancel_event.is_set():
            return
        magnet_entries = self._fetch_magnets_for_entries(entries, cancel_event=cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            return
        db.rebuild_source_titles("dodi", magnet_entries)
        db.set_sitemap_cache("dodi")
        logger.info("DODI index rebuilt: {} torrents indexed", len(magnet_entries))

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()
