"""Acquisition pipeline for gamarr."""

from __future__ import annotations

import datetime
import math
import re
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html import unescape
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

import requests
from loguru import logger

from gamarr.database import _EXPIRED_RESULT, _INDEFINITE_DAYS, Database
from gamarr.metacritic import MetacriticClient
from gamarr.metacritic_cache import MetacriticCache
from gamarr.models import SOURCE_DISPLAY
from gamarr.notifications import Notifier
from gamarr.qbittorrent import QBittorrentClient
from gamarr.sources.fitgirl import _USER_AGENT, FitGirlSource, _extract_magnet_from_html
from gamarr.sources.freegog import FreeGOGSource, _extract_magnet_from_freegog_page

# Re-exported for use by _tokenize_title()
from gamarr.utils import (
    _ROMAN_TO_ARABIC,
    EARLIEST_BROWSE_YEAR,
    CancelSignal,
    current_year_month,
    is_cancelled,
    is_valid_year_month,
    month_of,
    normalise_for_compare,
    numeral_runs_into_a_digit,
    subtract_months,
    year_month_to_date,
)

# urllib3 warnings for FitGirl self-signed cert are suppressed in gamarr.sources.fitgirl

if TYPE_CHECKING:
    from collections.abc import Callable


def _escape_markup(value: object) -> str:
    """Escape Loguru markup angle brackets in user-provided values.

    Loguru's ``opt(colors=True)`` interprets ``<color>`` tags.  User-provided
    game titles or descriptions may contain literal ``<`` or ``>`` characters
    that would be incorrectly parsed as markup tags.
    """
    return str(value).replace("<", "\\<").replace(">", "\\>")


def _escape_or(value: object, default: str) -> str:
    """Escape *value* for Loguru markup, using *default* when *value* is ``None``.

    Convenience wrapper that avoids repeating the ``X if X is not None else Y``
    pattern for every field in :func:`_log_game_details`.
    """
    return _escape_markup(value) if value is not None else default


def _log_game_details(mc_result: Any) -> None:
    """Log a colorized summary line for a looked-up game (gamecritic-style).

    The format matches ``/data/gamecritic/gamecritic.py`` and includes:
    title, Metascore, critic review count, user score, user review count,
    genre(s), must-play status, and release date.

    Args:
        mc_result: A :class:`ScoreResult` or anything with similar attributes.
    """
    if mc_result is None:
        return
    title = _escape_markup(mc_result.title)
    ms = _escape_or(mc_result.metascore, "TBD")
    ms_r = _escape_or(mc_result.metascore_review_count, "?")
    us = _escape_or(mc_result.user_score, "TBD")
    us_r = _escape_or(mc_result.user_review_count, "?")
    genre = _escape_markup(", ".join(mc_result.genres)) if mc_result.genres else "N/A"
    must_play = "<green><bold>Yes</bold></green>" if mc_result.must_play else "<dim>No</dim>"
    release = _escape_or(mc_result.release_date, "N/A")
    sep = " <dim>|</dim> "

    logger.opt(colors=True).info(
        f"Title: <cyan><bold>{title}</bold></cyan>"
        f"{sep}<green>Metascore: <bold>{ms}</bold></green> <dim>({ms_r} reviews)</dim>"
        f"{sep}<yellow>User: <bold>{us}</bold></yellow> <dim>({us_r} reviews)</dim>"
        f"{sep}<magenta>Genre: {genre}</magenta>"
        f"{sep}Must Play: {must_play}"
        f"{sep}Released: <dim>{release}</dim>"
    )


def _is_older_than(release_date: str | None, days: int) -> bool:
    """Check if a release date string is older than *days* from today.

    Returns ``False`` when *release_date* is ``None`` (unknown age
    — assume recent), *days* is zero or negative (no filter), or the
    date string is malformed.
    """
    if release_date is None or days <= 0:
        return False
    try:
        released = datetime.datetime.strptime(release_date.strip(), "%Y-%m-%d").date()
        cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=days)).date()
        return released < cutoff
    except (ValueError, TypeError):
        return False


def _normalise_mode(mode: str) -> Literal["latest", "backlog"]:
    """Validate the discovery mode passed through the API.

    Args:
        mode: The caller's mode.

    Returns:
        ``"latest"`` or ``"backlog"``; an unknown value falls back to backlog
        with a warning.
    """
    normalised = mode.casefold() if isinstance(mode, str) else ""
    if normalised == "latest":
        return "latest"
    if normalised != "backlog":
        logger.warning("mode {!r} is not 'latest' or 'backlog' — using backlog", mode)
    return "backlog"


def _normalise_max_date(max_date: int) -> int:
    """Validate a ``max_date`` passed through the API and clamp a future month.

    The config layer rejects invalid values; this guard keeps a direct caller
    from turning a typo into a century-long scan.

    Args:
        max_date: The caller's floor, ``0`` for none.

    Returns:
        A valid ``YYYYMM`` month, or ``0`` when the value was unusable.
    """
    if not is_valid_year_month(max_date):
        logger.warning("max_date {!r} is not a valid YYYYMM month — ignoring it", max_date)
        return 0
    current = current_year_month()
    if max_date > current:
        logger.warning("max_date {} is in the future — clamping to {}", max_date, current)
        return current
    return max_date


def _normalise_cycle_months(max_cycle_months: int) -> int:
    """Validate the month pace passed through the API.

    Args:
        max_cycle_months: The caller's pace, ``0`` for no pacing.

    Returns:
        A pace between 0 and 1200 months.
    """
    if isinstance(max_cycle_months, bool) or not isinstance(max_cycle_months, int):
        logger.warning("max_cycle_months {!r} is not an integer — ignoring it", max_cycle_months)
        return 0
    if max_cycle_months < 0:
        logger.warning("max_cycle_months {} is negative — ignoring it", max_cycle_months)
        return 0
    if max_cycle_months > 1200:
        logger.warning("max_cycle_months {} is implausibly large — clamping to 1200", max_cycle_months)
        return 1200
    return max_cycle_months


_DEFAULT_MAX_PAGES = 500
# Years of extra descent allowed below a floorless latest cycle's pace window, to
# absorb a newest release that lags today because the recent listings are empty.
_FLOORLESS_LATEST_SLACK_YEARS = 3


def _latest_call_depth(allowance: int, reserve: int) -> int:
    """Return the page depth for one year in latest mode.

    Args:
        allowance: Pages left of this cycle's depth budget.
        reserve: Pages to keep for the years below.

    Returns:
        The depth for this call.  ``0`` when the allowance is spent; when the
        allowance is at or below the reserve the reserve is dropped, since
        scanning nothing would be strictly worse.  A depth that small cannot
        honour the previous-year guarantee, so ``max_pages`` should be >= 2.
    """
    if allowance <= 0:
        return 0
    return max(allowance - reserve, 0) or allowance


def _floorless_latest_bound(max_cycle_months: int, current_year: int) -> int:
    """Return the oldest year a floorless latest cycle may probe.

    A floorless latest config only promises "read back N months from the newest
    release", and that release can lag today when the recent listings are empty.
    The descent therefore gets twice the pace window plus a few years of slack, so
    the full window still fits when the anchor lags by up to the pace window plus
    that slack.  The bound must not be derived from today's month alone (see the
    year-range branch), and it must never go below the catalogue's first year: a
    listing that carries no release dates at all would otherwise be crawled for
    decades every cycle.

    Args:
        max_cycle_months: The configured month pace; always positive here.
        current_year: The cycle's current year.

    Returns:
        The oldest year to probe.
    """
    pace_years = math.ceil(max_cycle_months / 12)
    return max(EARLIEST_BROWSE_YEAR, current_year - (2 * pace_years + _FLOORLESS_LATEST_SLACK_YEARS))


def _date_driven(scan_order: str, latest_mode: bool, max_date: int, max_cycle_months: int) -> bool:
    """Return True when date keys bound the scan instead of a page count.

    Both date keys only have meaning in the ``new`` browse order, whose pages
    carry a release-date dimension.  Backlog needs ``max_date`` because there
    the floor replaces the sweep budget; latest accepts either key, since it has
    no cursor to pace and a date floor alone would re-read the whole window on
    every cycle.

    Args:
        scan_order: The resolved browse order.
        latest_mode: True when this cycle runs in latest mode.
        max_date: The configured floor month, ``0`` when disabled.
        max_cycle_months: The configured month pace, ``0`` when disabled.

    Returns:
        True when the date window governs this cycle.
    """
    if scan_order != "new":
        return False
    if max_date:
        return True
    return latest_mode and bool(max_cycle_months)


def _normalise_latest_depth(max_pages: int | None, mode: str, *, date_driven: bool = False) -> int | None:
    """Clamp the latest-mode page depth.

    ``None`` and ``0`` both mean "unbounded", which in latest mode would scan the
    whole window — that is what backlog mode is for.  A latest cycle bounded by
    either date key has no page depth at all, so the zero clamp does not apply
    there; a negative value is still sanitised, because it is a typo either way.

    Args:
        max_pages: The caller's page budget or depth.
        mode: The resolved discovery mode.
        date_driven: True when date keys bound this cycle.  A latest cycle then
            has no depth, so the zero clamp does not apply; backlog behaviour is
            unaffected by this flag (its zero clamp was always latest-only).

    Returns:
        ``10`` when latest depth mode was given no depth, otherwise *max_pages*
        unchanged.
    """
    if max_pages is not None and max_pages < 0:
        # Not "unset": in backlog an unset budget means no page cap at all, so a
        # negative typo would silently enlarge the scan.  Use the documented
        # default (mirrors MetacriticConfig.max_pages) instead.
        logger.warning("max_pages {} is negative — using the default budget of {}", max_pages, _DEFAULT_MAX_PAGES)
        max_pages = _DEFAULT_MAX_PAGES
    if mode == "latest" and not max_pages and not date_driven:
        # Only depth-driven latest treats 0/None as "unbounded".  A date-driven
        # cycle has no depth, so those values are meaningless there, not unsafe.
        logger.warning("mode: latest with no max_pages is unbounded — clamping depth to 10 pages")
        return 10
    return max_pages


def _games_at_or_after_floor(games: list[dict[str, Any]], floor_month: int) -> list[dict[str, Any]]:
    """Return the games whose release month is unknown or at/after *floor_month*.

    ``max_date`` is the oldest month to review.  The browse stop works at page
    granularity, so a kept page can mix in-floor and below-floor games; this
    filter keeps the queue honest.  Undated games on a *kept* page are retained
    because their age is unknown — but the page that trips the cutoff is
    discarded whole by the scan, so undated entries on that page are not
    collected.

    Args:
        games: The games collected from one browse page.
        floor_month: The ``YYYYMM`` floor.

    Returns:
        The games that belong in the queue.
    """
    return [
        game
        for game in games
        if (game_month := month_of(game.get("release_date"))) is None or game_month >= floor_month
    ]


