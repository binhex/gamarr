"""FitGirl repacks RSS source for gamarr.

Fetches the FitGirl RSS feed, parses new entries, cleans game titles,
and extracts magnet links.
"""

from __future__ import annotations

import re
from typing import Any

import requests
from loguru import logger
from xmltodict import parse as parse_xml

from gamarr.models import GameEntry

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_TECH_PAREN_PATTERN = re.compile(r"\s*\(.*?(?:v?\d[\d.]*|MULTi|Selective|Repack).*?\)", re.IGNORECASE)
_REPACK_TAG_PATTERN = re.compile(r"\s*\[Repack\]", re.IGNORECASE)

# Strip edition suffixes after en-dash, colon, or comma
_EDITION_PATTERN = re.compile(
    r"(?:\s*[-–]\s*|\s*:\s*|,\s*)(?:(?:Digital\s+)?Deluxe\s+Edition|"
    r"Complete\s+Edition|Enhanced\s+Edition|Game\s+of\s+the\s+Year\s+Edition|"
    r"Gold\s+Edition|Platinum\s+Edition|Ultimate\s+Edition|Premium\s+Edition|"
    r"Collectors?(?:'s)?\s+Edition|Limited\s+Edition|Special\s+Edition|"
    r"Standard\s+Edition|Phantom\s+Liberty\s+Edition)"
    r"\b(?=\s*[,\d]|\s*$)",
    re.IGNORECASE,
)

# Strip comma-separated version/DLC/bonus metadata
# Also handles bare version strings like ", v1.0" when no trailing keywords
_VERSION_COMMA_PATTERN = re.compile(
    r",\s*v?\d[\d.,\s\w/\+]+(?:\+?\s*DLCs?|Bonuses|HV|Non[_-]?HV).*",
    re.IGNORECASE,
)

# Strip bare version strings after comma (no trailing keywords required)
# e.g. ", v1.0" where edition was already stripped
_BARE_VERSION_PATTERN = re.compile(r",\s*v?\d[\d.]*.*", re.IGNORECASE)

_MAGNET_PATTERN = re.compile(r"(magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s\"'<>]*)")

_CONNECT_TIMEOUT = 30.0
_READ_TIMEOUT = 60.0


def _clean_title(raw_title: str) -> str:
    """Strip FitGirl repack metadata from an RSS title, returning the canonical game name.

    Args:
        raw_title: Raw RSS title, e.g. ``"Elden Ring (v1.12 + DLCs, MULTi13) [Repack]"``.

    Returns:
        Cleaned game name, e.g. ``"Elden Ring"``.
    """
    title = raw_title.strip()
    title = _REPACK_TAG_PATTERN.sub("", title)
    title = _TECH_PAREN_PATTERN.sub("", title)
    title = _EDITION_PATTERN.sub("", title).strip()
    title = _VERSION_COMMA_PATTERN.sub("", title).strip()
    title = _BARE_VERSION_PATTERN.sub("", title).strip()
    return title.strip()


def _extract_magnet_from_html(html_content: str) -> str | None:
    """Extract the first magnet link found in *html_content*.

    Args:
        html_content: Raw HTML page content.

    Returns:
        The first magnet URI found, or ``None``.
    """
    match = _MAGNET_PATTERN.search(html_content)
    if match:
        return match.group(1).strip()
    return None


def _get_rss_items(feed: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract RSS item list from the parsed XML dictionary.

    Args:
        feed: Parsed RSS feed as a nested dict (from xmltodict).

    Returns:
        List of item dicts, or ``None`` if the structure is invalid.
    """
    try:
        channel = feed.get("rss", {}).get("channel", {})
        items = channel.get("item")
        if items is None:
            return None
        if isinstance(items, dict):
            return [items]
        if isinstance(items, list):
            return items
    except (AttributeError, TypeError):
        pass
    return None


class FitGirlSource:
    """FitGirl RSS source implementation.

    Args:
        rss_url: URL of the FitGirl RSS feed.
        platform: Platform identifier (default ``"pc"``).
        db_path: Path for the deduplication database.
            ``":memory:"`` uses an in-memory SQLite DB.
    """

    def __init__(
        self,
        rss_url: str,
        platform: str = "pc",
        db_path: str = ":memory:",
    ) -> None:
        self._rss_url = rss_url
        self._platform = platform
        from gamarr.database import Database

        self._db = Database(db_path)

    @property
    def source_name(self) -> str:
        """Return ``"fitgirl"`` as the source identifier."""
        return "fitgirl"

    @property
    def platform(self) -> str:
        """Return the platform this source targets."""
        return self._platform

    def fetch_new(self) -> list[GameEntry]:
        """Fetch the RSS feed and return entries not yet in the history DB.

        Returns:
            List of new :class:`GameEntry` objects.  Empty when the feed
            is unreachable or has no new entries.
        """
        logger.debug("Fetching FitGirl RSS feed from '{}'", self._rss_url)
        try:
            resp = requests.get(
                self._rss_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Failed to fetch FitGirl RSS feed: {}", exc)
            return []

        try:
            feed = parse_xml(resp.text)
        except Exception as exc:
            logger.warning("Failed to parse FitGirl RSS XML: {}", exc)
            return []

        items = _get_rss_items(feed)
        if items is None:
            logger.warning("No RSS items found in FitGirl feed response.")
            return []

        entries = self._build_entries(items)
        logger.info("FitGirl RSS: found {} new entries", len(entries))
        return entries

    def _build_entries(self, items: list[dict[str, Any]]) -> list[GameEntry]:
        """Convert RSS items to GameEntries, skipping already processed ones."""
        entries: list[GameEntry] = []
        for item in items:
            raw_title = item.get("title", "")
            link = item.get("link", "")
            if not raw_title or not link:
                continue
            if self._db.is_processed(self.source_name, link):
                logger.debug("Skipping already processed entry: '{}'", raw_title)
                continue
            cleaned_title = _clean_title(raw_title)
            magnet_url = self._extract_magnet(item, link)
            entries.append(
                GameEntry(
                    title=cleaned_title,
                    source_title=raw_title,
                    source=self.source_name,
                    platform=self._platform,
                    magnet_url=magnet_url or "",
                    source_url=link,
                )
            )
        return entries

    def _extract_magnet(self, item: dict[str, Any], link: str) -> str | None:
        """Attempt to extract a magnet link from the RSS item or its linked page.

        Args:
            item: The RSS item dictionary.
            link: The item's link URL.

        Returns:
            A magnet URI string, or ``None``.
        """
        description = item.get("description", "")
        if isinstance(description, str):
            magnet = _extract_magnet_from_html(description)
            if magnet:
                return magnet

        try:
            resp = requests.get(
                link,
                headers={"User-Agent": _USER_AGENT},
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
            resp.raise_for_status()
            magnet = _extract_magnet_from_html(resp.text)
            if magnet:
                return magnet
        except requests.RequestException as exc:
            logger.warning("Failed to fetch FitGirl article page '{}': {}", link, exc)

        return None

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()
