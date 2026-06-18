"""DODI repacks source for gamarr.

Fetches the DODI repack catalog from hydralinks.cloud via headless
Chromium (Playwright) and stores entries in the source_titles database
table for Metacritic-first matching.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading

from loguru import logger

from gamarr.database import Database

_DODI_SUFFIX_PATTERN = re.compile(r"[-.]DODI\s*$", re.IGNORECASE)
_DOT_TO_SPACE_PATTERN = re.compile(r"\.")


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


def _parse_hydra_json(json_str: str) -> list[dict[str, str | None]]:
    """Parse hydralinks.cloud JSON format into source_titles entries.

    Each download entry is converted to ``{"title", "url", "magnet"}``
    format with the title cleaned via ``_clean_dodi_title``.  Entries
    missing a title or URL are silently skipped.

    Args:
        json_str: Raw JSON string from hydralinks.cloud/sources/dodi.json.

    Returns:
        List of dicts compatible with ``Database.rebuild_source_titles``.
    """
    import json

    if not isinstance(json_str, str):
        return []
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []

    results: list[dict[str, str | None]] = []
    downloads = data.get("downloads") or []
    for entry in downloads:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        if not title:
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url:
            continue
        uris = entry.get("uris")
        magnet = uris[0] if isinstance(uris, list) and uris else None
        clean_title = _clean_dodi_title(str(title))
        if not clean_title:
            continue
        results.append(
            {
                "title": clean_title,
                "url": str(url),
                "magnet": magnet,
            }
        )
    return results


class DODISource:
    """DODI repacks source implementation.

    Uses the configured ``feed_url`` to fetch the DODI repack catalog
    from hydralinks.cloud (or a compatible JSON endpoint) and store
    entries in the database for Metacritic-first matching.
    """

    def __init__(
        self,
        platform: str = "pc",
        db: Database | None = None,
        cache_pages_hours: int = 6,
        feed_url: str = "https://hydralinks.cloud/sources/dodi.json",
    ) -> None:
        self._platform = platform
        self._cache_pages_hours = cache_pages_hours
        self._feed_url = feed_url.rstrip("/")
        self._db = db if db is not None else Database(":memory:")

    @property
    def source_name(self) -> str:
        """Return ``\"dodi\"`` as the source identifier."""
        return "dodi"

    @property
    def platform(self) -> str:
        """Return the platform this source targets."""
        return self._platform

    @staticmethod
    def _fetch_json_via_playwright(url: str, cancel_event: threading.Event | None = None) -> str | None:
        """Fetch JSON from a Cloudflare-protected URL using Playwright Chromium.

        Launches a headless Chromium browser, navigates to *url*, waits
        for the page to settle (passing Cloudflare Turnstile automatically
        as a real browser), and returns the page body text.

        Args:
            url: The URL to fetch (typically hydralinks.cloud JSON endpoint).
            cancel_event: Optional event to signal cancellation.

        Returns:
            The response body text (JSON string), or None on failure.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright is not installed. Run: uv add playwright && uv run playwright install chromium")
            return None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    if cancel_event is not None and cancel_event.is_set():
                        return None
                    response = page.goto(url, wait_until="networkidle", timeout=30000)
                    if response is None or not response.ok:
                        logger.warning(
                            "Failed to fetch '{}': HTTP {}", url, response.status if response else "no response"
                        )
                        return None
                    body_text: str | None = response.text()
                    if body_text and body_text.strip():
                        return body_text.strip()
                    logger.warning("Empty response from '{}'", url)
                    return None
                finally:
                    browser.close()
        except Exception as exc:
            logger.warning("Playwright fetch failed for '{}': {}", url, exc)
            return None

    def fetch_sitemap(self, db: Database, cancel_event: threading.Event | None = None) -> None:
        """Fetch the DODI repack catalog from hydralinks.cloud and rebuild source_titles.

        Uses headless Chromium (Playwright) to navigate to the JSON
        endpoint, passing through Cloudflare Turnstile automatically.
        The JSON is parsed via ``_parse_hydra_json`` and stored via
        ``rebuild_source_titles``.

        When cache is valid and titles exist, skips the fetch entirely.
        Checks *cancel_event* at entry for prompt shutdown.

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

        logger.info("Fetching DODI repacks from {}...", self._feed_url)
        json_str = self._fetch_json_via_playwright(self._feed_url, cancel_event=cancel_event)
        if json_str is None:
            logger.warning("Failed to fetch DODI data from hydralinks.cloud")
            return

        if cancel_event is not None and cancel_event.is_set():
            return

        entries = _parse_hydra_json(json_str)
        if not entries:
            logger.warning("No DODI entries found in hydralinks.cloud response")
            return

        db.rebuild_source_titles("dodi", entries)
        db.set_sitemap_cache("dodi")
        logger.info("DODI index rebuilt: {} torrents from hydralinks.cloud", len(entries))

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()
