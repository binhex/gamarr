"""FreeGOG PC Games download source for gamarr.

Fetches the FreeGOG A-Z game list, parses game entries,
cleans titles, and extracts magnet links from game pages.
"""

from __future__ import annotations

import base64
import re
from html import unescape
from typing import TYPE_CHECKING

import requests
from loguru import logger

from gamarr.database import Database

if TYPE_CHECKING:
    import threading

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Edition suffixes to strip (adapted from fitgirl.py with Sunset Edition added)
_EDITION_PATTERN = re.compile(
    r"(?:\s*[-–—]\s*|\s*:\s*|,\s*)(?:"
    r"(?:Digital\s+)?Deluxe\s+Edition|"
    r"Complete\s+Edition|Enhanced\s+Edition|Essence\s+Edition|"
    r"Definitive\s+Edition|Anniversary\s+Edition|Legendary\s+Edition|"
    r"Game\s+of\s+the\s+Year\s+Edition|"
    r"Gold\s+Edition|Platinum\s+Edition|Ultimate\s+Edition|"
    r"Premium\s+Edition|Collectors?(?:'s)?\s+Edition|"
    r"Limited\s+Edition|Special\s+Edition|Standard\s+Edition|"
    r"Phantom\s+Liberty\s+Edition|"
    r"Sunset\s+Edition|"
    r"GOTY(?:\s+Edition)?|Game\s+of\s+the\s+Year(?:\s+Edition)?)"
    r"\b",
    re.IGNORECASE,
)

# Strip DLC counts: +3DLC, + DLCs, etc.
_DLC_PATTERN = re.compile(r"\s*\+\s*\d*\s*DLCs?\b", re.IGNORECASE)

# Strip version numbers: v1.0, v1.2.3a, v3.0.60151
_VERSION_PATTERN = re.compile(r"\s+v\d[\d.]*[a-z]?\b")

# Strip year tags: 2022(rc3)
_YEAR_PATTERN = re.compile(r"\s+\d{4}\(rc\d+\)")

# A-Z page section and link extraction patterns
_SECTION_PATTERN = re.compile(
    r'<section[^>]*\bid="gd-az-([^"]+)"[^>]*\bclass="[^"]*\bgd-az-section\b[^"]*"[^>]*>(.*?)</section>',
    re.DOTALL | re.IGNORECASE,
)
_AZ_LINK_PATTERN = re.compile(
    r'<a\s+href="(https://freegogpcgames\.com/\d+/[^"/]+/)"[^>]*>\s*<span[^>]*>([^<]+)</span>',
)

# Pattern to extract base64-encoded magnet URL from game page
_MAGNET_URL_PATTERN = re.compile(r"url=v1\.([A-Za-z0-9\-_=]+)\.[A-Za-z0-9\-_]+")


def _clean_freegog_title(raw_title: str) -> str:
    """Strip FreeGOG metadata from a raw game title.

    Strips in order: edition suffixes, DLC counts, version numbers,
    and year tags.

    Args:
        raw_title: Raw title from the FreeGOG A-Z page, e.g.
            ``"Sea of Stars: Sunset Edition v3.0.60151 +3DLC"``.

    Returns:
        Cleaned canonical game name, e.g. ``"Sea of Stars"``.
    """
    title = raw_title.strip()
    title = _EDITION_PATTERN.sub("", title).strip()
    title = _DLC_PATTERN.sub("", title).strip()
    title = _VERSION_PATTERN.sub("", title).strip()
    title = _YEAR_PATTERN.sub("", title).strip()
    return title


def _parse_freegog_az_page(html: str) -> list[dict[str, str]]:
    """Parse the FreeGOG /game-list/ A-Z page HTML.

    Extracts game title from the first ``<span>`` inside ``<a>`` tags
    within ``<section class="gd-az-section">`` elements.

    Args:
        html: Raw HTML content of the A-Z page.

    Returns:
        List of ``{"title": ..., "url": ..., "letter": ...}`` dicts,
        deduplicated by URL.  *letter* is the section id (e.g. ``"a"``,
        ``"num"``).  Titles are cleaned via ``_clean_freegog_title``.
    """
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for letter_id, section_html in _SECTION_PATTERN.findall(html):
        # Normalize: "a", "b", ..., "z", "num" for #
        letter = letter_id.casefold()
        for match in _AZ_LINK_PATTERN.finditer(section_html):
            href = match.group(1)
            if href in seen_urls:
                continue
            seen_urls.add(href)
            raw_title = unescape(match.group(2))
            cleaned = _clean_freegog_title(raw_title)
            results.append({"title": cleaned, "url": href, "letter": letter})

    return results


def _extract_magnet_from_freegog_page(html: str) -> str | None:
    """Extract a magnet link from a FreeGOG game page.

    FreeGOG magnet links are base64-encoded in gateway URLs:
    ``https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.<BASE64>.sig``

    The base64 portion uses URL-safe characters (``-`` and ``_`` instead of
    ``+`` and ``/``).

    Args:
        html: Raw HTML content of a game page.

    Returns:
        Decoded magnet URI, or ``None`` if no magnet was found or
        decoding failed.
    """
    match = _MAGNET_URL_PATTERN.search(html)
    if not match:
        return None

    encoded = match.group(1)
    # Convert URL-safe base64 to standard base64
    encoded = encoded.replace("-", "+").replace("_", "/")
    # Add padding if needed
    missing_padding = len(encoded) % 4
    if missing_padding:
        encoded += "=" * (4 - missing_padding)

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (ValueError, TypeError, UnicodeDecodeError):
        return None

    if decoded.startswith("magnet:"):
        return decoded
    return None


