"""FreeGOG PC Games download source for gamarr.

Fetches the FreeGOG A-Z game list, parses game entries,
cleans titles, and extracts magnet links from game pages.
"""

from __future__ import annotations

import base64
import re
from contextlib import suppress
from html import unescape
from typing import TYPE_CHECKING, Final

from loguru import logger
from seleniumbase import SB

from gamarr.database import Database
from gamarr.utils import CancelSignal, TimeoutExceededError, run_with_timeout

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

# Hard watchdog for a single browser command (page fetch + page source
# read). A wedged WebDriver/Chrome channel must never be able to block the
# acquisition thread forever — that exact hang froze the whole pipeline
# for 13 days because the scheduler allows only one acquisition run.
_FETCH_TIMEOUT_SECONDS: Final[float] = 90.0
# WebDriver page-load cap applied to the shared browser session so a slow
# page is abandoned instead of stalling the index loop.
_PAGE_LOAD_TIMEOUT_SECONDS: Final[float] = 60.0
# Recycle the browser session after this many consecutive per-page fetch
# failures so a degrading session cannot wedge the loop permanently.
_MAX_CONSECUTIVE_FAILURES: Final[int] = 2
# Also abort the index run when the TOTAL number of fetch timeouts reaches
# this bound — a driver that alternates timeout/failure would otherwise
# recycle the session forever.
_MAX_TOTAL_TIMEOUTS: Final[int] = 4
# Budget for opening and tearing down a browser session — these are also
# WebDriver commands that can hang on a wedged channel.
_SESSION_IO_TIMEOUT_SECONDS: Final[float] = 30.0


def _sb_fetch_with_browser(
    sb: SB,
    url: str,
    *,
    fast: bool = False,
    timeout_seconds: float | None = None,
) -> str:
    """Fetch a URL using an existing SeleniumBase browser session.

    When *fast* is False (A-Z page), uses uc_open_with_reconnect for
    Cloudflare bypass. When *fast* is True (game pages), uses sb.get()
    — no reconnect needed since cf_clearance cookie is already set.

    The fetch runs under a hard watchdog so a hung WebDriver command
    raises TimeoutExceededError instead of blocking the caller forever.
    *timeout_seconds* is the watchdog budget for the whole fetch and
    defaults to ``_FETCH_TIMEOUT_SECONDS`` (resolved at call time).
    """
    if timeout_seconds is None:
        timeout_seconds = _FETCH_TIMEOUT_SECONDS

    def _fetch() -> str:
        if fast:
            sb.get(url)
        else:
            sb.uc_open_with_reconnect(url, 4)
            sb.uc_gui_click_captcha()
        return sb.get_page_source()  # type: ignore[no-any-return]

    return run_with_timeout(_fetch, timeout_seconds)


def _open_browser_session(sb_factory: Callable[[], Any]) -> tuple[Any, Any]:
    """Open an SB-compatible context manager, returning ``(context, browser)``.

    The context ``__enter__`` runs under a watchdog so a wedged driver
    startup cannot hang the caller. On failure, a best-effort bounded
    teardown of the half-opened context is attempted; if BOTH the startup
    and the teardown wedged, a browser process may still leak (residual —
    a container restart reclaims it).
    """
    ctx = sb_factory()
    try:
        sb = run_with_timeout(ctx.__enter__, _SESSION_IO_TIMEOUT_SECONDS)
    except BaseException:
        with suppress(Exception):
            run_with_timeout(lambda: ctx.__exit__(None, None, None), _SESSION_IO_TIMEOUT_SECONDS)
        raise
    return ctx, sb


def _close_browser_session(ctx: Any, sb: Any) -> None:
    """Best-effort teardown of an SB context (driver quit, then context exit).

    Both steps run under a watchdog so a wedged teardown cannot hang the
    caller; any failure (including timeout) is suppressed.
    """
    with suppress(Exception):
        run_with_timeout(sb.quit, _SESSION_IO_TIMEOUT_SECONDS)
    with suppress(Exception):
        run_with_timeout(lambda: ctx.__exit__(None, None, None), _SESSION_IO_TIMEOUT_SECONDS)