def _month_span(newer: int | None, older: int | None) -> int:
    """Return whole months between two ``YYYYMM`` values.

    Args:
        newer: The newer ``YYYYMM`` month, or ``None``.
        older: The older ``YYYYMM`` month, or ``None``.

    Returns:
        Whole months from *older* to *newer*; 0 when either side is unknown.
    """
    if not newer or not older:
        return 0
    return max(0, (newer // 100) * 12 + newer % 100 - ((older // 100) * 12 + older % 100))


@dataclass
class AcquisitionConfig:
    """Thresholds and settings for the acquisition run."""

    min_criticscore: int
    min_criticscore_reviews: int
    min_user_score: float
    min_user_reviews: int
    cache_details_days: int = 7
    cache_hours: int = 6
    enabled: bool = True
    max_queue_days: int = 30
    max_pages: int | None = None
    max_cycle_pages: int | None = None
    max_date: int = 0
    max_cycle_months: int = 0
    mode: Literal["latest", "backlog"] = "backlog"
    reject_genre: list[str] | None = None
    reject_title: list[str] | None = None
    fitgirl_max_queue_days: int = 60
    notify_on_scrape_failure: bool = True
    sort_order: Literal["new", "criticscore", "userscore"] = "new"
    age_recheck_weeks: int | None = None


def run_acquisition(
    *,
    platform: str = "pc",
    db_path: str = ":memory:",
    qbt_host: str = "localhost",
    qbt_port: int = 8080,
    qbt_username: str = "admin",
    qbt_password: str = "adminadmin",
    qbt_category: str = "games-gamarr",
    qbt_add_paused: bool = False,
    min_criticscore: int = 75,
    min_criticscore_reviews: int = 10,
    min_user_score: float = 7.5,
    min_user_reviews: int = 10,
    cache_details_days: int = 7,
    cache_hours: int = 6,
    enabled: bool = True,
    max_queue_days: int = 30,
    max_pages: int | None = None,
    max_cycle_pages: int | None = None,
    max_date: int = 0,
    max_cycle_months: int = 0,
    mode: Literal["latest", "backlog"] = "backlog",
    fitgirl_max_queue_days: int = 60,
    notify_on_scrape_failure: bool = True,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,
    apprise_urls: list[str] | None = None,
    notify_on_download: bool = True,
    notify_on_failure: bool = False,
    notify_on_error: bool = False,
    library_paths: list[str] | None = None,
    fitgirl_cache_hours: int = 6,
    fitgirl_reject_keywords: list[str] | None = None,
    cancel_event: CancelSignal | None = None,
    download_sites: list | None = None,
    sort_order: Literal["new", "criticscore", "userscore"] = "new",
    age_recheck_weeks: int | None = None,
) -> list[dict[str, Any]]:
    """Execute one scan cycle.

    Discovers games by browsing Metacritic in *mode*: ``"latest"`` re-reads the
    newest end of the window every cycle without persisting or resetting the
    scan cursors, and always browses in ``"new"`` order whatever ``sort_order``
    says.  A latest cycle is date-driven when *max_date* or *max_cycle_months* is
    set — *max_date* is the floor and *max_cycle_months* the per-cycle pace, so
    *max_pages* is unused — and otherwise falls back to a page depth of
    *max_pages* (clamped to 10 when unset).  ``"backlog"`` sweeps the window with
    durable cursors and month pacing in the configured ``sort_order``.  Each
    game's real Metacritic detail-page scores are verified against the configured
    thresholds, then survivors are matched against each configured source's
    sitemap and delivered to qBittorrent.
    """
    mode = _normalise_mode(mode)
    max_date = _normalise_max_date(max_date)
    max_cycle_months = _normalise_cycle_months(max_cycle_months)
    date_driven = _date_driven("new" if mode == "latest" else sort_order, mode == "latest", max_date, max_cycle_months)
    max_pages = _normalise_latest_depth(max_pages, mode, date_driven=date_driven)

    cfg = AcquisitionConfig(
        min_criticscore=min_criticscore,
        min_criticscore_reviews=min_criticscore_reviews,
        min_user_score=min_user_score,
        min_user_reviews=min_user_reviews,
        cache_details_days=cache_details_days,
        cache_hours=cache_hours,
        enabled=enabled,
        max_queue_days=max_queue_days,
        max_pages=max_pages,
        max_cycle_pages=max_cycle_pages,
        max_date=max_date,
        max_cycle_months=max_cycle_months,
        mode=mode,
        fitgirl_max_queue_days=fitgirl_max_queue_days,
        notify_on_scrape_failure=notify_on_scrape_failure,
        reject_genre=reject_genre,
        reject_title=reject_title,
        sort_order=sort_order,
        age_recheck_weeks=age_recheck_weeks,
    )

    logger.info("Fetching Metacritic pages for platform '{}', please wait...", platform)

    db = Database(db_path)
    mc = MetacriticClient(cache=MetacriticCache(db))

    notifier = Notifier(
        apprise_urls=apprise_urls,
        on_download=notify_on_download,
        on_failure=notify_on_failure,
        on_error=notify_on_error,
        on_scrape_failure=notify_on_scrape_failure,
    )

    qbt = QBittorrentClient(
        host=qbt_host,
        port=qbt_port,
        username=qbt_username,
        password=qbt_password,
        category=qbt_category,
        add_paused=qbt_add_paused,
    )

    if not qbt.is_connected():
        logger.warning("qBittorrent is not reachable; skipping scan.")
        notifier.send_error_notification("qBittorrent is not reachable")
        mc.close()
        db.close()
        return []

    def _make_fitgirl_entry() -> Any:
        """Build a fallback source entry from legacy parameters."""
        entry = types.SimpleNamespace()
        entry.name = "fitgirl"
        entry.enabled = True
        entry.platform = platform
        entry.cache_hours = fitgirl_cache_hours
        entry.reject_keywords = fitgirl_reject_keywords or []
        entry.max_queue_days = fitgirl_max_queue_days
        return entry

    def _run_discovery_phases(
        mc: Any,
        db: Database,
        cfg: AcquisitionConfig,
        platform: str,
        qbt: Any,
        notifier: Any,
        *,
        download_sites: list | None = None,
    ) -> list[dict[str, Any]]:
        """Run Metacritic browse, sitemap fetch (if needed), and pending matching.

        Returns combined results from pending-game matching.

        Metacritic-first: browse Metacritic first; only fetch a source's
        sitemap if Metacritic produced at least one game to match.
        """
        # Expiry runs FIRST, before browse: a lapsed row is invisible to
        # get_pending and absent from get_known_slugs, so browse would treat the
        # game as a fresh candidate and record_pending would then reject it on
        # the primary key (the row is still there).  Sweeping here lets the same
        # cycle re-queue it.  Results are NOT folded into `matched`: an expiry is
        # not a match.
        expired_count = len(_process_expired_games(db, cancel_event=cancel_event))

        # Metacritic-first discovery: browse Metacritic for games that pass
        # score thresholds and age filter. The FitGirl sitemap is fetched
        # only if Metacritic produced games to match against.
        browse_games: list[dict[str, Any]] = []
        new_pending: int = 0
        scan_year_anchor = datetime.datetime.now(tz=datetime.UTC).year

        # Mode resolution.  "latest" reads the top of the window every cycle and
        # never persists cursors; "backlog" sweeps with cursors and pacing.  A
        # date bound needs the per-year browse dimension, which only sort_order
        # "new" has.
        latest_mode = cfg.mode == "latest"
        scan_order: Literal["new", "criticscore", "userscore"] = "new" if latest_mode else cfg.sort_order
        if latest_mode and cfg.sort_order != "new":
            logger.warning(
                "Latest mode requires sort_order 'new' — scanning in 'new' order for this cycle (configured '{}')",
                cfg.sort_order,
            )
        # Date keys govern the scan when present: max_date is the floor, and in
        # latest mode max_cycle_months paces how much of that window one cycle
        # reads.  Either key makes a page depth pointless, so max_pages is only
        # the fallback for a latest cycle that sets neither.
        date_mode = _date_driven(scan_order, latest_mode, cfg.max_date, cfg.max_cycle_months)
        floor_month = cfg.max_date if date_mode else 0
        floor_cutoff = year_month_to_date(floor_month) if floor_month else None
        if latest_mode and date_mode:
            logger.info(
                "Discovery mode: latest (date window: floor {}, pace {})",
                floor_month or "none",
                f"{cfg.max_cycle_months} month(s)" if cfg.max_cycle_months else "unpaced",
            )
        elif latest_mode:
            logger.info("Discovery mode: latest (depth {} pages, window floor none)", cfg.max_pages)
        else:
            logger.info("Discovery mode: backlog")

        pages_this_cycle = 0
        min_month_seen: int | None = None
        max_month_seen: int | None = None
        cutoff_stopped = False

        if cfg.enabled:
            # Detect sort_order change and reset progress if needed
            previous_sort_order = db.get_last_sort_order(platform)
            if previous_sort_order is not None and previous_sort_order != scan_order:
                logger.info(
                    "Sort order changed from '{}' to '{}'",
                    previous_sort_order,
                    scan_order,
                )
                # The cached pages were fetched with a different browse order.
                db.clear_browse_cache(platform)

            if latest_mode:
                # Latest re-orders the cache but never reads the sweep cursors,
                # so it must not invalidate them: a user trying latest must be
                # able to return to a mid-sweep backlog position.
                if db.get_cursor_sort_order(platform) is not None and db.get_cursor_sort_order(platform) != scan_order:
                    logger.info("Latest mode leaves the backlog cursors untouched (they belong to another order)")
            else:
                cursor_order = db.get_cursor_sort_order(platform)
                if cursor_order is not None and cursor_order != scan_order:
                    # The recorded page positions belong to a different browse
                    # order, so resuming from them would skip unseen pages.
                    logger.info(
                        "Sweep cursors belong to sort order '{}' — resetting them for '{}'",
                        cursor_order,
                        scan_order,
                    )
                    db.reset_progress(platform, scan_order)
                db.set_cursor_sort_order(platform, scan_order)

            # Record the order the cache is about to hold BEFORE the scan runs: a
            # cycle abandoned by the watchdog must not leave the cache in one
            # order while the stored order still claims another.
            db.set_last_sort_order(platform, scan_order)

            mc.sort_order = scan_order

            if not latest_mode and cfg.max_date and not date_mode:
                logger.info(
                    "Config: max_date/max_cycle_months ignored because sort_order is '{}'",
                    scan_order,
                )
            if not latest_mode and cfg.max_cycle_months and not date_mode:
                logger.info(
                    "Config: max_cycle_months ignored because date mode is off (max_date={}, sort_order='{}')",
                    cfg.max_date,
                    scan_order,
                )
            if date_mode and not latest_mode and cfg.max_pages:
                logger.info(
                    "Config: max_pages ({}) is unused in date mode (max_date={})",
                    cfg.max_pages,
                    floor_month,
                )
            if date_mode and not latest_mode and cfg.max_cycle_pages:
                logger.info(
                    "Config: max_cycle_pages ignored in date mode (max_cycle_pages={}, max_cycle_months={})",
                    cfg.max_cycle_pages,
                    cfg.max_cycle_months,
                )
            if date_mode and latest_mode and cfg.max_pages:
                logger.info(
                    "Config: max_pages ({}) is unused in latest date mode (max_date={}, max_cycle_months={})",
                    cfg.max_pages,
                    floor_month or "none",
                    cfg.max_cycle_months or "none",
                )
            if latest_mode and cfg.max_cycle_pages:
                logger.info(
                    "Config: max_cycle_pages ({}) ignored in latest mode — the whole cycle is one pass",
                    cfg.max_cycle_pages,
                )
            # An unpaced window repeats on every cycle and nothing caps it, so name
            # the cost.  A pace that covers the whole window is NOT unpaced in that
            # sense: it means full coverage, which is the healthy case.  What is
            # worth stating instead is the opposite shape, a pace narrower than the
            # floored window: latest re-reads only the newest N months every cycle
            # (no cursors), so the older months are never read there at all.
            floor_span = _month_span(current_year_month(), floor_month) if floor_month else 0
            floorless_catalogue_wide = (
                bool(cfg.max_cycle_months)
                and not floor_month
                and (_floorless_latest_bound(cfg.max_cycle_months, scan_year_anchor) <= EARLIEST_BROWSE_YEAR)
            )
            # A one-month window costs nothing to re-read, so it warrants no warning;
            # the floorless shape has no month span to measure.
            if (
                latest_mode
                and date_mode
                and (not cfg.max_cycle_months or floorless_catalogue_wide)
                and (not floor_month or floor_span > 0)
            ):
                logger.warning(
                    "Latest mode's date window is effectively unpaced (max_cycle_months={}) — every cycle "
                    "re-reads the whole window back to {} with no page cap (max_pages is unused), and a window "
                    "deeper than the run timeout never reaches its floor; {}",
                    cfg.max_cycle_months or "none",
                    floor_month or _floorless_latest_bound(cfg.max_cycle_months, scan_year_anchor),
                    "set max_cycle_months to pace it"
                    if not cfg.max_cycle_months
                    else "lower max_cycle_months to pace it",
                )
            elif latest_mode and date_mode and floor_span > 0 and cfg.max_cycle_months < floor_span:
                logger.info(
                    "Latest mode is paced at {} month(s) — max_date={} is the floor, not a sweep target; "
                    "anything older than that pace is covered only in backlog mode",
                    cfg.max_cycle_months,
                    floor_month,
                )

            # Calculate year range
            if scan_order == "new":
                if date_mode and not latest_mode:
                    cutoff_year = min(scan_year_anchor, floor_month // 100)
                elif latest_mode and date_mode:
                    # The date stops end the loop; this bound is only a safety net
                    # beneath them.  The floored branch must be absolute: the pace
                    # anchors on the newest release the listings actually carry,
                    # which can lag today by years when recent listings are empty,
                    # and a bound of "today minus the pace" would then skip the
                    # paced window silently (latest persists no cursors, so nothing
                    # would ever come back for it).  The floorless branch has no
                    # floor to anchor on, so it substitutes twice the paced window
                    # plus slack, and a descent that found no listing at all is
                    # reported after the loop.
                    cutoff_year = (
                        floor_month // 100
                        if floor_month
                        else _floorless_latest_bound(cfg.max_cycle_months, scan_year_anchor)
                    )
                elif latest_mode:
                    # No date keys configured: also include the previous year, so
                    # a game released in December whose scores publish in January
                    # is still discoverable at the turn of the year.
                    cutoff_year = scan_year_anchor - 1
                else:
                    years_back = max(0, math.ceil((cfg.max_pages if cfg.max_pages else 500) / 52))
                    cutoff_year = scan_year_anchor - years_back
                current_year = scan_year_anchor
            else:
                # sort_order != "new" (criticscore/userscore): no year dimension, use year=0 sentinel
                cutoff_year = 0
                current_year = 0

            max_pages_cfg = cfg.max_pages if cfg.max_pages else 0
            if latest_mode:
                # Latest depth is per cycle: no durable budget, no reset.
                remaining_pages = 0
                cycle_allowance = 0
            elif date_mode:
                # Date mode has no page budget: the floor bounds the sweep and
                # the month allowance paces it.
                remaining_pages = 0
                cycle_allowance = 0
            else:
                total_scanned = db.sum_scanned_pages(platform, cutoff_year, current_year)

                # Budget check — auto-reset when exhausted
                if max_pages_cfg > 0 and total_scanned >= max_pages_cfg:
                    logger.info(
                        "Budget exhausted — {} of {} pages scanned, restarting from page 1",
                        total_scanned,
                        max_pages_cfg,
                    )
                    db.reset_progress(platform, scan_order)
                    total_scanned = 0

                remaining_pages = max_pages_cfg - total_scanned if max_pages_cfg > 0 else 0
                # Page allowance shared by the whole cycle (0 = unlimited).  It is
                # spent on one year at a time, so a cycle drains the newest year
                # instead of taking max_cycle_pages from every year in the range.
                cycle_allowance = cfg.max_cycle_pages if cfg.max_cycle_pages and cfg.max_cycle_pages > 0 else 0

            fetch_failed = False
            drained_probes = 0
            floor_year_finished = False

            # Year loop — newest year first, one year at a time: a year is
            # drained until it has no pages left before the next year down is
            # touched, and the cycle stops once its allowance is spent.
            # A single failed year is not an outage: the year listings are
            # independent, and one broken listing (a 5xx on a deep page, a parse
            # change on one year) must not abandon a cycle that the year below can
            # still serve.  Two consecutive failures are evidence of a run-wide
            # outage, and latest cannot resume next cycle (no cursors), so stop
            # rather than spend the run timeout on years that cost a full request
            # timeout each.  This holds after the first dated page too: an outage
            # that begins mid-window cannot be paced, because a failed year
            # collects no games and so never advances the month span.
            failed_years_in_a_row = 0
            undated_listing = False
            for scan_year in range(current_year, cutoff_year - 1, -1):
                if is_cancelled(cancel_event):
                    break
                if latest_mode and not date_mode:
                    if pages_this_cycle >= max_pages_cfg:
                        break
                elif date_mode:
                    if cfg.max_cycle_months and _month_span(max_month_seen, min_month_seen) >= cfg.max_cycle_months:
                        break
                    if latest_mode and failed_years_in_a_row >= 2:
                        logger.warning(
                            "Latest date scan: {} consecutive years failed to browse — stopping the descent",
                            failed_years_in_a_row,
                        )
                        break
                    if latest_mode and undated_listing:
                        logger.warning(
                            "Latest date scan: a whole listing carried no release dates — stopping the descent "
                            "before it crawls every year",
                        )
                        break
                else:
                    if max_pages_cfg > 0 and remaining_pages <= 0:
                        break
                    if cycle_allowance > 0 and pages_this_cycle >= cycle_allowance:
                        break
                start_page = 1 if latest_mode else db.get_last_scanned_page(platform, scan_year) + 1
                try:
                    if date_mode:
                        # Date bounds own the stop: the floor and, when paced,
                        # the month cutoff derived from the cycle's own anchor.
                        # Latest uses the same call shape as backlog date mode;
                        # it differs only in never reading or writing cursors.
                        per_call_max = 0
                        month_span_left = 0
                        cutoff_date = floor_cutoff
                        if max_month_seen is not None and cfg.max_cycle_months:
                            # Later calls: derive the pace cutoff from the cycle's
                            # own anchor so a new year's listing cannot re-anchor it
                            # and overshoot the allowance by up to a month.  With
                            # pacing off (0) the floor is the only bound.
                            pace_month = subtract_months(max_month_seen, cfg.max_cycle_months)
                            # A pace older than the catalogue can only be a config
                            # extreme; leaving the cutoff unset keeps the floor (or
                            # no bound) in charge instead of sending a date no page
                            # can meet.
                            if pace_month >= EARLIEST_BROWSE_YEAR * 100 + 1:
                                pace_cutoff = year_month_to_date(pace_month)
                                cutoff_date = max(pace_cutoff, cutoff_date) if cutoff_date else pace_cutoff
                        elif max_month_seen is None:
                            # First call of the cycle: the client anchors the month
                            # span on this call's newest page.
                            month_span_left = cfg.max_cycle_months
                    elif latest_mode:
                        # Depth-driven fallback: leave a page for the years below
                        # when one is still in the window.  A newest year deeper
                        # than the depth budget would otherwise consume it all,
                        # and the previous year is the only listing a December
                        # release with January scores appears in.
                        reserve = 1 if scan_year > cutoff_year else 0
                        per_call_max = _latest_call_depth(max_pages_cfg - pages_this_cycle, reserve)
                        cutoff_date = None
                        month_span_left = 0
                    else:
                        # Allowance for this call: the smaller of the remaining
                        # budget and what is left of the cycle allowance (0 = no
                        # limit on either).
                        allowance_left = cycle_allowance - pages_this_cycle if cycle_allowance > 0 else 0
                        per_call_max = remaining_pages if max_pages_cfg > 0 else 0
                        if cycle_allowance > 0 and (per_call_max == 0 or allowance_left < per_call_max):
                            per_call_max = allowance_left
                        cutoff_date = None
                        month_span_left = 0

                    year_games = mc.scan_recent_games(
                        platform,
                        # The client keeps its own parameter name; only config keys
                        # and pipeline API were renamed.
                        cache_pages_hours=cfg.cache_hours,
                        cutoff_date=cutoff_date,
                        cancel_event=cancel_event,
                        start_page=start_page,
                        show_progress=True,
                        year=scan_year if scan_order == "new" else None,
                        max_pages=per_call_max,
                        max_month_span=month_span_left,
                    )
                    if floor_month:
                        year_games = _games_at_or_after_floor(year_games, floor_month)
                    browse_games.extend(year_games)
                    last_page = mc._recent_games_last_page if isinstance(mc._recent_games_last_page, int) else 0
                    pages_scanned = last_page - start_page + 1 if last_page >= start_page else 0

                    # Only real pages advance the year's progress row.  An
                    # exhausted year keeps its last real page so the next cycle
                    # probes the same page instead of creeping forward and
                    # spending the budget on empty fetches.
                    if pages_scanned > 0 and not latest_mode:
                        db.set_last_scanned_page(platform, scan_year, last_page)

                    # Only a probe the client confirmed as an empty listing counts
                    # as drained; an outright fetch failure must never be read as
                    # the end of a year's listings, and a zero-page probe that is
                    # neither (scan stopped early) must not block the drained
                    # reset either.
                    # A failure anywhere in the year disqualifies the whole sweep,
                    # not just a zero-page probe.
                    if getattr(mc, "_recent_games_failed", None) is True:
                        fetch_failed = True
                        failed_years_in_a_row += 1
                    else:
                        failed_years_in_a_row = 0
                    if pages_scanned == 0 and getattr(mc, "_recent_games_exhausted", None) is True:
                        drained_probes += 1

                    pages_this_cycle += pages_scanned

                    if date_mode:
                        for game in year_games:
                            game_month = month_of(game.get("release_date"))
                            if game_month is None:
                                continue
                            if min_month_seen is None or game_month < min_month_seen:
                                min_month_seen = game_month
                            if max_month_seen is None or game_month > max_month_seen:
                                max_month_seen = game_month
                        if (
                            latest_mode
                            and max_month_seen is None
                            and pages_scanned > 0
                            and getattr(mc, "_recent_games_exhausted", None) is True
                        ):
                            # The year was drained end to end and still carried no
                            # release date, and no earlier year in this cycle carried
                            # one either, so no date bound can ever fire.  Stop here
                            # instead of crawling every year down to the bound.  A
                            # listing that is undated *and* hits the client's page cap
                            # is not detected here; the post-loop warning still
                            # reports it.  The latch is never cleared, so a dated year
                            # below this one is not probed in this cycle: that is the
                            # accepted cost of not crawling the whole window.
                            undated_listing = True
                        cutoff_reached = getattr(mc, "_recent_games_cutoff_reached", False) is True
                        if cutoff_reached:
                            cutoff_stopped = True
                        # Only a stop at the ABSOLUTE floor means the sweep reached
                        # max_date.  The client reports the effective cutoff that
                        # fired, so a pace (month-span) stop inside the floor year
                        # is not mistaken for completion — that would reset every
                        # cursor and re-sweep forever without ever reaching the
                        # floor.  A floor year that ends by listing exhaustion is
                        # also complete (a January floor can never be met by an
                        # all-before-floor page inside its own year).
                        floor_stop = cutoff_reached and getattr(mc, "_recent_games_stop_cutoff", None) == floor_cutoff
                        empty_floor_year = (
                            pages_scanned == 0
                            and getattr(mc, "_recent_games_failed", None) is not True
                            and not is_cancelled(cancel_event)
                        )
                        if scan_year == cutoff_year and (
                            floor_stop or getattr(mc, "_recent_games_exhausted", None) is True or empty_floor_year
                        ):
                            floor_year_finished = True
                        # Break only when THIS call stopped on a date bound.  A
                        # floor-level stop above the floor year means that year's
                        # listing was mis-indexed (all its pages look older than the
                        # floor); descending lets the floor year complete the sweep
                        # instead of probing the same page forever.  That year is
                        # skipped, so report it rather than losing it silently.
                        if floor_stop and scan_year != cutoff_year:
                            logger.warning(
                                "Browse year {}: its pages all look older than max_date — that "
                                "listing looks mis-indexed, so no games are queued for it this cycle",
                                scan_year,
                            )
                        if cutoff_reached and not (floor_stop and scan_year != cutoff_year):
                            break

                    if not date_mode and max_pages_cfg > 0:
                        remaining_pages -= pages_scanned
                except Exception:
                    fetch_failed = True
                    failed_years_in_a_row += 1
                    logger.exception("Scan failed for year {} — will retry next cycle", scan_year)

            if date_mode and pages_this_cycle > 0 and max_month_seen is None and not is_cancelled(cancel_event):
                # No page carried a parsable release date at or after the floor, so no
                # date bound could fire and the client drained the listings instead.
                # Say so: this is the silent-crawl shape (site markup change), not a
                # quiet site.
                logger.warning(
                    "Browse pages carried no usable release dates — the date window cannot bound this {} cycle; "
                    "check that the site still publishes release dates",
                    "latest" if latest_mode else "backlog",
                )
            elif (
                latest_mode
                and date_mode
                and not floor_month
                and not fetch_failed
                and pages_this_cycle == 0
                and not is_cancelled(cancel_event)
            ):
                # Nothing at all was listed down to the bound.  For a floorless
                # window that bound is synthetic, so the paced window may lie
                # below it — say which year the descent gave up at.
                logger.warning(
                    "Latest date scan: no listing was found from {} down to {} — the paced window may lie "
                    "below it; set max_date or use backlog mode",
                    scan_year_anchor,
                    cutoff_year,
                )

            if date_mode and not latest_mode:
                # A date sweep is complete once the floor year itself is finished
                # (exhausted or date-bounded), or when no page at all was fetched
                # and every year probed empty.  A fetch failure never counts.
                sweep_complete = False
                if not fetch_failed and not is_cancelled(cancel_event):
                    range_empty = pages_this_cycle == 0 and drained_probes > 0
                    sweep_complete = floor_year_finished or range_empty
                if sweep_complete:
                    reached = year_month_to_date(min_month_seen)[:7] if min_month_seen else "no pages"
                    logger.info(
                        "Backlog complete — reached {} (floor {}), restarting from the newest year",
                        reached,
                        year_month_to_date(floor_month)[:7],
                    )
                    db.reset_progress(platform, scan_order)
            elif not latest_mode:
                # A full pass that found no pages anywhere means the range is fully
                # drained: restart from the newest year so new releases are picked up.
                if pages_this_cycle == 0 and drained_probes > 0 and not fetch_failed and not is_cancelled(cancel_event):
                    logger.info(
                        "Backlog drained — no pages left in {}..{}, restarting from the newest year",
                        cutoff_year,
                        current_year,
                    )
                    db.reset_progress(platform, scan_order)

            # ── Shared: process browse results into pending queue ──
            if browse_games:
                thresholds = {
                    "min_criticscore": cfg.min_criticscore,
                    "min_criticscore_reviews": cfg.min_criticscore_reviews,
                    "min_user_score": cfg.min_user_score,
                    "min_user_reviews": cfg.min_user_reviews,
                }
                new_pending = _process_browse_games(
                    browse_games,
                    platform,
                    db,
                    thresholds,
                    # A row is inserted already verified, so it sits in the
                    # source-matching phase from the start and needs that window.
                    max_queue_days=cfg.fitgirl_max_queue_days,
                    reject_title=cfg.reject_title,
                    mc=mc,
                    cache_details_days=cfg.cache_details_days,
                    cache_pages_hours=cfg.cache_hours,
                    cancel_event=cancel_event,
                )
                if new_pending:
                    logger.info(
                        "{} of {} collected games passed filters and detail verification — added to pending queue",
                        new_pending,
                        len(browse_games),
                    )

            # ── Shared: scrape-health check ──
            if not is_cancelled(cancel_event) and not browse_games and cfg.notify_on_scrape_failure:
                # A year the client confirmed as drained is expected to yield no
                # games, so an empty browse result is not evidence of a scrape
                # failure — unless this sweep also had a real fetch failure, which
                # must always be reported.  A cycle that ended on a deliberate date
                # bound (date mode) is expected to produce nothing too.  Otherwise,
                # if we have cached browse
                # data, stale data is fine too.  Known limitation: a range that
                # stays empty after a drained reset probes from page 1 again, and
                # the client cannot confirm a drain from page 1, so a genuinely
                # empty range can notify once per cycle until a fetch credits a
                # page.  Closing that needs durable drained state (schema change).
                drained = not fetch_failed and (getattr(mc, "_recent_games_exhausted", None) is True or cutoff_stopped)
                # An empty cached row is not usable browse data, so test
                # truthiness rather than presence.
                cached_exists = (
                    drained
                    or bool(mc._cache.get_browse_page(platform, 1, ttl_hours=cfg.cache_hours, year=0))
                    or bool(
                        mc._cache.get_browse_page(
                            platform,
                            1,
                            ttl_hours=cfg.cache_hours,
                            year=datetime.datetime.now(tz=datetime.UTC).year,
                        )
                    )
                )
                if not cached_exists:
                    _diagnose_and_notify_scrape(
                        notifier,
                        _check_scrape_health(),
                        "Metacritic browse returned no games",
                    )
        # After the browse step, re-verify every pending game against
        # the real Metacritic detail page.  Browse-page Nuxt data does
        # NOT carry standard 0\u2013100 metascores or 0\u201310 user scores
        # \u2014 the \"score\" fields are internal browse-only metrics.
        # Games whose detail-page scores fail the configured thresholds
        # are removed from pending.
        pending_games = db.get_pending(platform=platform, sort_order=cfg.sort_order)
        if browse_games:
            logger.info(
                "Pending queue: {} total (including previous cycles)",
                len(pending_games),
            )
        if pending_games:
            total_pending = len(pending_games)
            carryover = total_pending - (new_pending if browse_games else 0)
            if carryover > 0:
                logger.info(
                    "{} pending games ({} new + {} from previous {})",
                    total_pending,
                    total_pending - carryover if browse_games else 0,
                    carryover,
                    "cycle" if carryover == 1 else "cycles",
                )
            logger.info(
                "Verifying {} pending games against cached scores and Metacritic",
                total_pending,
            )
            thresholds = {
                "min_criticscore": cfg.min_criticscore,
                "min_criticscore_reviews": cfg.min_criticscore_reviews,
                "min_user_score": cfg.min_user_score,
                "min_user_reviews": cfg.min_user_reviews,
            }
            removed = _verify_pending_scores(
                db,
                mc,
                platform,
                thresholds,
                cache_details_days=cfg.cache_details_days,
                max_verify=len(pending_games),
                reject_genre=cfg.reject_genre,
                reject_title=cfg.reject_title,
                fitgirl_max_queue_days=cfg.fitgirl_max_queue_days,
                notifier=notifier,
                cancel_event=cancel_event,
                sort_order=cfg.sort_order,
            )
            if removed:
                logger.info(
                    "Removed {} games from queue — rejected by genre or title",
                    removed,
                )

        library: Any = None
        if library_paths:
            from gamarr.library import LibraryScanner

            library = LibraryScanner(library_paths)
        # Source factory map for dispatching source config entries to source classes
        _source_factories: dict[str, type] = {
            "fitgirl": FitGirlSource,
            "freegog": FreeGOGSource,
        }

        def _build_source(entry: Any, db: Database) -> Any:
            """Create a source instance from a config entry."""
            factory = _source_factories.get(entry.name.casefold())
            if factory is None:
                raise ValueError(f"Unknown source: {entry.name}")
            kwargs: dict[str, Any] = {
                "platform": entry.platform,
                "db": db,
                # Source classes still take cache_pages_hours (their sitemap TTL);
                # only the config field was renamed.
                "cache_pages_hours": entry.cache_hours,
            }
            return factory(**kwargs)

        matched: list[dict[str, Any]] = []
        phase = 1  # Phase counter for logging

        if not is_cancelled(cancel_event) and db.has_verified_pending(platform=platform):
            match_thresholds = {
                "min_criticscore": cfg.min_criticscore,
                "min_criticscore_reviews": cfg.min_criticscore_reviews,
                "min_user_score": cfg.min_user_score,
                "min_user_reviews": cfg.min_user_reviews,
            }
            # If no download_sites provided, fall back to a default FitGirl entry
            # using the legacy parameters for backward compatibility.
            sites = download_sites if download_sites else [_make_fitgirl_entry()]
            # Per-source: index, then immediately match, so a slow or failing
            # source doesn't block matching from faster sources.
            for source_entry in sites:
                if not source_entry.enabled:
                    continue
                source = _build_source(source_entry, db)

                # Phase N: Index this source
                phase += 1
                display = _source_display(source_entry.name)
                logger.opt(colors=True).info("<cyan>━━━ Phase {}: Indexing {} ━━━</>", phase, display)
                source.fetch_sitemap(db, cancel_event=cancel_event)

                # Phase N+1: Match pending games against this source immediately
                phase += 1
                logger.opt(colors=True).info(
                    "<magenta>━━━ Phase {}: Searching for matching games on {} ━━━</>", phase, display
                )
                source_matched = _match_pending_games(
                    db,
                    qbt=qbt,
                    magnet_fetcher=_default_magnet_fetcher,
                    notifier=notifier,
                    library=library,
                    mc=mc,
                    thresholds=match_thresholds,
                    reject_keywords=source_entry.reject_keywords or None,
                    source_name=source_entry.name,
                    platform=platform,
                    sort_order=cfg.sort_order,
                )
                if source_matched:
                    matched.extend(source_matched)
                    logger.info("{} queued games found on {}", len(source_matched), display)
        else:
            if is_cancelled(cancel_event):
                logger.info("Skipping match phase — pipeline is shutting down")
            else:
                logger.info(
                    "Skipping match phase — no verified games available "
                    "(all pending games either failed score thresholds or were rejected)"
                )

        # Mandatory end-of-cycle phase
        phase += 1
        delivered = sum(1 for m in matched if m.get("result") == "Passed")
        logger.opt(colors=True).info(
            "<green>━━━ Phase {}: End of cycle ━━━</>",
            phase,
        )
        logger.info(
            "{} games browsed, {} queued, {} matched, {} delivered, {} expired",
            len(browse_games),
            new_pending,
            len(matched),
            delivered,
            expired_count,
        )

        # Process old verified games AFTER the matching phase.
        # Games that passed score verification must get a chance to
        # match against a sitemap before being aged out.  Previously
        # this ran before matching, silently removing every old game
        # from pending before it could be delivered.
        _process_aged_games(db, cfg, platform, cancel_event=cancel_event)

        # Log scan progress: latest reports the window it read (a date window or a
        # depth), date mode reports the deepest month reached, page mode keeps the
        # page-budget percentage.
        if cfg.enabled and latest_mode and date_mode:
            logger.info(
                "Latest scan: {} page(s) read, floor {}, pace {} ({} collected, {} queued)",
                pages_this_cycle,
                floor_month or "none",
                f"{cfg.max_cycle_months} month(s)" if cfg.max_cycle_months else "unpaced",
                len(browse_games),
                new_pending,
            )
        elif cfg.enabled and latest_mode:
            logger.info(
                "Latest scan: {} page(s) read of a {} page budget, window current year and previous ({} collected, {} queued)",
                pages_this_cycle,
                max_pages_cfg,
                len(browse_games),
                new_pending,
            )
        elif cfg.enabled and date_mode:
            logger.info(
                "Backlog: reached {} (floor {}, {} page(s) this cycle)",
                year_month_to_date(min_month_seen)[:7] if min_month_seen else "unknown",
                year_month_to_date(floor_month)[:7],
                pages_this_cycle,
            )
        elif cfg.enabled and cfg.max_pages and cfg.max_pages > 0:
            total_scanned = db.sum_scanned_pages(platform, cutoff_year, current_year)
            if total_scanned > 0:
                pct = min(100, round(total_scanned / cfg.max_pages * 100))
                logger.info(
                    "Progress: {} of {} pages scanned ({}%)",
                    total_scanned,
                    cfg.max_pages,
                    pct,
                )

        return matched

    logger.opt(colors=True).info("<yellow>━━━ Phase 1: Discovering games on Metacritic ━━━</>")
    try:
        # Metacritic-first acquisition: discover games via Metacritic browse,
        # then match against each configured source, and deliver to qBittorrent.
        return _run_discovery_phases(
            mc,
            db,
            cfg,
            platform,
            qbt,
            notifier,
            download_sites=download_sites,
        )
    finally:
        try:
            mc.close()
        finally:
            db.close()


def _cancel_remaining_futures(futures: list[Any], start: int) -> None:
    """Cancel all futures from *start* onwards."""
    for remaining_fut in futures[start:]:
        remaining_fut.cancel()


def _reject_by_browse_review_counts(
    game: dict[str, Any],
    min_critic_reviews: int,
    min_user_reviews: int,
) -> str | None:
    """Return a rejection reason string if browse-page review counts
    are available and below threshold, or None if the game should proceed.

    Only uses browse-page fields that are real counts (not scaled browse
    metrics).  When a count is None (missing from browse data), the check
    is skipped and the game proceeds to the detail-page verify phase.

    Args:
        game: A browse-page game dict from ``_parse_browse_page``.
        min_critic_reviews: Minimum critic reviews threshold (``min_criticscore_reviews``).
        min_user_reviews: Minimum user reviews threshold (``min_user_reviews``).

    Returns:
        ``"critic_reviews_too_few_at_browse"``, ``"user_reviews_too_few_at_browse"``,
        or ``None`` if the game should proceed.
    """
    critic_count = game.get("critic_review_count")
    if critic_count is not None and critic_count < min_critic_reviews:
        return "critic_reviews_too_few_at_browse"
    user_count = game.get("user_review_count")
    if user_count is not None and user_count < min_user_reviews:
        return "user_reviews_too_few_at_browse"
    return None


# Section headings that end the FitGirl "Repack Features" block.  Scoping the
# keyword scan this way is deliberate: the download-mirror boilerplate above the
# block mentions "non-HV repack" and "INSTALLED HV version" even on pages whose
# repack is not HV at all, and the backwards-compatibility prose below it refers
# to older releases.
_FEATURES_HEADING = "repack features"
# Headings that end the "Repack Features" block, in FitGirl's own capitalisation.
# Matched exactly: a features bullet that merely mentions a heading ("All included
# DLCs are present") must not truncate the block, and ".title()" cannot be used
# because it mangles acronyms ("Included Dlcs").
_FEATURES_STOP_HEADINGS: tuple[str, ...] = (
    "Screenshots",
    "System Requirements",
    "Backwards Compatibility",
    "Game Description",
    "Included DLCs",
    "Download Mirrors",
)
_FEATURES_MAX_CHARS = 4000


def _extract_repack_features(article_text: str | None) -> str | None:
    """Return the FitGirl "Repack Features" block, or None when it is absent.

    The block starts at the "Repack Features" heading (canonical spelling
    preferred, else a case-insensitive match) and ends at the first canonical
    following heading, or after ``_FEATURES_MAX_CHARS`` characters, whichever
    comes first — so a page without a nearby following heading cannot silently
    scan the whole article.  Stop headings are matched by their canonical
    spelling only: a features bullet that merely mentions one must not end the
    block, so a page that spells its next heading in other casing falls back to
    the length cap.

    Known limit: a bullet whose text starts with a canonical stop heading
    ("Included DLCs: ...") still ends the block, so an HV marker listed after it
    is not scanned.  Matching on a sentence/bullet boundary was tried and is not
    viable: FitGirl's collapsed text puts real headings straight after a word
    ("... lossless Backwards Compatibility ..."), so any boundary rule drops
    genuine headings.

    Args:
        article_text: The stripped ``<article>`` text, or ``None``.

    Returns:
        The feature block, or ``None`` when the page has no such heading.
    """
    if not article_text:
        return None
    lowered = article_text.lower()
    start = article_text.find("Repack Features")
    if start < 0:
        start = lowered.find(_FEATURES_HEADING)
    if start < 0:
        return None
    body = article_text[start:]
    cut = len(body)
    # Prefer the heading's capitalisation so a features bullet that merely
    # mentions "included DLCs" does not truncate the block.
    # Canonical spelling only: a features bullet that merely mentions a heading
    # ("All included DLCs are present") must not truncate the block.  Pages whose
    # following heading uses other casing simply fall back to the length cap.
    for heading in _FEATURES_STOP_HEADINGS:
        index = body.find(heading, len(_FEATURES_HEADING))
        if 0 <= index < cut:
            cut = index
    return body[: min(cut, _FEATURES_MAX_CHARS)].strip()


# Words that negate a keyword when they appear immediately before it, e.g.
# "non-HV repack" or "without HV".  FitGirl's patch-note prose uses these when
# describing how to move *off* an HV release, which must not reject the page.
_NEGATION_TOKENS: tuple[str, ...] = ("non-", "without", "no", "not")
_NEGATION_FILLER: tuple[str, ...] = (
    "a",
    "an",
    "the",
    "any",
    "need",
    "needed",
    "needing",
    "require",
    "for",
    "of",
    "to",
    "be",
    "include",
    "includes",
    "including",
    "enable",
    "enabled",
    "use",
    "uses",
    "using",
)

# Contractions hide negation from a token scan ("doesn't include an HV version").
_CONTRACTIONS: tuple[tuple[str, str], ...] = (
    ("doesn't", "does not"),
    ("can't", "can not"),
    ("isn't", "is not"),
    ("don't", "do not"),
    ("won't", "will not"),
    ("cannot", "can not"),
)
_NEGATION_WINDOW = 25


def _is_negated(text: str, start: int) -> bool:
    """Return True when the keyword at *start* is preceded by a negation.

    Looks only backwards, over the ``_NEGATION_WINDOW`` characters before the
    match, skipping articles and lightweight filler ("not an HV repack",
    "doesn't include an HV version").  Contractions are expanded first.

    A trailing negation ("HV ... not required") deliberately does NOT count: a
    forward look cannot distinguish it from a neighbouring item's negation
    ("HV bypass, DX not required"), and a missed rejection would deliver a
    repack the user excluded.

    Args:
        text: The text being scanned.
        start: Index of the keyword's first character.

    Returns:
        True when the occurrence should be ignored.
    """
    # Expand over a wider slice so a contraction straddling the window edge is
    # still recognised, then keep only the window's worth of characters.
    wider = text[max(0, start - _NEGATION_WINDOW - 12) : start].lower()
    # FitGirl is a WordPress site: typographic apostrophes are as common as
    # ASCII ones, and an unnormalised U+2019 splits "doesn't" into two tokens.
    for apostrophe in ("\u2019", "\u02bc", "\u00b4"):
        wider = wider.replace(apostrophe, "'")
    for contraction, expanded in _CONTRACTIONS:
        wider = wider.replace(contraction, expanded)
    # Catch the unlisted "n't" forms (shouldn't, wasn't, wouldn't) generically.
    wider = wider.replace("n't", " not")
    before = wider[-_NEGATION_WINDOW:]
    tokens = [token for token in re.split(r"[^a-z0-9-]+", before) if token]
    # Articles and prepositions may sit between the negation and the keyword
    # ("not an HV repack", "no need for HV"), so skip over them.
    while tokens and tokens[-1] in _NEGATION_FILLER:
        tokens.pop()
    # No forward look: "HV bypass, DX not required" would otherwise read as a
    # negated keyword and deliver an HV repack.  Backward negation already covers
    # the "no HV required" phrasing that matters.
    return bool(tokens) and tokens[-1] in _NEGATION_TOKENS


def _keyword_match(text: str | None, keywords: list[str] | None) -> str | None:
    """Return the first matching keyword that is not negated, trying keywords in order.

    Matching is case-insensitive and anchored on word boundaries, so ``hv``
    matches "HV version" but not "shevron".  An occurrence is skipped when the
    25 characters immediately before it end with a negation token
    (``non-``, ``without``, ``no``, ``not``) — that is the difference between
    "a new non-HV repack" (not this repack) and "an HV version" (this repack).

    Args:
        text: The text to scan, or ``None``.
        keywords: Configured reject keywords, or ``None``.

    Returns:
        The matched keyword, lowercased, or ``None``.
    """
    if not text or not keywords:
        return None
    for keyword in keywords:
        needle = keyword.strip()
        if not needle:
            continue
        pattern = re.compile(rf"\b{re.escape(needle)}\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            if _is_negated(text, match.start()):
                continue
            return needle.lower()
    return None


def _title_matches_reject(title: str, reject_title: list[str] | None) -> bool:
    """Return True if *title* case-insensitively matches any reject_title entry."""
    if not reject_title:
        return False
    title_lower = title.lower()
    return any(term.lower() in title_lower for term in reject_title)


def _source_display(name: str) -> str:
    """Return the display-cased form of a source name."""
    return SOURCE_DISPLAY.get(name, name.title())


def _game_passes_thresholds(game: dict[str, Any], thresholds: dict[str, Any]) -> bool:
    """Check if a browse-page game dict passes all score thresholds.

    Note: browse-page ``score`` and ``user_rating`` fields are internal
    metrics on a different scale (e.g. 1478), not real 0-100 metascores
    or 0-10 user scores.  Real score verification happens during the
    detail-page phase.  Review count filtering is now handled by
    ``_reject_by_browse_review_counts``, called from
    ``_browse_game_reject_reason``.
    """
    metascore = game.get("score")
    user_score = game.get("user_rating")
    if metascore is None or user_score is None:
        return False
    return all(
        [
            metascore >= thresholds["min_criticscore"],
            user_score >= thresholds["min_user_score"],
        ]
    )


def _is_game_known(slug: str, db: Database, *, known_slugs: set[str] | None = None) -> bool:
    """Return True if *slug* is already processed or pending.

    Uses an optional pre-computed set for O(1) lookup, falling back
    to individual DB queries when no set is provided.
    """
    if known_slugs is not None:
        return slug in known_slugs
    return db.is_processed("metacritic", f"mc:{slug}") or db.is_pending(slug)


def _is_game_eligible(
    game: dict[str, Any],
    db: Database,
    thresholds: dict[str, Any],
    *,
    known_slugs: set[str] | None = None,
) -> bool:
    """Return True if *game* passes the cheap, network-free filters.

    Passing only means the game is worth a detail-page verification:
    ``_process_browse_games`` still checks its real scores before queueing it.

    Args:
        known_slugs: Pre-computed set of known slugs (processed or pending).
            When provided, avoids per-game DB round-trips for the is_processed
            and is_pending checks — a single batch query replaces N+1.
    """
    slug = game.get("slug", "")
    title = game.get("title", "")
    if not slug or not title:
        return False
    if _is_game_known(slug, db, known_slugs=known_slugs):
        return False
    return _game_passes_thresholds(game, thresholds)


def _calculate_pending_expiry(max_queue_days: int) -> str:
    """Return an ISO-format expiry timestamp for a pending game.

    When *max_queue_days* is 0 or negative, uses an indefinite
    (far-future) expiry.
    """
    now = datetime.datetime.now(tz=datetime.UTC)
    if max_queue_days <= 0:
        return (now + datetime.timedelta(days=_INDEFINITE_DAYS)).isoformat()
    return (now + datetime.timedelta(days=max_queue_days)).isoformat()


def _process_browse_games(
    browse_games: list[dict[str, Any]],
    platform: str,
    db: Database,
    thresholds: dict[str, Any],
    *,
    max_queue_days: int = 30,
    reject_title: list[str] | None = None,
    mc: Any = None,
    cache_details_days: int = 7,
    cache_pages_hours: int = 6,
    cancel_event: CancelSignal | None = None,
) -> int:
    """Evaluate browse-page games and insert qualifying ones into the pending queue.

    Browse listings from ``_parse_browse_page`` carry their own ``score`` and
    ``user_rating`` fields; those only act as a pre-filter.  Games that survive
    ``_browse_game_reject_reason`` — already-processed and already-pending games
    are rejected there — are verified against the Metacritic detail page, and
    only the ones whose real scores pass *thresholds* are inserted into
    ``pending_games``, with the detail-page scores, review counts, genres and
    ``score_checks_passed`` set.  Games that fail, or whose scores are not
    published yet, are not inserted — they are not marked known either, so the
    next browse pass re-evaluates them.

    Args:
        browse_games: List from ``_parse_browse_page``.
        platform: Target platform name.
        db: Database instance.
        thresholds: Dict with ``min_criticscore`` keys.
        max_queue_days: How many days to keep the game pending before expiry.
        reject_title: Titles matching any of these are skipped.
        mc: Metacritic client used for the detail-page verification.
        cache_details_days: TTL in days for the detail-page cache.
        cache_pages_hours: TTL in hours for the browse-page cache.

    Returns:
        Number of new pending games added.
    """
    known_slugs = db.get_known_slugs(source="metacritic")
    seen_slugs: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for game in browse_games:
        reject_reason = _browse_game_reject_reason(
            game,
            db,
            thresholds,
            reject_title=reject_title,
            known_slugs=known_slugs,
        )
        if reject_reason is not None:
            logger.debug("Skipping '{}' — {}", game.get("title", ""), reject_reason)
            continue
        g_slug = game.get("slug", "")
        # Skip duplicate slugs within this batch — the DB layer would
        # silently no-op, but we avoid the unnecessary roundtrip.
        if g_slug in seen_slugs:
            continue
        seen_slugs.add(g_slug)
        candidates.append(game)

    if not candidates:
        return 0

    details = _lookup_details(
        candidates,
        mc,
        platform,
        cache_details_days,
        cache_pages_hours,
        cancel_event=cancel_event,
    )
    if is_cancelled(cancel_event):
        # A shutdown during the lookups must not queue games from an abandoned cycle.
        logger.info("Browse phase cancelled — discarding {} verified candidate(s)", len(candidates))
        return 0

    return _queue_verified_candidates(
        db,
        candidates,
        details,
        platform,
        thresholds,
        max_queue_days,
        cancel_event,
    )


def _queue_verified_candidates(
    db: Database,
    candidates: list[dict[str, Any]],
    details: list[Any],
    platform: str,
    thresholds: dict[str, Any],
    max_queue_days: int,
    cancel_event: CancelSignal | None,
) -> int:
    """Insert verified candidates, stopping early when the cycle is cancelled.

    Args:
        db: Database instance.
        candidates: Browse games that survived the cheap pre-filters.
        details: One ``ScoreResult`` (or ``None``) per candidate, in order.
        platform: Platform key for the pending rows.
        thresholds: Score thresholds used to verify each detail result.
        max_queue_days: Expiry window for newly queued rows.
        cancel_event: Cancellation signal checked before each insert.

    Returns:
        The number of rows queued before the loop finished or was cancelled.
    """
    new_count = 0
    for game, detail in zip(candidates, details, strict=True):
        if is_cancelled(cancel_event):
            logger.info("Cancelled while queueing verified games — dropping the remaining candidates")
            return new_count
        if _queue_verified_game(db, game, detail, platform, thresholds, max_queue_days):
            new_count += 1
    return new_count


def _queue_verified_game(
    db: Database,
    game: dict[str, Any],
    detail: Any,
    platform: str,
    thresholds: dict[str, Any],
    max_queue_days: int,
) -> bool:
    """Queue one candidate when its detail scores pass, else skip it.

    Args:
        db: Database instance.
        game: The browse game dict.
        detail: The candidate's ``ScoreResult``, or ``None`` when lookup failed.
        platform: Platform identifier.
        thresholds: The configured score thresholds.
        max_queue_days: Expiry window for the queued row.

    Returns:
        True when a row was inserted.
    """
    g_slug = game.get("slug", "")
    g_title = game.get("title", "")
    if detail is None:
        logger.debug("Skipping '{}' — detail lookup failed, retried on the next pass", g_title)
        return False
    if not _real_scores_pass_thresholds(detail, thresholds):
        logger.debug("Skipping '{}' — detail scores do not pass the thresholds yet", g_title)
        return False

    expires_at = _calculate_pending_expiry(max_queue_days)
    inserted = db.record_pending(
        slug=g_slug,
        game_title=g_title,
        platform=platform,
        metascore=detail.metascore,
        metascore_reviews=detail.metascore_review_count,
        user_score=detail.user_score,
        user_reviews=detail.user_review_count,
        genres=detail.genres,
        release_date=detail.release_date or game.get("release_date"),
        expires_at=expires_at,
        score_checks_passed=True,
    )
    if inserted:
        logger.debug("Added pending game: '{}' (slug: {}, expires {})", g_title, g_slug, expires_at)
    return inserted


_QUEUE_LOOKUP_WORKERS = 10


def _lookup_details(
    candidates: list[dict[str, Any]],
    mc: Any,
    platform: str,
    cache_details_days: int,
    cache_pages_hours: int,
    cancel_event: CancelSignal | None = None,
) -> list[Any]:
    """Look up every candidate's detail page, preserving candidate order.

    The lookups are network-bound, so they run through a small pool rather than
    one at a time — a cold cache with many new candidates would otherwise
    serialise hundreds of requests and can exceed the cycle watchdog. Each
    lookup is isolated: one malformed page becomes ``None`` for that candidate
    instead of aborting the cycle.

    Args:
        candidates: Browse games that survived the cheap filters.
        mc: The Metacritic client.
        platform: Platform identifier.
        cache_details_days: TTL for the detail-page cache.
        cache_pages_hours: TTL for the browse-page cache.

    Notes:
        Cancellation stops tasks that have not started; a lookup already in
        flight runs to completion, so the pool drains within the HTTP read
        timeout even after a cancel.  Callers treat that as the shutdown bound
        rather than assuming an immediate stop.

    Returns:
        One ``ScoreResult`` or ``None`` per candidate, in the same order.
    """

    def lookup(game: dict[str, Any]) -> Any:
        if is_cancelled(cancel_event):
            return None
        try:
            # direct_only: the slug came from the browse listing, so the
            # fallback browse scan would only re-fail the same lookup.
            return mc.lookup_game(
                game.get("title", ""),
                platform,
                cache_details_days=cache_details_days,
                cache_pages_hours=cache_pages_hours,
                direct_only=True,
                slug=game.get("slug", ""),
            )
        except Exception:
            logger.exception("Detail lookup failed for '{}' — treating as unverified", game.get("title", ""))
            return None

    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=_QUEUE_LOOKUP_WORKERS) as pool:
        return list(pool.map(lookup, candidates))


def _browse_game_reject_reason(
    game: dict[str, Any],
    db: Database,
    thresholds: dict[str, Any],
    *,
    reject_title: list[str] | None,
    known_slugs: set[str],
) -> str | None:
    """Return why a browse game is skipped before detail verification.

    Covers the cheap, network-free filters only: already-known slugs, score
    eligibility, ``reject_title`` and the browse-page review counts.

    Args:
        game: One browse-page game dict.
        db: Database used for the processed/pending lookup.
        thresholds: The configured score thresholds.
        reject_title: Title substrings to reject, or ``None``.
        known_slugs: Slugs already processed or pending.

    Returns:
        A short reason when the game is skipped, ``None`` when it should be
        verified against its detail page.
    """
    if not _is_game_eligible(game, db, thresholds, known_slugs=known_slugs):
        return "already known or ineligible"
    if _title_matches_reject(game.get("title", ""), reject_title):
        return "matches reject_title"
    return _reject_by_browse_review_counts(
        game,
        min_critic_reviews=thresholds.get("min_criticscore_reviews", 0),
        min_user_reviews=thresholds.get("min_user_reviews", 0),
    )


def _log_verify_progress(verified: int, max_verify: int, total: int) -> None:
    """Log periodic progress during score verification (DEBUG level only)."""
    if verified % 100 == 0:
        logger.debug(
            "Verifying pending scores for {} of {} games...",
            verified,
            max_verify if max_verify < total else total,
        )


def _scores_present(
    result: Any,
) -> bool:
    """Return True if the ScoreResult has at least one valid score (> 0)."""
    return result is not None and (
        (result.metascore is not None and result.metascore > 0.0)
        or (result.user_score is not None and result.user_score > 0.0)
    )


def _check_score_threshold(
    value: float | None,
    threshold: float,
) -> bool | None:
    """Compare a score value against a threshold.

    Returns True/False when the value passes/fails, or None when the
    value is absent (no review data to check).  A score of ``0.0`` is
    treated as a real value that should be checked against the threshold.
    """
    if value is None:
        return None
    return value >= threshold


def _fails_review_count_check(
    score_value: float | None,
    review_count: int | None,
    threshold: int,
) -> bool:
    """Check whether a review count fails its threshold when a score is present.

    When *score_value* is present (> 0) but *review_count* is missing or
    zero, and *threshold* is > 0, the check fails — missing review data
    cannot be assumed to pass.

    When *score_value* itself is absent (None), the review count
    check is skipped — there is no score data in this category to
    verify reviews for.  A score of ``0.0`` does NOT skip the check —
    it means the data exists (zero score) and the review count should
    still be verified.

    Returns True when the game should be rejected due to insufficient
    review data.
    """
    if threshold <= 0:
        return False
    if score_value is None:
        return False
    return bool(review_count is None or review_count <= 0)


def _any_thresholded_score_absent(result: Any, thresholds: dict[str, Any]) -> bool:
    """Return True when any score is None (TBD) while its threshold is > 0.

    Prevents TBD games (where metascore or user_score is None) from
    silently bypassing score thresholds.  A score of 0.0 means "unrated
    but exists" and should stay pending for re-verification — only strict
    None triggers rejection.
    """
    return (thresholds.get("min_criticscore", 0) > 0 and result.metascore is None) or (
        thresholds.get("min_user_score", 0) > 0 and result.user_score is None
    )


def _real_scores_pass_thresholds(
    result: Any,
    thresholds: dict[str, Any],
) -> bool:
    """Check a ScoreResult (from the detail page) against configured thresholds.

    When a score value is ``None`` (TBD — not yet reviewed) and its
    corresponding threshold is ``> 0``, the check **fails** immediately
    — this prevents unreviewed games from silently bypassing score
    thresholds.

    A score value of ``0.0`` is treated as real data and is checked against
    its threshold.  Unlike ``None`` (TBD), a ``0.0`` score will fail if it
    does not meet the configured minimum.

    However, when a *review count* is None/0 and the corresponding
    threshold is > 0, the check **fails** — ``None`` means "no reviews",
    not "skip the check".  This prevents games with unreviewed pages
    (e.g. newly released games showing "TBD") from bypassing
    ``min_user_reviews`` and ``min_criticscore_reviews``.

    Returns True when the existing checks all pass.
    """
    if result is None:
        return False

    # Reject when a score value is None (TBD — not reviewed) but a
    # non-zero threshold is configured.  This prevents TBD games
    # (metascore=None, like WEBFISHING) from silently bypassing
    # min_criticscore.
    if _any_thresholded_score_absent(result, thresholds):
        return False

    if _fails_review_count_check(
        score_value=result.user_score,
        review_count=result.user_review_count,
        threshold=thresholds.get("min_user_reviews", 0),
    ):
        return False

    if _fails_review_count_check(
        score_value=result.metascore,
        review_count=result.metascore_review_count,
        threshold=thresholds.get("min_criticscore_reviews", 0),
    ):
        return False

    checks: list[bool] = [
        c
        for c in [
            _check_score_threshold(result.metascore, thresholds["min_criticscore"]),
            _check_score_threshold(result.metascore_review_count, thresholds["min_criticscore_reviews"]),
            _check_score_threshold(result.user_score, thresholds["min_user_score"]),
            _check_score_threshold(result.user_review_count, thresholds["min_user_reviews"]),
        ]
        if c is not None
    ]
    if not checks:
        return False  # No review data — can't verify
    return all(checks)


def _reject_by_genre(
    game: Any,
    result: Any,
    reject_genre: list[str] | None,
) -> str | None:
    """Return the first genre that matched *reject_genre* (case-insensitive substring), or *None*.

    Substring match means ``reject_genre=["RPG"]`` matches ``"Action RPG"``,
    ``"Western RPG"``, ``"JRPG"``, and ``"RPG"`` itself.
    A more specific entry like ``"Western RPG"`` only matches genres
    containing that exact substring.
    """
    if not (result is not None and reject_genre and getattr(result, "genres", None)):
        return None
    genre_lower = [g.lower() for g in result.genres]
    for term in reject_genre:
        term_lower = term.lower()
        for i, genre in enumerate(genre_lower):
            if term_lower in genre:
                logger.debug(
                    "Removing '{}' — genre '{}' matches reject genre '{}'",
                    game.game_title,
                    result.genres[i],
                    term,
                )
                return str(result.genres[i])
    return None


def _reject_by_title(
    game: Any,
    reject_title: list[str] | None,
) -> str | None:
    """Return the first reject_title entry that matched the game title, or None.

    Case-insensitive substring match means ``reject_title=["Remake"]`` matches
    ``"Resident Evil 4 Remake"``, ``"Remake Collection"``, etc.
    """
    if not (reject_title and game and game.game_title):
        return None
    title_lower = str(game.game_title).lower()
    for term in reject_title:
        term_lower = term.lower()
        if term_lower in title_lower:
            logger.debug(
                "Removing '{}' — title matches reject_title '{}'",
                game.game_title,
                term,
            )
            return str(term)
    return None


def _scores_fail_check(result: Any, thresholds: dict[str, Any]) -> bool:
    """Return True when scores are missing or fail the configured thresholds."""
    return not _scores_present(result) or not _real_scores_pass_thresholds(result, thresholds)


def _should_age_game(game: Any, age_recheck_weeks: int) -> bool:
    """Return True if *game* meets all criteria for age-based removal.

    A game must have been checked at least once, have a release date older
    than *age_recheck_weeks*, AND have passed score verification.
    """
    return (
        game.last_checked_at is not None
        and game.release_date is not None
        and game.score_checks_passed is True
        and _is_older_than(game.release_date, days=age_recheck_weeks * 7)
    )


def _process_aged_games(
    db: Database,
    cfg: AcquisitionConfig,
    platform: str,
    cancel_event: CancelSignal | None = None,
) -> int:
    """Mark old verified pending games as processed.

    Queries all non-expired pending games that have been checked at
    least once (``last_checked_at IS NOT NULL``), passed score
    verification (``score_checks_passed IS TRUE``), and whose
    ``release_date`` is older than ``cfg.age_recheck_weeks``.

    These games are permanently recorded with ``result="Processed"``
    and removed from the pending queue — they are skipped on the next run
    on future cycles.

    Args:

    Returns the count of games processed.
    """
    if not cfg.age_recheck_weeks:
        return 0

    pending = db.get_pending(platform=platform, sort_order=cfg.sort_order)
    processed = 0
    for game in pending:
        if is_cancelled(cancel_event):
            break
        if not _should_age_game(game, cfg.age_recheck_weeks):
            continue

        db.record_processed(
            source="metacritic",
            source_title=str(game.game_title),
            source_url=f"mc:{game.slug}",
            game_title=str(game.game_title),
            platform=str(game.platform),
            metascore=game.metascore,
            user_score=game.user_score,
            result="Processed",
            result_details=f"Game older than {cfg.age_recheck_weeks}-week threshold, not re-checked",
        )
        db.remove_pending(str(game.slug))
        logger.debug(
            "Processed '{}' \u2014 release date older than {} weeks",
            game.game_title,
            cfg.age_recheck_weeks,
        )
        processed += 1

    if processed:
        logger.info(
            "Processed {} game(s) older than {} weeks \u2014 skipping on next run",
            processed,
            cfg.age_recheck_weeks,
        )
    return processed


def _process_verify_result(
    db: Database,
    game: Any,
    result: Any,
    thresholds: dict[str, Any],
    *,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,
    fitgirl_max_queue_days: int = 60,
) -> int:
    """Process one score-check result.

    The expiry window is refreshed only when a row *newly* qualifies.  Every
    queued game is re-verified each cycle, so refreshing on every pass would
    push the window forward forever and a verified game that never matches
    would never expire - the unbounded queue growth this mode exists to stop.
    Rows queued by verify-before-queue arrive already qualified and take the
    source-matching window at insert, so this refresh only affects legacy rows
    queued unverified (and rows whose verification lapsed).

    Returns:
        -2 if the game was removed (rejected by genre or title).
        -1 if the game was kept but did not pass score thresholds
            (including when the game was not found on Metacritic).
         1 if the game was kept and scores passed thresholds.
    """
    # Captured before any write: update_pending_scores() flips this flag.
    was_verified = game.score_checks_passed is True
    matched_genre = _reject_by_genre(game, result, reject_genre)
    if matched_genre is not None:
        db.record_processed(
            source="metacritic",
            source_title=str(game.game_title),
            source_url=f"mc:{str(game.slug)}",
            game_title=str(game.game_title),
            platform=str(game.platform),
            metascore=result.metascore,
            user_score=result.user_score,
            result="Failed",
            result_details=f"Game '{game.game_title}' — genre '{matched_genre}' is in reject_genre list",
        )
        db.remove_pending(str(game.slug))
        return -2

    matched_title = _reject_by_title(game, reject_title)
    if matched_title is not None:
        db.record_processed(
            source="metacritic",
            source_title=str(game.game_title),
            source_url=f"mc:{str(game.slug)}",
            game_title=str(game.game_title),
            platform=str(game.platform),
            metascore=result.metascore,
            user_score=result.user_score,
            result="Failed",
            result_details=f"Game '{game.game_title}' — title matches reject_title '{matched_title}'",
        )
        db.remove_pending(str(game.slug))
        return -2

    if result is None:
        db.touch_pending(str(game.slug))
        logger.debug(
            "Keeping '{}' in queue \u2014 game not found on Metacritic page",
            game.game_title,
        )
        return -1

    if _scores_fail_check(result, thresholds):
        db.touch_pending(str(game.slug))
        logger.debug(
            "Keeping '{}' in queue \u2014 Metacritic scores ({}, {}) below thresholds",
            game.game_title,
            result.metascore,
            result.user_score,
        )
        return -1

    db.update_pending_scores(
        slug=str(game.slug),
        metascore=result.metascore,
        metascore_reviews=result.metascore_review_count,
        user_score=result.user_score,
        user_reviews=result.user_review_count,
    )
    if not was_verified:
        db.update_pending_expiry(str(game.slug), fitgirl_max_queue_days)
    logger.debug(
        "'{}' passed score check \u2014 ({}, {}) with ({} reviews, {} reviews)",
        game.game_title,
        result.metascore,
        result.user_score,
        result.metascore_review_count,
        result.user_review_count,
    )
    return 1


def _log_batch_summary(
    checked: int, cache_hits: int, *, passed: int = 0, removed: int = 0, kept_failed: int = 0
) -> None:
    """Log a single summary line after a batch of pending game lookups.

    Args:
        checked: Number of games looked up this batch.
        cache_hits: Number of results served from the cache.
        passed: Number of games whose detail-page scores met thresholds.
        removed: Number of games removed (rejected by genre or title).
        kept_failed: Number of games kept but below score thresholds.

    Clamps subtraction: on cancellation, *cache_hits* from still-running
    futures may exceed the count of consumed results.
    """
    if checked > 0:
        logger.info(
            "Score check: {} checked, {} passed, {} kept (below thresholds), {} removed (genre/title) "
            "({} from cache, {} from Metacritic API)",
            checked,
            passed,
            kept_failed,
            removed,
            cache_hits,
            max(0, checked - cache_hits),
        )


def _safe_result(fut: Any, game: Any) -> Any:
    """Call ``fut.result()`` and return the value, or None on any exception."""
    try:
        return fut.result()
    except Exception:
        logger.exception(
            "Lookup failed for '{}' (slug={}); treating as None.",
            game.game_title,
            game.slug,
        )
        return None


def _process_verify_batch(
    db: Database,
    mc: MetacriticClient,
    platform: str,
    thresholds: dict[str, Any],
    batch: list[Any],
    max_verify: int,
    total_pending: int,
    *,
    cache_details_days: int = 7,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,
    fitgirl_max_queue_days: int = 60,
    cancel_event: CancelSignal | None = None,
) -> tuple[int, bool]:
    """Process a batch of pending game lookups concurrently.

    Args:
        db: Database instance.
        mc: MetacriticClient instance.
        platform: Platform identifier.
        thresholds: Score threshold dict.
        batch: List of pending games to look up.
        max_verify: Maximum games to verify.
        total_pending: Total pending games for progress logging.
        cache_details_days: TTL for the detail-page cache.
        reject_genre: Genre substrings to reject.
        reject_title: Title substrings to reject.
        fitgirl_max_queue_days: Days to extend pending expiry when scores pass.

    Returns:
        Tuple of ``(removed_count, any_success)`` where *any_success* is True
        if at least one lookup returned a non-None result.
    """

    removed = 0
    passed = 0
    kept_failed = 0
    any_success = False

    # Check for pre-set cancellation before submitting any work
    if is_cancelled(cancel_event):
        logger.info("Verify cancelled by shutdown signal; skipping batch")
        return removed, any_success

    mc.reset_cache_hits()  # Reset per-batch cache-hit counter (thread-safe)

    pool = ThreadPoolExecutor(max_workers=10)
    checked = 0
    try:
        futures = [
            pool.submit(
                mc.lookup_game,
                str(game.game_title),
                platform=platform,
                slug=str(game.slug),
                cache_details_days=cache_details_days,
                direct_only=True,
            )
            for game in batch
        ]

        for verified, (game, fut) in enumerate(zip(batch, futures, strict=True)):
            # Check for mid-batch cancellation
            if is_cancelled(cancel_event):
                logger.info(
                    "Verify cancelled by shutdown signal; partial results after {} games",
                    verified,
                )
                _cancel_remaining_futures(futures, verified)
                break

            checked = verified + 1
            _log_verify_progress(verified, max_verify, total_pending)
            result = _safe_result(fut, game)
            if result is not None:
                any_success = True
            verdict = _process_verify_result(
                db,
                game,
                result,
                thresholds,
                reject_genre=reject_genre,
                reject_title=reject_title,
                fitgirl_max_queue_days=fitgirl_max_queue_days,
            )
            if verdict == -2:
                removed += 1
            elif verdict == 1:
                passed += 1
            else:
                kept_failed += 1

        # Single summary line per batch instead of per-100-game progress spam.
        # Clamp subtraction: on cancellation, cache_hits from still-running
        # futures may exceed the count of consumed results.
        _log_batch_summary(checked, int(mc.cache_hits), passed=passed, removed=removed, kept_failed=kept_failed)
    finally:
        pool.shutdown(wait=True)

    return removed, any_success


def _verify_pending_scores(
    db: Database,
    mc: MetacriticClient,
    platform: str,
    thresholds: dict[str, Any],
    *,
    cache_details_days: int = 7,
    max_verify: int = 50,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,
    fitgirl_max_queue_days: int = 60,
    notifier: Any = None,
    cancel_event: CancelSignal | None = None,
    sort_order: Literal["new", "criticscore", "userscore"] | None = None,
) -> int:
    """Re-verify pending games' scores against the real Metacritic detail page.

    Browse-page Nuxt data does NOT carry standard 0\u2013100 metascores or
    0\u201310 user scores — the \"score\" fields are internal browse-only
    metrics.  Games whose detail-page scores fail the configured thresholds
    (or cannot be found at all on the detail page) are kept in the queue
    for re-verification on subsequent cycles.

    Games that pass the real-score check have their pending record updated
    with the correct scores from the detail page.

    Only the first *max_verify* games are checked per call; the rest
    remain pending for the next cycle.  This prevents a large pending
    queue (e.g. 2773 games) from generating thousands of sequential
    HTTP requests in a single cycle.

    Args:
        db: Database instance.
        mc: MetacriticClient instance.
        platform: Platform identifier (e.g. ``"pc"``).
        thresholds: Dict with score threshold keys.
        sort_order: Active browse sort order used to order the pending queue
            (see :meth:`Database.get_pending`).  ``None`` keeps the default
            database order.
        cache_details_days: TTL for the detail-page cache.
        max_verify: Maximum number of games to verify per cycle.
            Set to 0 to skip verification entirely.
        reject_genre: List of genre substrings to reject (case-insensitive).
            Games whose genre contains any entry are removed immediately.
            E.g. ``["RPG"]`` matches ``"Action RPG"``, ``"JRPG"``, etc.
        reject_title: List of title substrings to reject (case-insensitive).
            Games whose title contains any entry are removed immediately.
        fitgirl_max_queue_days: Passed through to _process_verify_result for
            expiry recalculation when scores pass.
        notifier: Optional Notifier instance. When provided and every lookup
            in the batch returns None, a scrape notification is sent.

    Returns the number of games removed.
    """
    if max_verify <= 0:
        return 0

    removed = 0
    pending = db.get_pending(platform=platform, sort_order=sort_order)
    total_pending = len(pending)

    # Collect the batch of games to check this cycle
    batch = list(pending[:max_verify])

    if not batch:
        return 0

    removed, any_success = _process_verify_batch(
        db,
        mc,
        platform,
        thresholds,
        batch,
        max_verify,
        total_pending,
        cache_details_days=cache_details_days,
        reject_genre=reject_genre,
        reject_title=reject_title,
        fitgirl_max_queue_days=fitgirl_max_queue_days,
        cancel_event=cancel_event,
    )

    if notifier is not None and not is_cancelled(cancel_event) and batch and not any_success:
        _diagnose_and_notify_scrape(
            notifier,
            _check_scrape_health(),
            "Metacritic game detail lookup failed for all games",
        )

    return removed


def _jit_verify_and_update(
    db: Database,
    mc: Any,
    thresholds: dict[str, Any] | None,
    game_title: str,
    game_slug: str,
    game_platform: str,
) -> Any:
    """Just-in-time verify a matched game's scores before delivery.

    Calls the Metacritic detail page and checks real scores against
    thresholds.  If verification passes, the pending record is updated
    with the real scores.  If it fails, the game is removed from pending.

    Returns a tuple of (metascore, user_score, metascore_reviews,
    user_reviews, genres, must_play, description) on success (real
    scores pass thresholds), or ``None`` when the game was removed
    (confirmed failing scores) or kept pending (transient Metacritic
    failure).  When *mc* or *thresholds* is not provided, returns an
    empty tuple ``()`` signalling "use original scores, skip
    verification".
    """
    if mc is None or thresholds is None:
        return ()
    jit_result = mc.lookup_game(
        game_title,
        platform=game_platform,
        slug=game_slug,
        direct_only=True,
    )
    if jit_result is None:
        # Transient Metacritic failure (timeout, DNS, etc.) \u2014 keep
        # pending for re-verification on the next cycle.  Removing the
        # game silently loses a verified, scored, and matched entry.
        logger.debug(
            "Keeping '{}' pending \u2014 Metacritic unavailable during JIT verify",
            game_title,
        )
        return None
    if not (_scores_present(jit_result) and _real_scores_pass_thresholds(jit_result, thresholds)):
        db.record_processed(
            source="metacritic",
            source_title=game_title,
            source_url=f"mc:{game_slug}",
            game_title=game_title,
            platform=game_platform,
            metascore=jit_result.metascore,
            user_score=jit_result.user_score,
            result="Failed",
            result_details="JIT verify: scores below thresholds",
        )
        db.remove_pending(game_slug)
        logger.debug(
            "Skipped '{}' \u2014 scores missing or below thresholds",
            game_title,
        )
        return None
    # Update pending record with real detail-page scores
    db.update_pending_scores(
        slug=game_slug,
        metascore=jit_result.metascore,
        metascore_reviews=jit_result.metascore_review_count,
        user_score=jit_result.user_score,
        user_reviews=jit_result.user_review_count,
    )
    return (
        jit_result.metascore,
        jit_result.user_score,
        jit_result.metascore_review_count,
        jit_result.user_review_count,
        getattr(jit_result, "genres", None),
        getattr(jit_result, "must_play", None),
        getattr(jit_result, "description", None),
    )


def _deliver_with_jit_verify(
    db: Database,
    mc: Any,
    thresholds: dict[str, Any] | None,
    game_title: str,
    game_slug: str,
    game_platform: str,
    game_metascore: float | None,
    game_user_score: float | None,
    game_metascore_reviews: int | None,
    game_user_reviews: int | None,
    game_release_date: str | None,
    *,
    qbt: Any,
    magnet_fetcher: Callable[[str], str | None],
    notifier: Any,
    best: dict[str, Any],
    source_name: str = "fitgirl",
) -> dict[str, Any] | None:
    """Verify scores just-in-time, then deliver the match.

    If *mc* and *thresholds* are provided, looks up the real Metacritic
    detail-page scores before delivering.  Games with wrong browse-only
    metrics (e.g. 1478.0) are skipped and removed from pending.

    Returns the result dict on successful delivery, or ``None`` if the
    game was removed due to missing/failing scores.
    """
    jit_scores = _jit_verify_and_update(
        db,
        mc,
        thresholds,
        game_title,
        game_slug,
        game_platform,
    )
    # ``None`` → verification failed, skip delivery
    # ``()`` → no mc/thresholds, use original scores
    if jit_scores is None:
        return None
    game_genres = None
    game_must_play = None
    if jit_scores:
        game_metascore, game_user_score = jit_scores[0], jit_scores[1]
        game_metascore_reviews, game_user_reviews = jit_scores[2], jit_scores[3]
        game_genres = jit_scores[4]
        game_must_play = jit_scores[5]

    return _deliver_match(
        db,
        qbt=qbt,
        magnet_fetcher=magnet_fetcher,
        notifier=notifier,
        best=best,
        game_slug=game_slug,
        game_title=game_title,
        game_platform=game_platform,
        game_metascore=game_metascore,
        game_user_score=game_user_score,
        game_metascore_reviews=game_metascore_reviews,
        game_user_reviews=game_user_reviews,
        game_genres=game_genres,
        game_must_play=game_must_play,
        game_release_date=game_release_date,
        source_name=source_name,
    )


def _match_pending_games(
    db: Database,
    *,
    qbt: Any = None,
    magnet_fetcher: Callable[[str], str | None] | None = None,
    notifier: Any = None,
    library: Any = None,
    mc: Any = None,
    thresholds: dict[str, Any] | None = None,
    reject_keywords: list[str] | None = None,
    source_name: str = "fitgirl",
    platform: str | None = None,
    sort_order: Literal["new", "criticscore", "userscore"] | None = None,
) -> list[dict[str, Any]]:
    """Match pending games against a torrent source index.

    For each non-expired pending game:
      1. Normalize its title
      2. Search ``source_titles`` for a match filtered by *source_name*
      3. On match: skip if already in library, otherwise verify the
         game's scores via *mc* before delivering (prevents games with
         wrong browse-only metrics from being downloaded)
      4. If no match: update ``last_checked_at``
      5. (Expiry is NOT handled here — see ``_process_expired_games``)

    When *qbt* and *magnet_fetcher* are provided, matched games are
    delivered to qBittorrent.  Without them, only a history record
    is written (no download).

    Args:
        mc: MetacriticClient instance for just-in-time score verification.
        thresholds: Score thresholds for verification. Required when *mc*
            is provided.
        source_name: Name of the source to match against ("fitgirl").
        platform: Optional platform filter. When provided, only pending games
            matching this platform are processed.
        sort_order: Active browse sort order used to order the pending queue
            (see :meth:`Database.get_pending`).  ``None`` keeps the default
            database order.

    Returns a list of result dicts.
    """

    results: list[dict[str, Any]] = []

    can_deliver = qbt is not None and magnet_fetcher is not None

    # Match non-expired pending games whose scores have been checked
    # against the real Metacritic detail page.
    pending = db.get_pending(platform=platform, sort_order=sort_order)
    for game in pending:
        # ── Score-check gate ──
        # Games whose scores haven't been verified against the real
        # Metacritic detail page (score_checks_passed=False/None) stay
        # in the queue until the next cycle's score-check phase.
        if not game.score_checks_passed:
            continue

        game_title: str = str(game.game_title)
        game_slug: str = str(game.slug)
        game_platform: str = str(game.platform)
        game_metascore: float | None = game.metascore
        game_metascore_reviews: int | None = game.metascore_reviews
        game_user_score: float | None = game.user_score
        game_user_reviews: int | None = game.user_reviews
        game_release_date: str | None = game.release_date

        result = _process_single_pending_match(
            db,
            mc,
            thresholds,
            qbt,
            magnet_fetcher,
            notifier,
            library,
            can_deliver,
            game_title=game_title,
            game_slug=game_slug,
            game_platform=game_platform,
            game_metascore=game_metascore,
            game_metascore_reviews=game_metascore_reviews,
            game_user_score=game_user_score,
            game_user_reviews=game_user_reviews,
            game_release_date=game_release_date,
            reject_keywords=reject_keywords,
            source_name=source_name,
        )
        if result is not None:
            results.append(result)

    return results


def _deliver_match(
    db: Database,
    *,
    qbt: Any,
    magnet_fetcher: Callable[[str], str | None],
    notifier: Any,
    best: dict[str, Any],
    game_slug: str,
    game_title: str,
    game_platform: str,
    game_metascore: float | None,
    game_user_score: float | None,
    game_metascore_reviews: int | None = None,
    game_user_reviews: int | None = None,
    game_genres: list[str] | None = None,
    game_must_play: bool | None = None,
    game_release_date: str | None = None,
    source_name: str = "fitgirl",
) -> dict[str, Any]:
    """Deliver a matched pending game to qBittorrent and emit notifications.

    Uses the pre-stored magnet from the source index if available,
    otherwise fetches the magnet from the source page.  Adds the
    torrent to qBittorrent, and sends a download notification on success.
    A magnet qBittorrent already holds is never re-uploaded and produces exactly
    one info line: a torrent gamarr owns (tagged earlier, or sitting in gamarr's
    category) is adopted and recorded as passed so post-processing still copies
    it, while a torrent outside gamarr's category is recorded as skipped, since
    there is nothing for gamarr to copy.  On transient delivery failure the game
    stays in the pending queue for retry on the next cycle — no permanent
    failure notification is sent, since the game may succeed later.

    Returns:
        A result dict with ``result`` set to ``"Passed"`` on successful delivery
        or adoption, ``"Skipped"`` when the torrent is present but not gamarr's,
        or ``"Error"`` on magnet-fetch / qBittorrent failure.
    """
    source_url: str = str(best["url"])
    # Use pre-stored magnet if available, otherwise fetch from the source page.
    magnet = best.get("magnet") or magnet_fetcher(source_url)
    # Always consume the cached page title (avoids unbounded growth on failed fetches).
    source_title = _fitgirl_page_title_cache.pop(source_url, None) or best["title"] or game_title
    if not magnet:
        logger.info("No magnet found for matched '{}' at {}", game_title, source_url)
        record_result = _record_delivery_error(
            db,
            game_slug=game_slug,
            game_title=game_title,
            game_platform=game_platform,
            game_metascore=game_metascore,
            game_user_score=game_user_score,
            best=best,
        )
        return record_result

    if qbt.is_torrent_present(magnet) is not False:
        # Present, or the check could not be evaluated.  Never upload blindly:
        # a duplicate add can be reported as successful while nothing was added.
        return _deliver_present_torrent(
            db,
            qbt,
            best=best,
            magnet=magnet,
            source_name=source_name,
            source_title=source_title,
            game_slug=game_slug,
            game_title=game_title,
            game_platform=game_platform,
            game_metascore=game_metascore,
            game_user_score=game_user_score,
            game_genres=game_genres,
        )

    display_name = _source_display(source_name)
    tag = qbt.add_torrent(magnet_url=magnet, title=f"[{display_name}] {source_title}")
    if not tag:
        record_result = _record_delivery_error(
            db,
            game_slug=game_slug,
            game_title=game_title,
            game_platform=game_platform,
            game_metascore=game_metascore,
            game_user_score=game_user_score,
            best=best,
        )
        return record_result

    # Log game details before the delivery confirmation.
    _log_game_details(
        types.SimpleNamespace(
            title=game_title,
            metascore=game_metascore,
            metascore_review_count=game_metascore_reviews,
            user_score=game_user_score,
            user_review_count=game_user_reviews,
            genres=game_genres,
            must_play=game_must_play,
            release_date=game_release_date,
        )
    )

    logger.info("\u2713 Delivered matched '{}' to qBittorrent (tag: {})", _escape_markup(game_title), tag)
    record_result = _record_result(
        db,
        source=source_name,
        source_title=game_title,
        source_url=f"mc:{game_slug}",
        game_title=game_title,
        platform=game_platform,
        metascore=game_metascore,
        user_score=game_user_score,
        result="Passed",
        result_details=f"Downloaded from {best['url']}",
        magnet_url=magnet,
        torrent_tag=str(tag),
        genres=", ".join(game_genres) if game_genres else None,
    )
    record_result["slug"] = game_slug
    db.remove_pending(game_slug)
    # See comment above re: notification ordering.
    _safe_notify(
        notifier,
        "send_download_notification",
        title=game_title,
        platform=game_platform,
        metascore=game_metascore,
        metascore_reviews=game_metascore_reviews,
        user_score=game_user_score,
        user_reviews=game_user_reviews,
        slug=game_slug,
        genres=game_genres,
        must_play=game_must_play,
        release_date=game_release_date,
        add_paused=qbt.add_paused,
        source_name=source_name,
        source_url=best["url"],
    )
    return record_result


def _deliver_present_torrent(
    db: Database,
    qbt: Any,
    *,
    best: dict[str, Any],
    magnet: str,
    source_name: str,
    source_title: str,
    game_slug: str,
    game_title: str,
    game_platform: str,
    game_metascore: float | None,
    game_user_score: float | None,
    game_genres: list[str] | None = None,
) -> dict[str, Any]:
    """Record a magnet qBittorrent already holds, without re-uploading it.

    A duplicate must not be re-added (qBittorrent rejects it) and must not be
    retried every cycle.  A torrent gamarr owns - one it tagged earlier, or one
    sitting in gamarr's category, which is the opt-in - is adopted with one INFO
    line and recorded as delivered so post-processing still copies it.  A torrent
    outside gamarr's category is recorded as skipped: there is nothing for
    gamarr to copy, so the game is never retried.

    Returns:
        The result dict for the caller.  When the torrent cannot be confirmed or
        adopted, the game stays pending so the next cycle retries it, exactly as
        a delivery failure does.
    """
    adopted_tag = qbt.adopt_present_torrent(magnet, title=f"[{_source_display(source_name)}] {source_title}")
    if adopted_tag is None:
        # _record_delivery_error owns the single warning for this failure; this line
        # records why the duplicate path could not settle.
        logger.info(
            "Could not confirm or adopt the already-present torrent for '{}' (unusable magnet, or qBittorrent "
            "unreachable); keeping it pending for retry",
            game_title,
        )
        return _record_delivery_error(
            db,
            game_slug=game_slug,
            game_title=game_title,
            game_platform=game_platform,
            game_metascore=game_metascore,
            game_user_score=game_user_score,
            best=best,
        )
    if adopted_tag is False:
        logger.info(
            "Torrent '{}' already exists in qBittorrent outside gamarr's category; leaving it untouched.",
            _escape_markup(game_title),
        )
        result, state = "Skipped", "skipped"
        details = "Already present in qBittorrent; nothing for gamarr to copy"
    else:
        logger.info(
            "Torrent '{}' already exists in qBittorrent; adopted it for post-processing instead of re-uploading.",
            _escape_markup(game_title),
        )
        result, state = "Passed", None
        details = "Already present in qBittorrent; adopted for post-processing"

    record_result = _record_result(
        db,
        source=source_name,
        source_title=game_title,
        source_url=f"mc:{game_slug}",
        game_title=game_title,
        platform=game_platform,
        metascore=game_metascore,
        user_score=game_user_score,
        result=result,
        result_details=details,
        magnet_url=magnet,
        torrent_tag=adopted_tag if isinstance(adopted_tag, str) else None,
        genres=", ".join(game_genres) if game_genres else None,
        post_process_state=state,
    )
    record_result["slug"] = game_slug
    db.remove_pending(game_slug)
    return record_result


def _skip_for_reject_keywords(
    source_name: str,
    db: Database,
    best: dict[str, Any],
    game_title: str,
    game_slug: str,
    reject_keywords: list[str] | None,
) -> bool:
    """Return True if the source match should be skipped due to rejected keywords.

    Only applies to FitGirl — FreeGOG pages don't have the same article body
    structure with HV/hypervisor keywords and use standard HTTPS (verify=True).
    """
    return source_name.casefold() == "fitgirl" and _check_reject_keywords(
        db, best, game_title, game_slug, reject_keywords
    )


def _reject_scan_text(article_text: str | None, page_title: str | None, game_title: str) -> str:
    """Return the text to scan for reject keywords.

    The "Repack Features" block when the page has one, else the first
    ``_FEATURES_MAX_CHARS`` characters of the article.  With no block there is
    nothing to scope to, and degrading to the title would let an HV mention in
    the description through; the cap only bounds a pathological page.  When a block does exist it is
    used on its own: the boilerplate above it names "non-HV" releases, and
    scanning that produced the false positives this scoping removed.

    Args:
        article_text: The stripped ``<article>`` text, or ``None``.
        page_title: The parsed ``<title>``, or ``None``.
        game_title: The game being matched, for the log line.

    Returns:
        The text to scan; may be empty.
    """
    features = _extract_repack_features(article_text)
    if features is not None:
        return features
    _warn_missing_features_block(page_title, game_title)
    return (article_text or "")[:_FEATURES_MAX_CHARS]


def _warn_missing_features_block(page_title: str | None, game_title: str) -> None:
    """Report that a FitGirl page yielded no Repack Features text.

    Args:
        page_title: The parsed ``<title>``, or ``None`` when it did not parse.
        game_title: The game being matched, for the log line.
    """
    if page_title:
        logger.warning(
            "FitGirl page for '{}' has no Repack Features block — scanning the article preamble and "
            "the page title instead, which may miss an HV repack",
            game_title,
        )
        return
    logger.warning(
        "FitGirl page for '{}' has neither a Repack Features block nor a page title — "
        "only the article preamble could be scanned for reject keywords",
        game_title,
    )


def _check_reject_keywords(
    db: Database,
    best: dict[str, Any],
    game_title: str,
    game_slug: str,
    reject_keywords: list[str] | None,
) -> bool:
    """Check whether a match should be skipped due to rejected keywords.

    Returns True if the match should be skipped, keeping the game
    pending for the next cycle.  Fetches the FitGirl HTML page and
    checks the "Repack Features" block first (most descriptive), then
    the HTML ``<title>`` tag, against *reject_keywords*.  When the page
    cannot be fetched, the match is kept pending for re-check in the
    next cycle (the sitemap title is too weak a signal).

    The check reads only the page ``<title>`` and the "Repack Features" block.
    Scanning the whole article produced false positives: the download-mirror
    boilerplate mentions "non-HV repack" even on pages whose repack is not HV,
    and the backwards-compatibility prose refers to older releases.
    """
    if not reject_keywords:
        return False

    # Single HTTP request for both title and article body
    page_title, article_text = _fetch_fitgirl_page_content(best["url"])

    if page_title is None and article_text is None:
        # Cannot verify reject_keywords against the page content.
        # Erring on the side of caution — keep game pending so it can
        # be re-checked in the next cycle when the page may be reachable.
        logger.warning(
            "Could not fetch page content for '{}' — cannot check '{}' for reject keywords, "
            "so this candidate is skipped for now",
            best["url"],
            game_title,
        )
        db.touch_pending(game_slug)
        return True

    if matched := _keyword_match(_reject_scan_text(article_text, page_title, game_title), reject_keywords):
        logger.info(
            "Skipping match for '{}' — FitGirl Repack Features contains rejected keyword '{}'",
            game_title,
            matched,
        )
        db.touch_pending(game_slug)
        return True

    if page_title and (matched := _keyword_match(page_title, reject_keywords)):
        logger.info(
            "Skipping match for '{}' — FitGirl page title '{}' contains rejected keyword '{}'",
            game_title,
            page_title,
            matched,
        )
        db.touch_pending(game_slug)
        return True

    return False


# Common English stop words excluded from token overlap matching.
# These words appear in many game titles but carry no identifying
# value — including them causes false positives between unrelated
# games that happen to share generic words like "the" or "of".
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "of",
        "and",
        "a",
        "an",
        "in",
        "for",
        "to",
        "it",
        "is",
        "on",
        "at",
        "or",
        "by",
        "be",
        "as",
        "no",
        "so",
        "we",
        "he",
        "she",
        "they",
        "this",
        "that",
        "with",
        "from",
        "but",
        "not",
        "if",
        "all",
        "its",
        "are",
        "was",
    }
)


def _tokenize_title(title: str) -> set[str]:
    """Tokenize a game title into lowercased word tokens, converting Roman
    numerals and excluding stop words.

    Applies Roman numeral -> Arabic conversion (same as normalise_for_compare),
    then splits on non-alphanumeric boundaries.  Stop words ("the", "of",
    etc.) are excluded so that only meaningful game-name tokens contribute
    to the overlap count.
    """
    text = title.lower()
    for pattern, replacement in _ROMAN_TO_ARABIC:
        text = pattern.sub(replacement, text)
    tokens = {token.strip() for token in re.split(r"[^a-z0-9]+", text) if token.strip()}
    return tokens - _STOP_WORDS


# Minimum shared word tokens before a title pair is worth an article fetch.
_DEEP_SEARCH_MIN_SHARED_TOKENS = 3


def _shared_token_count(tokens_a: set[str], tokens_b: set[str]) -> int:
    """Return how many word tokens the two tokenised titles share.

    Callers pass sets from :func:`_tokenize_title`, so a title is tokenised once
    (the Roman-numeral substitutions are not free) and the same count drives both
    the candidate gate (:data:`_DEEP_SEARCH_MIN_SHARED_TOKENS`) and the relevance
    ranking.
    """
    return len(tokens_a & tokens_b)


def _substring_candidate_confirms_dlc(
    page_title: str | None,
    article_text: str | None,
    normalized: str,
) -> bool:
    """Return True when a substring candidate's page confirms the pending DLC.

    The sitemap title is part of the pending title here, so the two name the
    same game; DLC/expansion keywords on the page or in its body then confirm
    that the DLC is included.
    """
    if _page_title_has_dlc_keywords(page_title):
        return True
    if not article_text:
        return False
    if normalized in normalise_for_compare(article_text):
        return True
    return _article_contains_all_dlcs(article_text)


def _article_names_game(article_text: str | None, normalized: str, pending_title: str) -> bool:
    """Return True when the article body actually names the pending game.

    Used for token-overlap candidates, where a shared generic word ("2",
    "master", "collection") and repack boilerplate ("All DLCs") are not
    evidence that the repack is the pending game.
    """
    if not article_text:
        return False
    if normalized in normalise_for_compare(article_text):
        return True
    if not pending_title:
        return False
    pending_tokens = _tokenize_title(pending_title)
    return bool(pending_tokens) and pending_tokens <= _tokenize_title(article_text)


def _substring_candidate(entry_norm: str, normalized: str) -> bool:
    """Return True when *entry_norm* names the pending title without a numeral run-in.

    The entry may appear anywhere in *normalized* (a sitemap title with extra
    franchise words still qualifies); the only rejected containment is a match
    whose numeral runs straight into another digit, e.g. "battlefield2" inside
    "battlefield2042", which names a different game.
    """
    index = normalized.find(entry_norm)
    if index < 0:
        return False
    return not numeral_runs_into_a_digit(normalized, entry_norm, index)


def _check_candidate_for_dlc_match(
    candidate: dict[str, str | None],
    normalized: str,
    *,
    substring_match: bool = True,
    pending_title: str = "",
) -> bool:
    """Check whether *candidate* matches *normalized* via DLC-aware analysis.

    Returns True if the candidate's repack page covers *normalized*.

    When *substring_match* is True (candidate found via substring containment),
    the page-title DLC-keyword check is a reliable fast path: the sitemap title
    is part of the pending title, so the pending title most likely extends the
    same game and the extra wording is a DLC/edition name.

    When *substring_match* is False (candidate found via token overlap), the two
    titles only share words, which may be entirely generic, and "All DLCs" is
    boilerplate on most repack pages.  The article body must therefore actually
    name the pending game.

    Args:
        candidate: Sitemap entry with ``title``/``url``/``magnet`` keys.
        normalized: The normalised pending game title.
        substring_match: True when the candidate title is contained in the
            pending title (see :func:`_deep_search_article_body`).
        pending_title: The original pending game title, used for the
            token-coverage check on token-overlap candidates.

    Returns:
        True when the candidate's page confirms it covers the pending game.
    """
    page_title, article_text = _fetch_fitgirl_page_content(str(candidate["url"]))
    if substring_match:
        return _substring_candidate_confirms_dlc(page_title, article_text, normalized)
    return _article_names_game(article_text, normalized, pending_title)


def _deep_search_article_body(
    db: Database,
    source_name: str,
    normalized: str,
    pending_title: str = "",
) -> list[dict[str, str | None]]:
    """Search repack article bodies for the game title when direct matching fails.

    Handles DLC/expansion cases where the Metacritic game title is longer
    than the FitGirl sitemap title (e.g. ``"Dark Souls III: The Ringed City"``
    vs ``"Dark Souls Iii"`` from URL slug).  The DLC/expansion name is
    referenced in the article body's repack features section.

    Candidates are selected when the source title is a normalised substring of
    the pending title, or when the two share at least
    :data:`_DEEP_SEARCH_MIN_SHARED_TOKENS` word tokens (after Roman numeral
    conversion).  This prevents needless HTTP requests for games with no
    connection to any FitGirl repack.  At most 3 article pages are fetched to
    limit HTTP overhead.

    Args:
        db: Database instance for source title lookup.
        source_name: Source identifier (e.g. ``"fitgirl"``).
        normalized: The normalised Metacritic game title.
        pending_title: The original (pre-normalisation) Metacritic game
            title, used for token overlap matching.

    Returns:
        A list with one match dict if found in article body, or an empty list.
    """
    candidates: list[tuple[dict[str, str | None], bool, int]] = []
    pending_tokens = _tokenize_title(pending_title)
    for entry in db.get_all_source_titles(source_name):
        entry_title = str(entry.get("title", ""))
        entry_norm = normalise_for_compare(entry_title)
        if not entry_norm or normalized == entry_norm:
            continue
        # Tokenise once: this runs for every row of the source index, and the
        # Roman-numeral substitutions in _tokenize_title are not free.
        overlap = _shared_token_count(_tokenize_title(entry_title), pending_tokens)
        if _substring_candidate(entry_norm, normalized):
            candidates.append((entry, True, overlap))  # substring match
        elif overlap >= _DEEP_SEARCH_MIN_SHARED_TOKENS:
            candidates.append((entry, False, overlap))  # token-overlap match

    # Sort candidates: substring matches first (more reliable), then by the
    # strength of the title overlap, then by URL for determinism.  URL order
    # alone used to decide which candidate earned the scarce article fetches,
    # which let an unrelated repack win over the right one.
    candidates.sort(key=lambda x: (not x[1], -x[2], x[0].get("url", "")))

    # At most 3 HTTP requests to limit overhead
    for candidate, substring_match, _overlap in candidates[:3]:
        if _check_candidate_for_dlc_match(
            candidate,
            normalized,
            substring_match=substring_match,
            pending_title=pending_title,
        ):
            return [candidate]

    return []


def _find_first_non_rejected_match(
    db: Database,
    source_name: str,
    matches: list[dict[str, str | None]],
    game_title: str,
    game_slug: str,
    reject_keywords: list[str] | None,
) -> dict[str, str | None] | None:
    """Return the first match that passes reject_keywords, or None if all rejected.

    Iterates through *matches* in order, skipping any whose article body
    or page title contains a rejected keyword.  The first clean match is
    returned; ``None`` means every candidate was rejected and the game
    should stay pending.
    """
    for candidate in matches:
        if _skip_for_reject_keywords(source_name, db, candidate, game_title, game_slug, reject_keywords):
            continue
        return candidate
    return None


def _handle_matched_game(
    db: Database,
    mc: Any,
    thresholds: dict[str, Any] | None,
    qbt: Any,
    magnet_fetcher: Callable[[str], str | None] | None,
    notifier: Any,
    library: Any,
    can_deliver: bool,
    *,
    best: dict[str, Any],
    game_title: str,
    game_slug: str,
    game_platform: str,
    game_metascore: float | None,
    game_metascore_reviews: int | None,
    game_user_score: float | None,
    game_user_reviews: int | None,
    game_release_date: str | None,
    source_name: str = "fitgirl",
) -> dict[str, Any] | None:
    """Process a matched game: library check, delivery, or record-only.

    Returns a result dict or None if the match produces no delivery result.
    """
    # Check library first — skip if already owned
    if library is not None:
        lib_match = library.check_game(game_title)
        if lib_match is not None:
            return _record_library_match(
                db,
                game_title=game_title,
                game_slug=game_slug,
                game_platform=game_platform,
                game_metascore=game_metascore,
                game_user_score=game_user_score,
                lib_match=lib_match,
                source_name=source_name,
            )

    # If qbt and magnet_fetcher are provided, deliver the torrent
    if can_deliver:
        if qbt is None or magnet_fetcher is None:
            raise RuntimeError("can_deliver requires both qbt and magnet_fetcher")
        result_dict = _deliver_with_jit_verify(
            db,
            mc,
            thresholds,
            game_title,
            game_slug,
            game_platform,
            game_metascore,
            game_user_score,
            game_metascore_reviews,
            game_user_reviews,
            game_release_date,
            qbt=qbt,
            magnet_fetcher=magnet_fetcher,
            notifier=notifier,
            best=best,
            source_name=source_name,
        )
        if result_dict is not None:
            return result_dict
        return None

    # No qbt/magnet_fetcher — record match without delivery
    return _record_match_only(
        db,
        game_title=game_title,
        game_slug=game_slug,
        game_platform=game_platform,
        game_metascore=game_metascore,
        game_user_score=game_user_score,
        best=best,
        source_name=source_name,
    )


def _process_single_pending_match(
    db: Database,
    mc: Any,
    thresholds: dict[str, Any] | None,
    qbt: Any,
    magnet_fetcher: Callable[[str], str | None] | None,
    notifier: Any,
    library: Any,
    can_deliver: bool,
    *,
    game_title: str,
    game_slug: str,
    game_platform: str,
    game_metascore: float | None,
    game_metascore_reviews: int | None,
    game_user_score: float | None,
    game_user_reviews: int | None,
    game_release_date: str | None,
    reject_keywords: list[str] | None = None,
    source_name: str = "fitgirl",
) -> dict[str, Any] | None:
    """Match one pending game against a source sitemap and either deliver or touch."""
    normalized = normalise_for_compare(game_title)
    matches = db.match_source_title(source_name, normalized)
    if not matches and source_name.casefold() == "fitgirl":
        # Deep search: the game may be a DLC/expansion included in a
        # base-game repack, referenced only in the article repack features.
        matches = _deep_search_article_body(db, source_name, normalized, pending_title=game_title)
        if matches:
            logger.debug(
                "Deep search matched '{}'",
                game_title,
            )

    if not matches:
        db.touch_pending(game_slug)
        logger.info(
            "Title: '{}' passed Metacritic checks but has no {} match \u2014 staying in queue",
            game_title,
            _source_display(source_name),
        )
        return None

    # Iterate through matches — if one is rejected by keywords, try the next.
    best = _find_first_non_rejected_match(db, source_name, matches, game_title, game_slug, reject_keywords)

    if best is None:
        return None

    logger.info(
        "{} match: '{}' \u2192 '{}' ({})",
        _source_display(source_name),
        game_title,
        best["title"],
        best["url"],
    )
    logger.debug(
        "Matched pending '{}' to {} source '{}' ({})",
        game_title,
        _source_display(source_name),
        best["title"],
        best["url"],
    )

    return _handle_matched_game(
        db,
        mc,
        thresholds,
        qbt,
        magnet_fetcher,
        notifier,
        library,
        can_deliver,
        best=best,
        game_title=game_title,
        game_slug=game_slug,
        game_platform=game_platform,
        game_metascore=game_metascore,
        game_metascore_reviews=game_metascore_reviews,
        game_user_score=game_user_score,
        game_user_reviews=game_user_reviews,
        game_release_date=game_release_date,
        source_name=source_name,
    )


def _process_expired_games(
    db: Database,
    *,
    cancel_event: CancelSignal | None = None,
) -> list[dict[str, Any]]:
    """Move expired pending games to history and return their result dicts.

    Checks *cancel_event* before each row: a shutdown during a large lapsed
    backlog must not run an uninterruptible burst of history inserts.

    Args:
        db: Database instance.
        cancel_event: Optional cancellation signal.

    Returns:
        One result dict per swept row.
    """
    results: list[dict[str, Any]] = []
    expired = db.get_expired_pending()
    for game in expired:
        if is_cancelled(cancel_event):
            logger.info(
                "Cancelled during the expiry sweep — {} row(s) left for the next cycle", len(expired) - len(results)
            )
            break
        game_title = str(game.game_title)
        game_slug = str(game.slug)
        record_result = _record_result(
            db,
            source="metacritic",
            source_title=game_title,
            source_url=f"mc:{game_slug}",
            game_title=game_title,
            platform=str(game.platform),
            metascore=game.metascore,
            user_score=game.user_score,
            result=_EXPIRED_RESULT,
            result_details="Not available on any source within pending window",
        )
        record_result["slug"] = game_slug
        db.remove_pending(game_slug)
        results.append(record_result)
        logger.info("'{}' expired \u2014 queued too long, no match found on any source", game_title)
    return results


def _record_library_match(
    db: Database,
    *,
    game_title: str,
    game_slug: str,
    game_platform: str,
    game_metascore: float | None,
    game_user_score: float | None,
    lib_match: Any,
    source_name: str = "fitgirl",
) -> dict[str, Any]:
    """Record that a matched game was already found in the local library."""
    record_result = _record_result(
        db,
        source=source_name,
        source_title=game_title,
        source_url=f"mc:{game_slug}",
        game_title=game_title,
        platform=game_platform,
        metascore=game_metascore,
        user_score=game_user_score,
        result="Already owned",
        result_details=f"Found in library: {lib_match.matched_path}",
    )
    record_result["slug"] = game_slug
    db.remove_pending(game_slug)
    logger.info(
        "Already owned: '{}' found in library at {}; skipping",
        game_title,
        lib_match.matched_path,
    )
    return record_result


def _record_match_only(
    db: Database,
    *,
    game_title: str,
    game_slug: str,
    game_platform: str,
    game_metascore: float | None,
    game_user_score: float | None,
    best: dict[str, Any],
    source_name: str = "fitgirl",
) -> dict[str, Any]:
    """Record a matched game to history without delivering to qBittorrent."""
    record_result = _record_result(
        db,
        source=source_name,
        source_title=game_title,
        source_url=f"mc:{game_slug}",
        game_title=game_title,
        platform=game_platform,
        metascore=game_metascore,
        user_score=game_user_score,
        result="Passed",
        result_details=f"Matched on {_source_display(source_name)}: {best['url']}",
    )
    record_result["slug"] = game_slug
    db.remove_pending(game_slug)
    logger.info("\u2713 '{}' matched \u2014 logged (no downloader configured)", game_title)
    return record_result


def _record_delivery_error(
    db: Database,
    *,
    game_slug: str,
    game_title: str,
    game_platform: str,
    game_metascore: float | None,
    game_user_score: float | None,
    best: dict[str, Any],
) -> dict[str, Any]:
    """Record a delivery error and keep the game pending for retry.

    Unlike permanent failures (genre/title rejection, expired scores),
    delivery failures are transient — the source site may be down or
    qBittorrent temporarily unreachable.  The game stays in the pending
    queue so it can be retried on the next cycle.  Only the last-checked
    timestamp is bumped to prevent the same game from dominating every
    cycle.
    """
    db.touch_pending(game_slug)
    logger.warning(
        "Delivery failed for '{}' at {} — keeping pending for retry",
        game_title,
        best.get("url", "unknown"),
    )
    return {
        "result": "Error",
        "title": game_title,
        "slug": game_slug,
        "platform": game_platform,
    }


def _check_scrape_health() -> str:
    """Check whether Metacritic scraping is broken or it's a network issue.

    Tries Metacritic first. If Metacritic is unreachable, tries a
    generic endpoint (google.com) to differentiate internet outage
    from a Metacritic-specific problem.

    Returns:
        - ``"metacritic_broken"``: Metacritic responded but returned no data
        - ``"metacritic_down"``: Metacritic unreachable, internet works
        - ``"internet_down"``: Both Metacritic and internet unreachable
    """
    # Step 1: Try Metacritic home page
    try:
        resp = requests.head("https://www.metacritic.com", timeout=5)
        if resp.status_code < 500:
            return "metacritic_broken"
        return "metacritic_down"
    except requests.RequestException:
        pass

    # Step 2: Try generic endpoint to check internet connectivity
    try:
        requests.head("https://google.com", timeout=5)
        return "metacritic_down"
    except requests.RequestException:
        return "internet_down"


def _diagnose_and_notify_scrape(notifier: Any, reason: str, context: str) -> None:
    """Diagnose a scrape failure and send notification if appropriate.

    Args:
        notifier: Notifier instance to send through.
        reason: Result from ``_check_scrape_health()``.
        context: Description of what failed (e.g. "Metacritic browse returned no games").
    """
    if reason == "metacritic_broken":
        notifier.send_scrape_notification(f"{context} — the site structure may have changed.")
    elif reason == "metacritic_down":
        notifier.send_scrape_notification(f"{context} — Metacritic is unreachable.")
    else:
        logger.debug(
            "Internet appears down — skipping scrape notification (reason={})",
            reason,
        )


def _safe_notify(
    notifier: Any,
    method_name: str,
    **kwargs: Any,
) -> None:
    """Call a notifier method if notifier is not None, swallowing exceptions.

    Notifications are dispatched AFTER the DB has been updated, so a
    notifier failure (e.g. Apprise network blip) cannot leave the
    pending row in place and trigger a duplicate download next cycle.
    """
    if notifier is None:
        return
    try:
        getattr(notifier, method_name)(**kwargs)
    except Exception as exc:  # Notifier failures must not abort the pipeline
        logger.warning("{} raised: {}", method_name, exc)


def _record_result(
    db: Database,
    *,
    source: str,
    source_title: str,
    source_url: str,
    game_title: str,
    platform: str,
    metascore: float | None = None,
    user_score: float | None = None,
    result: str,
    result_details: str = "",
    magnet_url: str | None = None,
    torrent_tag: str | None = None,
    genres: str | None = None,
    post_process_state: str | None = None,
) -> dict[str, Any]:
    """Persist a result row and return the result dict for the caller."""
    db.record_processed(
        source=source,
        source_title=source_title,
        source_url=source_url,
        game_title=game_title,
        platform=platform,
        metascore=metascore,
        user_score=user_score,
        result=result,
        result_details=result_details,
        magnet_url=magnet_url,
        torrent_tag=torrent_tag,
        genres=genres,
        post_process_state=post_process_state,
    )
    return {
        "result": result,
        "game_title": game_title,
        "metascore": metascore,
        "user_score": user_score,
        "result_details": result_details,
    }


_SITEMAP_TIMEOUT = 30.0

# Regex patterns for detecting DLC-inclusion keywords in the
# FitGirl repack page HTML <title> tag.
_PAGE_TITLE_DLC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\+\s*all\s+(?:dlcs?|expansions?)\b", re.IGNORECASE),
    re.compile(r"\+\s*\d+\s+(?:dlcs?|expansions?)\b", re.IGNORECASE),
]

