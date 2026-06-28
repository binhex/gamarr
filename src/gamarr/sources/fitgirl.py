"""FitGirl repacks RSS source for gamarr.

Fetches the FitGirl RSS feed, parses new entries, cleans game titles,
and extracts magnet links.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

import requests
import urllib3
from loguru import logger

from gamarr.database import Database

# FitGirl uses a self-signed certificate. Disable SSL verification warnings process-wide
# so log output is not spammed with InsecureRequestWarning on every request.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if TYPE_CHECKING:
    import threading

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_TECH_PAREN_PATTERN = re.compile(r"\s*\(.*?(?:v?\d[\d.]*|MULTi|Selective|Repack).*?\)", re.IGNORECASE)
_REPACK_TAG_PATTERN = re.compile(r"\s*\[(?:FitGirl\s+)?Repack\]", re.IGNORECASE)

# Strip edition suffixes after en-dash, colon, or comma
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
    r"GOTY(?:\s+Edition)?|Game\s+of\s+the\s+Year(?:\s+Edition)?)"
    r"\b(?=\s*[,\d–—-]|\s*$)",
    re.IGNORECASE,
)

# Strip comma-separated version/DLC/bonus metadata
# Also handles bare version strings like ", v1.0" when no trailing keywords
_VERSION_COMMA_PATTERN = re.compile(
    r"(?:,\s*|\s*[–—-]\s*)v?\d[\d.,\s\w/\+]+(?:\+?\s*DLCs?|Bonuses|HV|Non[_-]?HV).*",
    re.IGNORECASE,
)

# Strip bare version strings after comma or dash (no trailing keywords required)
# e.g. ", v1.0" or " – v1.0" where edition was already stripped
_BARE_VERSION_PATTERN = re.compile(r"(?:,\s*|\s*[–—-]\s*)v?\d[\d.]*.*", re.IGNORECASE)

# RSS categories that indicate a non-game entry (blog/news posts)
_MAGNET_PATTERN = re.compile(r"(magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s\"'<>]*)")


def _title_from_url(url: str) -> str:
    """Extract a display title from a FitGirl repack URL slug.

    ``https://fitgirl-repacks.site/elden-ring/``→ ``Elden Ring``

    Args:
        url: The full URL of a repack page.

    Returns:
        A human-readable title derived from the URL slug.
    """
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    # Heuristic: if the slug is mostly alphanumeric + hyphens, title-case it
    if re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        return slug.replace("-", " ").title()
    return slug


def _parse_sitemap(xml_content: bytes) -> list[dict[str, str]]:
    """Parse a ``<urlset>`` sitemap XML into a list of ``{title, url}`` dicts.

    Args:
        xml_content: Raw XML bytes of a sitemap urlset.

    Returns:
        List of dicts with ``title`` and ``url`` keys.
    """
    root = ET.fromstring(xml_content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    results: list[dict[str, str]] = []
    for url_elem in root.findall("sm:url", ns):
        loc = url_elem.find("sm:loc", ns)
        if loc is not None and loc.text:
            url = loc.text.strip()
            title = _title_from_url(url)
            results.append({"title": title, "url": url})
    return results


_NON_GAME_URL_PATTERNS = ("/tag/", "/author/", "/category/")


def _filter_game_urls(titles: list[dict[str, str]]) -> list[dict[str, str]]:
    """Filter a sitemap entry list to only game URLs.

    Removes entries whose URL is the site root (homepage) or contains
    non-game path segments like ``/tag/``, ``/author/``, ``/category/``.

    Args:
        titles: List of ``{"title": ..., "url": ...}`` dicts from a sitemap.

    Returns:
        Filtered list containing only game-page entries.
    """
    results: list[dict[str, str]] = []
    for entry in titles:
        url = entry["url"]
        # Skip the root URL (homepage)
        if url.rstrip("/") == "https://fitgirl-repacks.site":
            continue
        # Skip tag, author, and category pages
        if any(pattern in url for pattern in _NON_GAME_URL_PATTERNS):
            continue
        results.append(entry)
    return results


def _parse_sitemap_index(xml_content: bytes) -> list[str]:
    """Parse a ``<sitemapindex>`` XML and return child sitemap URLs.

    Args:
        xml_content: Raw XML bytes of a sitemap index.

    Returns:
        List of child sitemap ``<loc>`` URLs.
    """
    root = ET.fromstring(xml_content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: list[str] = []
    for sitemap_elem in root.findall("sm:sitemap", ns):
        loc = sitemap_elem.find("sm:loc", ns)
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def _fetch_child_sitemaps(
    child_urls: list[str],
    fetcher: Any,
) -> list[dict[str, str]]:
    """Fetch and parse child sitemaps, deduplicating by URL."""
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for child_url in child_urls:
        try:
            resp = fetcher(child_url)
            resp.raise_for_status()
            for entry in _parse_sitemap(resp.content):
                if entry["url"] not in seen_urls:
                    seen_urls.add(entry["url"])
                    results.append(entry)
        except Exception as exc:
            logger.warning("Failed to fetch child sitemap '{}': {}", child_url, exc)
    return results


def _resolve_sitemap(
    xml_content: bytes,
    fetcher: Any = None,
) -> list[dict[str, str]]:
    """Resolve a sitemap that may be a ``<urlset>`` or ``<sitemapindex>``.

    For a ``<urlset>``, extracts game title/url pairs directly.
    For a ``<sitemapindex>``, fetches each child sitemap via *fetcher*
    and aggregates all URL entries.  *fetcher* must be a callable that
    accepts a URL and returns a response with a ``.content`` attribute.

    Args:
        xml_content: Raw XML bytes of the sitemap.
        fetcher: Callable ``(url) -> response`` for fetching child
            sitemaps.  Required when the root is ``<sitemapindex>``.

    Returns:
        List of ``{"title": ..., "url": ...}`` dicts.
    """
    root = ET.fromstring(xml_content)
    tag = root.tag
    local_tag = tag.split("}", 1)[-1] if "}" in tag else tag

    if local_tag == "urlset":
        return _parse_sitemap(xml_content)

    if local_tag == "sitemapindex":
        if fetcher is None:
            return []
        return _fetch_child_sitemaps(_parse_sitemap_index(xml_content), fetcher)

    return []


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


class FitGirlSource:
    """FitGirl sitemap source implementation.

    The FitGirl source contributes the *sitemap title index* used by
    Metacritic-first matching: pending Metacritic games are matched
    against titles discovered here.  No RSS iteration is performed.

    Args:
        feed_url: Feed/source URL for this source (unused at runtime, retained
            for backwards-compatible configuration).
        platform: Platform identifier (default ``"pc"``).
        db_path: Path for the deduplication database.
            ``":memory:"`` uses an in-memory SQLite DB.
    """

    def __init__(
        self,
        feed_url: str,
        platform: str = "pc",
        db_path: str = ":memory:",
        db: Database | None = None,
        cache_pages_hours: int = 6,
    ) -> None:
        self._feed_url = feed_url
        self._platform = platform
        self._cache_pages_hours = cache_pages_hours

        if db is not None:
            self._db = db
        else:
            self._db = Database(db_path)

    @property
    def source_name(self) -> str:
        """Return ``"fitgirl"`` as the source identifier."""
        return "fitgirl"

    @property
    def platform(self) -> str:
        """Return the platform this source targets."""
        return self._platform

    def _fetch_and_store_sitemap(self, db: Database) -> None:
        """Fetch the FitGirl sitemap XML and store results in the DB."""
        url = "https://fitgirl-repacks.site/sitemap.xml"
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": _USER_AGENT}, verify=False)
            resp.raise_for_status()
            titles = _resolve_sitemap(
                resp.content,
                fetcher=lambda url: requests.get(url, timeout=30, headers={"User-Agent": _USER_AGENT}, verify=False),
            )
            titles = _filter_game_urls(titles)
            db.rebuild_source_titles("fitgirl", [{"magnet": None, **t} for t in titles])
            db.set_sitemap_cache("fitgirl")
            logger.info("FitGirl sitemap indexed {} game titles", len(titles))
        except requests.RequestException as exc:
            logger.warning("Failed to fetch FitGirl sitemap: {}", exc)
            db.set_sitemap_cache("fitgirl")

    def fetch_sitemap(self, db: Database, cancel_event: threading.Event | None = None) -> None:
        """Fetch the FitGirl sitemap and rebuild the source_titles index.

        Handles both ``<urlset>`` and ``<sitemapindex>`` sitemap formats.
        Results are cached in ``sitemap_cache`` for ``cache_pages_hours``.
        Re-fetches even when cache is valid if source_titles is empty
        (e.g. after an initial fetch that returned no parsed titles).
        Checks *cancel_event* at entry for prompt shutdown.

        Args:
            db: The database instance to store results in.
            cancel_event: Optional event to signal cancellation.
        """
        if cancel_event is not None and cancel_event.is_set():
            logger.debug("FitGirl sitemap fetch skipped — cancelled")
            return
        if self._cache_pages_hours > 0 and db.get_sitemap_cache("fitgirl", self._cache_pages_hours):
            if len(db.get_all_source_titles("fitgirl")) > 0:
                logger.info(
                    "FitGirl cache is still valid (TTL: {} hours) — skipping fetch",
                    self._cache_pages_hours,
                )
                return
            logger.info(
                "FitGirl cache is valid but no titles indexed — re-fetching sitemap",
            )

        self._fetch_and_store_sitemap(db)

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()