def _default_sb_factory() -> SB:
    """Return a fresh SeleniumBase undetected-chromedriver context manager."""
    return SB(uc=True)


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
        sb: SB,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Fetch a single FreeGOG game page and store its magnet.

        Args:
            db: Database instance.
            entry: Dict with ``title`` and ``url`` keys.
            sb: Active SeleniumBase browser session.
            timeout_seconds: Hard watchdog budget for the page fetch, so a
                hung browser is treated as a page failure instead of
                blocking the acquisition thread forever. Defaults to
                ``_FETCH_TIMEOUT_SECONDS``.

        Returns True if a new game was indexed, False otherwise.

        Raises:
            TimeoutExceededError: When the page fetch exceeds
                *timeout_seconds* (the session must be recycled).
        """
        if timeout_seconds is None:
            timeout_seconds = _FETCH_TIMEOUT_SECONDS
        try:
            html = _sb_fetch_with_browser(sb, entry["url"], fast=True, timeout_seconds=timeout_seconds)
            magnet = _extract_magnet_from_freegog_page(html)
        except TimeoutExceededError as exc:
            # Re-raise: a timed-out fetch leaves a zombie worker hung on the
            # shared driver, so callers must recycle the session immediately.
            logger.warning("FreeGOG game page fetch timed out for '{}': {}", entry["url"], exc)
            raise
        except Exception as exc:
            logger.warning(
                "Failed to fetch FreeGOG game page '{}': {}",
                entry["url"],
                exc,
            )
            return False

        # Atomically delete old row and insert new row in a single transaction
        # via upsert_source_title to avoid a crash window between two separate commits.
        try:
            db.upsert_source_title(
                source="freegog",
                title=entry["title"],
                url=entry["url"],
                magnet=magnet,
            )
        except Exception as exc:
            # A DB error must not abort the whole index loop.
            logger.warning("Failed to store FreeGOG game '{}': {}", entry["url"], exc)
            return False
        return True

    @staticmethod
    def _log_az_progress(
        new_count: int,
        missing_magnet_count: int,
        total_entries: int,
        known_count: int,
    ) -> None:
        """Log periodic progress during FreeGOG A-Z page indexing."""
        remaining = total_entries - known_count
        if new_count == 0 and missing_magnet_count == 0:
            logger.info(
                "FreeGOG: fetching {} game pages ({} known, {} need magnets)",
                remaining,
                known_count,
                remaining,
            )
        elif (new_count + missing_magnet_count) % 500 == 0:
            logger.info(
                "FreeGOG progress: {}/{} games fetched",
                new_count + missing_magnet_count,
                remaining,
            )

    @staticmethod
    def _log_az_summary(
        new_count: int,
        total_entries: int,
        known_count: int,
        missing_magnet_count: int,
    ) -> None:
        """Log a summary line after A-Z page indexing completes."""
        if new_count > 0 or missing_magnet_count > 0:
            logger.info(
                "FreeGOG: {} new games indexed ({} checked, {} known, {} failed)",
                new_count,
                total_entries,
                known_count,
                missing_magnet_count,
            )
        else:
            logger.info(
                "FreeGOG: all {} entries already known — nothing new",
                total_entries,
            )

    @staticmethod
    def _configure_driver(sb: SB) -> None:
        """Cap page loads on the shared session so a slow page cannot stall the loop."""
        with suppress(Exception):
            sb.driver.set_page_load_timeout(_PAGE_LOAD_TIMEOUT_SECONDS)

    def _index_az_page(
        self,
        db: Database,
        *,
        sb_factory: Callable[[], Any] | None = None,
        cancel_event: CancelSignal | None = None,
    ) -> None:
        """Fetch the FreeGOG A-Z page and index new games.

        Cross-references against existing ``source_titles`` entries and
        only fetches game pages for new URLs. Uses a single SeleniumBase
        browser session for all fetches to avoid per-page startup overhead;
        the session is recycled after ``_MAX_CONSECUTIVE_FAILURES``
        consecutive per-page failures so a degrading browser cannot wedge
        the loop permanently. Every page fetch also runs under a hard
        watchdog (see :func:`_sb_fetch_with_browser`).

        Args:
            db: The database instance to store results in.
            sb_factory: Optional factory returning an SB-compatible context
                manager (used for test injection; defaults to ``SB(uc=True)``).
            cancel_event: Optional event; when set, indexing stops early.
        """
        url = "https://freegogpcgames.com/game-list/"
        if sb_factory is None:
            sb_factory = _default_sb_factory
        ctx: Any = None
        sb: Any = None
        try:
            ctx, sb = _open_browser_session(sb_factory)
            self._configure_driver(sb)
            html = _sb_fetch_with_browser(sb, url)
            az_entries = _parse_freegog_az_page(html)
            ctx, sb, new_count, known_count, missing_magnet_count, cancelled = self._index_az_entries(
                db,
                az_entries,
                self._build_existing_urls(db),
                ctx,
                sb,
                sb_factory,
                cancel_event,
            )
            self._finalise_az_index(
                db,
                len(az_entries),
                new_count,
                known_count,
                missing_magnet_count,
                completed=not cancelled,
            )
        except Exception as exc:
            logger.warning("Failed to fetch FreeGOG A-Z page: {}", exc)
            # Do NOT update the cache on failure — a transient error should not
            # suppress retries for the full TTL window.
        finally:
            if ctx is not None and sb is not None:
                _close_browser_session(ctx, sb)

    def _index_az_entries(
        self,
        db: Database,
        az_entries: list[dict[str, str]],
        existing_urls: dict[str, str | None],
        ctx: Any,
        sb: Any,
        sb_factory: Callable[[], Any],
        cancel_event: CancelSignal | None,
    ) -> tuple[Any, Any, int, int, int, bool]:
        """Index parsed A-Z entries, recycling the browser session on repeated failures.

        Args:
            db: The database instance to store results in.
            az_entries: Parsed ``{"title", "url", "letter"}`` entries.
            existing_urls: URL-to-magnet map from ``source_titles``.
            ctx: Open SB-compatible context manager.
            sb: Browser session bound to *ctx*.
            sb_factory: Factory for replacement sessions.
            cancel_event: Optional event; when set, indexing stops early.

        Returns:
            ``(ctx, sb, new_count, known_count, missing_magnet_count,
            cancelled)`` where ``ctx``/``sb`` are the final (possibly
            recycled) pair and ``cancelled`` is True when the loop stopped
            early because *cancel_event* was set.
        """
        new_count = 0
        known_count = 0
        missing_magnet_count = 0
        consecutive_failures = 0
        consecutive_timeouts = 0
        total_timeouts = 0
        total_entries = len(az_entries)

        try:
            for entry in az_entries:
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("FreeGOG indexing cancelled — stopping early")
                    return ctx, sb, new_count, known_count, missing_magnet_count, True

                outcome = self._process_az_entry(db, entry, sb, existing_urls)
                if outcome == "known":
                    known_count += 1
                    continue

                self._log_az_progress(new_count, missing_magnet_count, total_entries, known_count)
                if outcome == "ok":
                    new_count += 1
                    consecutive_failures = 0
                    consecutive_timeouts = 0
                elif outcome == "timeout":
                    # A timed-out fetch leaves a zombie worker hung on the shared
                    # driver — recycle immediately so the next fetch gets a clean
                    # session. If timeouts persist (consecutively or in total),
                    # the site/driver is wedged: abort this index run entirely.
                    missing_magnet_count += 1
                    consecutive_timeouts += 1
                    total_timeouts += 1
                    if consecutive_timeouts >= _MAX_CONSECUTIVE_FAILURES or total_timeouts >= _MAX_TOTAL_TIMEOUTS:
                        logger.warning(
                            "FreeGOG: {} page fetch timeouts ({} consecutive) — aborting indexing for this cycle",
                            total_timeouts,
                            consecutive_timeouts,
                        )
                        return ctx, sb, new_count, known_count, missing_magnet_count, True
                    ctx, sb = self._recycle_browser_session(ctx, sb, sb_factory, "page fetch timed out")
                    consecutive_failures = 0
                else:
                    missing_magnet_count += 1
                    consecutive_timeouts = 0
                    consecutive_failures += 1
                    if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        ctx, sb = self._recycle_browser_session(
                            ctx,
                            sb,
                            sb_factory,
                            f"{consecutive_failures} consecutive fetch failures",
                        )
                        consecutive_failures = 0
            return ctx, sb, new_count, known_count, missing_magnet_count, False
        except BaseException:
            # Close whichever session is current so a failure mid-recycle
            # cannot leak the live browser; the caller's finally then closes
            # the original pair again — a double-close is harmless (suppressed).
            _close_browser_session(ctx, sb)
            raise

    def _recycle_browser_session(
        self,
        ctx: Any,
        sb: Any,
        sb_factory: Callable[[], Any],
        reason: str,
    ) -> tuple[Any, Any]:
        """Close the current browser session and open a fresh one.

        Args:
            ctx: Open SB-compatible context manager to close.
            sb: Browser session bound to *ctx*.
            sb_factory: Factory for the replacement session.
            reason: Human-readable reason for the recycle (logged).

        Returns:
            ``(ctx, sb)`` for the newly opened session.
        """
        logger.warning("FreeGOG: recycling browser session — {}", reason)
        _close_browser_session(ctx, sb)
        ctx, sb = _open_browser_session(sb_factory)
        self._configure_driver(sb)
        return ctx, sb

    def _process_az_entry(
        self,
        db: Database,
        entry: dict[str, str],
        sb: Any,
        existing_urls: dict[str, str | None],
    ) -> str:
        """Process one A-Z entry.

        Returns ``"known"`` when the URL is already indexed with a magnet,
        ``"ok"`` when the page fetch and store succeeded, ``"timeout"``
        when the page fetch exceeded its watchdog (the session must be
        recycled), and ``"failed"`` when the page could not be fetched or
        stored for any other reason.
        """
        if entry["url"] in existing_urls and existing_urls[entry["url"]] is not None:
            return "known"
        try:
            if self._fetch_and_store_game(db, entry, sb):
                return "ok"
        except TimeoutExceededError:
            return "timeout"
        return "failed"

    @staticmethod
    def _finalise_az_index(
        db: Database,
        total_entries: int,
        new_count: int,
        known_count: int,
        missing_magnet_count: int,
        *,
        completed: bool,
    ) -> None:
        """Update the sitemap cache and log the A-Z index summary.

        The cache is only updated when entries were actually parsed AND the
        entry loop ran to completion; an empty parse (0 entries) likely
        means the site structure changed, and an early-cancelled run must
        not suppress the remaining pages for the full TTL window.
        """
        if completed and total_entries > 0:
            db.set_sitemap_cache("freegog")
        elif total_entries == 0:
            logger.warning("FreeGOG A-Z page returned 0 entries — site structure may have changed")
        else:
            logger.info("FreeGOG A-Z indexing cancelled before completion — cache not updated")
        FreeGOGSource._log_az_summary(new_count, total_entries, known_count, missing_magnet_count)

    def fetch_sitemap(
        self,
        db: Database,
        cancel_event: CancelSignal | None = None,
    ) -> None:
        """Fetch the FreeGOG game list and index new games.

        Checks the sitemap cache first. Re-fetches even when cache is
        valid if ``source_titles`` is empty (e.g. after an initial fetch
        that returned no parsed titles). Checks *cancel_event* at entry
        for prompt shutdown and propagates it to the index loop so
        per-page indexing also stops early when it is set.

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

        self._index_az_page(db, cancel_event=cancel_event)