# Regex patterns for detecting "All DLCs" keyword variants in the
# FitGirl repack article body text.
_ALL_DLCS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\ball\s+(?:dlcs?|expansions?)\b", re.IGNORECASE),
    re.compile(r"\ball\s+available\s+(?:dlcs?|expansions?)\b", re.IGNORECASE),
    re.compile(r"\ball\s+existing\s+(?:dlcs?|expansions?)\b", re.IGNORECASE),
]

# Cache: URL → HTML <title> tag, populated by _default_magnet_fetcher
# to avoid a second HTTP request for torrent rename.
# Each entry is consumed (popped) by _deliver_match on the very next line
# after magnet_fetcher returns — callers must honour this implicit cleanup
# contract to prevent unbounded growth.
_fitgirl_page_title_cache: dict[str, str | None] = {}


def _page_title_has_dlc_keywords(page_title: str | None) -> bool:
    """Check if the HTML <title> tag contains DLC/expansion-inclusion patterns.

    Scans the page title (already extracted by
    ``_fetch_fitgirl_page_content``) for patterns like ``"+ All DLCs"``,
    ``"+ 3 Expansions"``, or ``"+ 15 DLCs"``.  Case-insensitive.

    Args:
        page_title: The unescaped HTML ``<title>`` tag content, or None.

    Returns:
        True if a DLC-inclusion pattern was found.
    """
    if not page_title:
        return False
    return any(pattern.search(page_title) for pattern in _PAGE_TITLE_DLC_PATTERNS)