class FreeGOGSource:
    """FreeGOG PC Games A-Z page source implementation.

    Fetches the FreeGOG game list, indexes new games, and extracts
    magnet links.

    Args:
        platform: Platform identifier (default ``"pc"``).
        db_path: Path for the deduplication database.
            ``":memory:"`` uses an in-memory SQLite DB.
        db: Optional shared Database instance. If provided, *db_path* is
            ignored.
        cache_pages_hours: TTL for the sitemap cache in hours.
    """

    def __init__(
        self,
        platform: str = "pc",
        db_path: str = ":memory:",
        db: Database | None = None,
        cache_pages_hours: int = 6,
    ) -> None:
        self._platform = platform
        self._cache_pages_hours = cache_pages_hours

        if db is not None:
            self._db = db
        else:
            self._db = Database(db_path)

    @property
    def source_name(self) -> str:
        """Return ``"freegog"`` as the source identifier."""
        return "freegog"

    @property
    def platform(self) -> str:
        """Return the platform this source targets."""
        return self._platform

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()

    @staticmethod
    def _build_existing_urls(
        db: Database,
    ) -> dict[str, str | None]:
        """Build a URL-to-magnet dict from existing source_titles."""
        existing = db.get_all_source_titles("freegog")
        existing_urls: dict[str, str | None] = {}
        for e in existing:
            e_url = e.get("url")
            if e_url is not None:
                existing_urls[e_url] = e.get("magnet")
        return existing_urls

    @staticmethod
    def _fetch_and_store_game(
        db: Database,
        entry: dict[str, str],
        existing_urls: dict[str, str | None],
    ) -> bool:
        """Fetch a single FreeGOG game page and store its magnet.

        Returns True if a new game was indexed, False otherwise.
        """
        try:
            game_resp = requests.get(
                entry["url"],
                timeout=30,
                headers={"User-Agent": _USER_AGENT},
            )
            game_resp.raise_for_status()
            magnet = _extract_magnet_from_freegog_page(game_resp.text)
        except requests.RequestException as exc:
            logger.warning(
                "Failed to fetch FreeGOG game page '{}': {}",
                entry["url"],
                exc,
            )
            return False

        # Atomically delete old row and insert new row in a single transaction
        # via upsert_source_title to avoid a crash window between two separate commits.
        db.upsert_source_title(
            source="freegog",
            title=entry["title"],
            url=entry["url"],
            magnet=magnet,
        )
        return True

    def _index_az_page(self, db: Database) -> None:
        """Fetch the FreeGOG A-Z page and index new games.

        Cross-references against existing ``source_titles`` entries and
        only fetches game pages for new URLs.

        Args:
            db: The database instance to store results in.
        """
        url = "https://freegogpcgames.com/game-list/"
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            az_entries = _parse_freegog_az_page(resp.text)

            existing_urls = self._build_existing_urls(db)

            new_count = 0
            known_count = 0
            total_entries = len(az_entries)

            for entry in az_entries:
                if entry["url"] in existing_urls and existing_urls[entry["url"]] is not None:
                    # Skip only if we already have a valid magnet for this URL.
                    # Re-fetch entries with magnet=None (broken from earlier buggy indexing).
                    known_count += 1
                    continue

                if self._fetch_and_store_game(db, entry, existing_urls):
                    new_count += 1

            db.set_sitemap_cache("freegog")
            if new_count > 0:
                logger.info(
                    "FreeGOG: {} new games found ({} entries checked, {} already known)",
                    new_count,
                    total_entries,
                    known_count,
                )
            else:
                logger.info(
                    "FreeGOG: all {} entries already known — nothing new",
                    total_entries,
                )
        except requests.RequestException as exc:
            logger.warning("Failed to fetch FreeGOG A-Z page: {}", exc)
            db.set_sitemap_cache("freegog")

    def fetch_sitemap(
        self,
        db: Database,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Fetch the FreeGOG game list and index new games.

        Checks the sitemap cache first. Re-fetches even when cache is
        valid if ``source_titles`` is empty (e.g. after an initial fetch
        that returned no parsed titles). Checks *cancel_event* at entry
        for prompt shutdown.

        Args:
            db: The database instance to store results in.
            cancel_event: Optional event to signal cancellation.
        """
        if cancel_event is not None and cancel_event.is_set():
            logger.debug("FreeGOG sitemap fetch skipped \u2014 cancelled")
            return
        if self._cache_pages_hours > 0 and db.get_sitemap_cache("freegog", self._cache_pages_hours):
            if len(db.get_all_source_titles("freegog")) > 0:
                expiry = db.get_sitemap_cache_expiry("freegog", self._cache_pages_hours) or "unknown"
                logger.info(
                    "FreeGOG cache is still valid \u2014 expires at {} \u2014 skipping fetch",
                    expiry,
                )
                return
            logger.info(
                "FreeGOG cache is valid but no titles indexed \u2014 re-fetching A-Z page",
            )

        self._index_az_page(db)