def _article_contains_all_dlcs(article_text: str | None) -> bool:
    """Check if the article body text contains "All DLCs" / "All Expansions" patterns.

    Scans the article text (extracted from the ``<article>`` HTML element
    by ``_fetch_fitgirl_page_content``) for patterns like
    ``"all DLCs"``, ``"all existing DLCs"``, and ``"all expansions"``.
    Case-insensitive.

    Args:
        article_text: The article body text content, or None.

    Returns:
        True if an "All DLCs" keyword pattern was found.
    """
    if not article_text:
        return False
    return any(pattern.search(article_text) for pattern in _ALL_DLCS_PATTERNS)


_FITGIRL_HOST = "fitgirl-repacks.site"


def _is_fitgirl_url(url: str) -> bool:
    """Return True when *url* points at fitgirl-repacks.site.

    Compares the parsed host exactly.  A ``str.startswith`` test would also match
    ``https://fitgirl-repacks.site.example.com/...``, which is then fetched with
    ``verify=False``.

    Args:
        url: The URL to test.

    Returns:
        True when the host (ignoring a ``www.`` prefix) is FitGirl's.
    """
    try:
        netloc = urlparse(url).netloc.casefold()
    except ValueError:
        return False
    return netloc.removeprefix("www.") == _FITGIRL_HOST


def _fetch_fitgirl_page_content(url: str) -> tuple[str | None, str | None]:
    """Fetch a FitGirl repack page and extract its <title> and <article> text.

    Returns ``(title, article_text)`` where both are ``None`` if the page
    could not be fetched or the tag is missing.  The title has HTML entities
    decoded (e.g. ``&#039;`` → ``'``, ``&quot;`` → ``"``).  The article
    text has HTML tags stripped (including link anchor text from ``<a>``
    elements to exclude download filenames from keyword scanning) and
    whitespace collapsed.

    Returns:
        A ``(title, article_text)`` tuple.
    """
    try:
        # Only fetch from FitGirl (uses self-signed cert — verify=False is intentional).
        # Compare the host exactly: a prefix test would also accept
        # "fitgirl-repacks.site.example.com", which would then be fetched with
        # TLS verification disabled.
        if not _is_fitgirl_url(url):
            return None, None
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_SITEMAP_TIMEOUT,
            verify=False,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None, None

    html = resp.text
    # Extract <title> tag content
    title: str | None = None
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        title = unescape(match.group(1).strip())

    # Extract <article> text content (excludes sidebar, nav, comments)
    article_text: str | None = None
    article_match = re.search(r"<article[^>]*>(.*?)</article>", html, re.IGNORECASE | re.DOTALL)
    if article_match:
        article_html = article_match.group(1)
        # Strip <a> tags AND their content to avoid false positives from
        # download link filenames (e.g. "Persona.5.Royal.v1.04.Hypervisor.Bypass-"
        # in anchor text).  These filenames contain structural metadata about
        # the cracking method and do not describe the game's requirements.
        article_html = re.sub(r"<a[^>]*>.*?</a>", "", article_html, flags=re.IGNORECASE | re.DOTALL)
        article_text = re.sub(r"<[^>]+>", " ", article_html)
        # Decode entities so "doesn&#8217;t include an HV version" reaches the
        # negation check as text rather than as an entity sequence.
        article_text = unescape(article_text)
        article_text = re.sub(r"\s+", " ", article_text).strip()

    return title, article_text


def _fetch_fitgirl_page_title(url: str) -> str | None:
    """Fetch a FitGirl repack page and extract its HTML <title> tag.

    Returns the full page title (e.g. "Crimson Desert [FitGirl HV Repack]")
    or None if the page could not be fetched.
    """
    title, _ = _fetch_fitgirl_page_content(url)
    return title


def _default_magnet_fetcher(url: str) -> str | None:
    """Fetch a source page and extract its magnet link.

    Handles both FitGirl (self-signed cert, verify=False) and
    FreeGOG (standard HTTPS) pages, dispatching to the correct
    magnet extraction function for each source.

    Caches the unescaped HTML ``<title>`` tag (with HTML entities
    decoded) in ``_fitgirl_page_title_cache`` so callers can retrieve
    it without a second HTTP request.
    """
    try:
        if _is_fitgirl_url(url):
            verify = False
        elif url.startswith("https://freegogpcgames.com"):
            verify = True
        else:
            logger.warning("Skipping unknown magnet source: {}", url)
            return None
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_SITEMAP_TIMEOUT,
            verify=verify,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Magnet fetch failed for {}: {}", url, exc)
        return None
    # Cache page title for torrent rename (avoids re-fetch)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
    _fitgirl_page_title_cache[url] = unescape(title_match.group(1).strip()) if title_match else None

    # Dispatch to the correct magnet extractor based on URL
    if url.startswith("https://freegogpcgames.com"):
        return _extract_magnet_from_freegog_page(resp.text)
    return _extract_magnet_from_html(resp.text)
