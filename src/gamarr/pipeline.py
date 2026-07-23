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

import requests
from loguru import logger

from gamarr.database import _INDEFINITE_DAYS, Database
from gamarr.metacritic import MetacriticClient
from gamarr.metacritic_cache import MetacriticCache
from gamarr.notifications import Notifier
from gamarr.qbittorrent import QBittorrentClient
from gamarr.sources.fitgirl import _USER_AGENT, FitGirlSource, _extract_magnet_from_html
from gamarr.sources.freegog import FreeGOGSource, _extract_magnet_from_freegog_page
from gamarr.utils import is_cancelled, normalise_for_compare

# urllib3 warnings for FitGirl self-signed cert are suppressed in gamarr.sources.fitgirl

if TYPE_CHECKING:
    import threading
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


@dataclass
class AcquisitionConfig:
    """Thresholds and settings for the acquisition run."""

    min_metascore: int
    min_metascore_reviews: int
    min_user_score: float
    min_user_reviews: int
    cache_details_days: int = 7
    cache_pages_hours: int = 6
    enabled: bool = True
    max_queue_days: int = 30
    max_pages: int | None = None
    max_cycle_pages: int | None = None
    reject_genre: list[str] | None = None
    reject_title: list[str] | None = None
    fitgirl_max_queue_days: int = 60
    notify_on_scrape_failure: bool = True
    sort_order: Literal["new", "metascore"] = "new"
    search_mode: Literal["backlog", "latest"] = "latest"
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
    min_metascore: int = 75,
    min_metascore_reviews: int = 10,
    min_user_score: float = 7.5,
    min_user_reviews: int = 10,
    cache_details_days: int = 7,
    cache_pages_hours: int = 6,
    enabled: bool = True,
    max_queue_days: int = 30,
    max_pages: int | None = None,
    max_cycle_pages: int | None = None,
    fitgirl_max_queue_days: int = 60,
    notify_on_scrape_failure: bool = True,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,
    apprise_urls: list[str] | None = None,
    notify_on_download: bool = True,
    notify_on_failure: bool = False,
    notify_on_error: bool = False,
    library_paths: list[str] | None = None,
    fitgirl_cache_pages_hours: int = 6,
    fitgirl_reject_keywords: list[str] | None = None,
    cancel_event: threading.Event | None = None,
    download_sites: list | None = None,
    sort_order: Literal["new", "metascore"] = "new",
    search_mode: Literal["backlog", "latest"] = "latest",
    age_recheck_weeks: int | None = None,
) -> list[dict[str, Any]]:
    """Execute one scan cycle.

    Discovers games by browsing Metacritic (newest-first), verifies
    each game's real Metacritic detail-page scores against the
    configured thresholds, then matches survivors against each
    configured source's sitemap and delivers to qBittorrent.
    """
    cfg = AcquisitionConfig(
        min_metascore=min_metascore,
        min_metascore_reviews=min_metascore_reviews,
        min_user_score=min_user_score,
        min_user_reviews=min_user_reviews,
        cache_details_days=cache_details_days,
        cache_pages_hours=cache_pages_hours,
        enabled=enabled,
        max_queue_days=max_queue_days,
        max_pages=max_pages,
        max_cycle_pages=max_cycle_pages,
        fitgirl_max_queue_days=fitgirl_max_queue_days,
        notify_on_scrape_failure=notify_on_scrape_failure,
        reject_genre=reject_genre,
        reject_title=reject_title,
        sort_order=sort_order,
        search_mode=search_mode,
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
        entry.cache_pages_hours = fitgirl_cache_pages_hours
        entry.reject_keywords = fitgirl_reject_keywords or []
        entry.max_queue_days = fitgirl_max_queue_days
        return entry

    def _log_backlog_progress(
        platform: str,
        db: Database,
        max_pages: int,
        max_cycle_pages: int,
        cutoff_year: int,
        current_year: int,
    ) -> None:
        """Log backlog scan progress and estimated cycles remaining."""
        if max_pages <= 0:
            return
        total_scanned = db.sum_scanned_pages(platform, cutoff_year, current_year)
        pct = min(100, round(total_scanned / max_pages * 100))
        remaining = max(0, max_pages - total_scanned)

        if remaining == 0 or total_scanned >= max_pages:
            return  # exhaustion already logged by backlog completion check
        elif max_cycle_pages and max_cycle_pages > 0:
            cycles = math.ceil(remaining / max_cycle_pages)
            logger.info(
                "Backlog progress: {} of {} pages ({}%, ~{} cycles remaining)",
                total_scanned,
                max_pages,
                pct,
                cycles,
            )
        else:
            logger.info(
                "Backlog progress: {} of {} pages ({}%, unlimited per cycle)",
                total_scanned,
                max_pages,
                pct,
            )

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
        # Metacritic-first discovery: browse Metacritic for games that pass
        # score thresholds and age filter. The FitGirl sitemap is fetched
        # only if Metacritic produced games to match against.
        browse_games: list[dict[str, Any]] = []
        new_pending: int = 0
        scan_year_anchor = datetime.datetime.now(tz=datetime.UTC).year

        if cfg.enabled:
            # cutoff_date is always None — page-count based limit is handled
            # by max_cycle_pages passed to scan_recent_games.
            cutoff_date: str | None = None

            if cfg.search_mode == "backlog":
                # ── Backlog mode: year-loop with progress tracking ──
                previous_sort_order = db.get_last_sort_order(platform)
                if previous_sort_order is not None and previous_sort_order != cfg.sort_order:
                    logger.info(
                        "Sort order changed from '{}' to '{}'",
                        previous_sort_order,
                        cfg.sort_order,
                    )
                    # Clear the browse page cache so stale data from the old
                    # sort order is not returned (cache key does not include sort_order).
                    db.clear_cache("metacritic")
                    # Also reset backlog progress so the new sort order starts
                    # from page 1 — the page structure differs for "new" vs "metascore".
                    db.reset_backlog_progress(platform, cfg.sort_order)

                mc.sort_order = cfg.sort_order

                if cfg.sort_order == "new":
                    years_back = max(0, math.ceil((cfg.max_pages if cfg.max_pages else 500) / 52))
                    cutoff_year = scan_year_anchor - years_back
                    current_year = scan_year_anchor
                else:
                    # sort_order == "metascore": no year dimension, use year=0 sentinel
                    cutoff_year = 0
                    current_year = 0

                total_backlog = db.sum_scanned_pages(platform, cutoff_year, current_year)
                max_pages_cfg = cfg.max_pages if cfg.max_pages else 0

                if max_pages_cfg > 0 and total_backlog >= max_pages_cfg:
                    logger.info(
                        "Backlog complete — {} of {} pages scanned. "
                        "Switch to search_mode: latest for ongoing monitoring.",
                        total_backlog,
                        max_pages_cfg,
                    )
                else:
                    for scan_year in range(cutoff_year, current_year + 1):
                        if is_cancelled(cancel_event):
                            break
                        start_page = db.get_last_scanned_page(platform, scan_year) + 1
                        try:
                            year_games = mc.scan_recent_games(
                                platform,
                                cache_pages_hours=cfg.cache_pages_hours,
                                cutoff_date=cutoff_date,
                                cancel_event=cancel_event,
                                start_page=start_page,
                                show_progress=True,
                                year=scan_year if cfg.sort_order == "new" else None,
                                max_pages=cfg.max_cycle_pages
                                if cfg.max_cycle_pages
                                else (cfg.max_pages if cfg.max_pages else 0),
                            )
                            browse_games.extend(year_games)
                            last_page = mc._recent_games_last_page if isinstance(mc._recent_games_last_page, int) else 0
                            db.set_last_scanned_page(platform, scan_year, last_page)
                        except Exception:
                            logger.exception("Scan failed for year {} — will retry next cycle", scan_year)

                    db.set_last_sort_order(platform, cfg.sort_order)

            else:
                # ── Latest mode: simple page-1..N scan, no progress tracking ──
                mc.sort_order = cfg.sort_order
                year = scan_year_anchor if cfg.sort_order == "new" else None
                try:
                    browse_games = mc.scan_recent_games(
                        platform,
                        cache_pages_hours=cfg.cache_pages_hours,
                        cutoff_date=None,
                        cancel_event=cancel_event,
                        start_page=1,
                        show_progress=True,
                        year=year,
                        max_pages=cfg.max_cycle_pages if cfg.max_cycle_pages else 0,
                    )
                except Exception:
                    logger.exception("Latest scan failed — will retry next cycle")

            # ── Shared: process browse results into pending queue ──
            if browse_games:
                thresholds = {
                    "min_metascore": cfg.min_metascore,
                    "min_metascore_reviews": cfg.min_metascore_reviews,
                    "min_user_score": cfg.min_user_score,
                    "min_user_reviews": cfg.min_user_reviews,
                }
                new_pending = _process_browse_games(
                    browse_games,
                    platform,
                    db,
                    thresholds,
                    max_queue_days=cfg.max_queue_days,
                    reject_title=cfg.reject_title,
                    search_mode=cfg.search_mode,
                )
                pending_queue_len = (
                    len(db.get_backlog_pending(platform=platform))
                    if cfg.search_mode == "backlog"
                    else len(db.get_latest_pending(platform=platform))
                )
                logger.info(
                    "Pending queue: {} total (including previous cycles)",
                    pending_queue_len,
                )
                if new_pending:
                    logger.info(
                        "{} of {} collected games passed title/age filters — added to pending queue",
                        new_pending,
                        len(browse_games),
                    )

            # ── Shared: scrape-health check ──
            if not is_cancelled(cancel_event) and not browse_games and cfg.notify_on_scrape_failure:
                # Check if we have any cached browse data (if so, stale data is fine)
                cached_exists = (
                    mc._cache.get_browse_page(platform, 1, ttl_hours=cfg.cache_pages_hours, year=0) is not None
                    or mc._cache.get_browse_page(
                        platform,
                        1,
                        ttl_hours=cfg.cache_pages_hours,
                        year=datetime.datetime.now(tz=datetime.UTC).year,
                    )
                    is not None
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
        pending_games = (
            db.get_backlog_pending(platform=platform)
            if cfg.search_mode == "backlog"
            else db.get_latest_pending(platform=platform)
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
                "min_metascore": cfg.min_metascore,
                "min_metascore_reviews": cfg.min_metascore_reviews,
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
                search_mode=cfg.search_mode,
            )
            if removed:
                logger.info(
                    "Removed {} games from queue — rejected by genre, title, or not found on Metacritic",
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
                "cache_pages_hours": entry.cache_pages_hours,
            }
            return factory(**kwargs)

        matched: list[dict[str, Any]] = []
        phase = 1  # Phase counter for logging
        if not is_cancelled(cancel_event) and (
            db.has_verified_backlog_pending(platform=platform)
            if cfg.search_mode == "backlog"
            else db.has_verified_latest_pending(platform=platform)
        ):
            match_thresholds = {
                "min_metascore": cfg.min_metascore,
                "min_metascore_reviews": cfg.min_metascore_reviews,
                "min_user_score": cfg.min_user_score,
                "min_user_reviews": cfg.min_user_reviews,
            }
            # If no download_sites provided, fall back to a default FitGirl entry
            # using the legacy parameters for backward compatibility.
            sites = download_sites if download_sites else [_make_fitgirl_entry()]
            # Phase 2-3: Index all sources (indexing must happen before matching
            # so _match_pending_games sees fresh sitemap data).
            sources_built: list[tuple[Any, Any]] = []
            for source_entry in sites:
                if not source_entry.enabled:
                    continue
                source = _build_source(source_entry, db)
                sources_built.append((source_entry, source))

                phase += 1
                display = _source_display(source_entry.name)
                logger.opt(colors=True).info("<cyan>━━━ Phase {}: Indexing {} ━━━</>", phase, display)

                source.fetch_sitemap(db, cancel_event=cancel_event)

            # Per-source matching phases
            for source_entry, _source in sources_built:
                phase += 1
                display = _source_display(source_entry.name)
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
                    search_mode=cfg.search_mode,
                    platform=platform,
                )
                if source_matched:
                    matched.extend(source_matched)
                    logger.info("{} queued games found on {}", len(source_matched), display)

        # Mandatory end-of-cycle phase
        phase += 1
        delivered = sum(1 for m in matched if m.get("result") == "Passed")
        logger.opt(colors=True).info(
            "<green>━━━ Phase {}: End of cycle ━━━</>",
            phase,
        )
        logger.info(
            "{} games browsed, {} queued, {} matched, {} delivered",
            len(browse_games),
            new_pending,
            len(matched),
            delivered,
        )

        # Process old verified games AFTER the matching phase.
        # Games that passed score verification must get a chance to
        # match against a sitemap before being aged out.  Previously
        # this ran before matching, silently removing every old game
        # from pending before it could be delivered.
        _process_aged_games(db, cfg, platform, cancel_event=cancel_event, search_mode=cfg.search_mode)

        # Log backlog progress if max_pages is configured (backlog mode only)
        if cfg.enabled and cfg.search_mode == "backlog":
            _log_backlog_progress(
                platform,
                db,
                cfg.max_pages if cfg.max_pages else 0,
                cfg.max_cycle_pages if cfg.max_cycle_pages else 0,
                cutoff_year,
                current_year,
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
        min_critic_reviews: Minimum critic reviews threshold (``min_metascore_reviews``).
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


def _title_contains_keywords(title: str, keywords: list[str] | None) -> str | None:
    """Return the first matching keyword (lowercased), or None if no match."""
    if not keywords:
        return None
    title_lower = title.lower()
    for kw in keywords:
        if kw.lower() in title_lower:
            return kw.lower()
    return None


def _title_matches_reject(title: str, reject_title: list[str] | None) -> bool:
    """Return True if *title* case-insensitively matches any reject_title entry."""
    if not reject_title:
        return False
    title_lower = title.lower()
    return any(term.lower() in title_lower for term in reject_title)


_SOURCE_DISPLAY: dict[str, str] = {"fitgirl": "FitGirl", "freegog": "FreeGOG"}


def _source_display(name: str) -> str:
    """Return the display-cased form of a source name."""
    return _SOURCE_DISPLAY.get(name, name.title())


def _game_passes_thresholds(game: dict[str, Any], thresholds: dict[str, Any]) -> bool:
    """Check if a browse-page game dict passes all score thresholds.

    Note: browse-page ``score`` and ``user_rating`` fields are internal
    metrics on a different scale (e.g. 1478), not real 0-100 metascores
    or 0-10 user scores.  Real score verification happens during the
    detail-page phase.  Review count filtering is now handled by
    ``_reject_by_browse_review_counts`` in ``_process_browse_games``.
    """
    metascore = game.get("score")
    user_score = game.get("user_rating")
    if metascore is None or user_score is None:
        return False
    return all(
        [
            metascore >= thresholds["min_metascore"],
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
    """Return True if *game* passes all filters and should be added to pending.

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


def _process_browse_games(
    browse_games: list[dict[str, Any]],
    platform: str,
    db: Database,
    thresholds: dict[str, Any],
    *,
    max_queue_days: int = 30,
    reject_title: list[str] | None = None,
    search_mode: str = "latest",
) -> int:
    """Evaluate browse-page games and insert qualifying ones into the pending queue.

    Browse listings from ``_parse_browse_page`` already include scores
    in their dict (``score`` = critic metascore, ``user_rating`` = user
    score).  Games that pass the thresholds are inserted into
    ``pending_games``.  Already-processed or already-pending games are
    skipped.

    Args:
        browse_games: List from ``_parse_browse_page``.
        platform: Target platform name.
        db: Database instance.
        thresholds: Dict with ``min_metascore`` keys.
        max_queue_days: How many days to keep the game pending before expiry.
        reject_title: Titles matching any of these are skipped.
        search_mode: "backlog" or "latest" — determines which pending queue to use.

    Returns:
        Number of new pending games added.
    """
    known_slugs = _get_known_slugs_by_mode(db, search_mode, source="metacritic", platform=platform)
    new_count = 0
    for game in browse_games:
        if not _is_game_eligible(game, db, thresholds, known_slugs=known_slugs):
            continue
        if _title_matches_reject(game.get("title", ""), reject_title):
            logger.debug("Skipping '{}' — matches reject_title", game.get("title", ""))
            continue

        reject_reason = _reject_by_browse_review_counts(
            game,
            min_critic_reviews=thresholds.get("min_metascore_reviews", 0),
            min_user_reviews=thresholds.get("min_user_reviews", 0),
        )
        if reject_reason is not None:
            logger.debug(
                "Skipping '{}' — {}",
                game.get("title", ""),
                reject_reason,
            )
            continue

        g_slug = game.get("slug", "")
        g_title = game.get("title", "")
        if max_queue_days <= 0:
            expires_at = (
                datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=_INDEFINITE_DAYS)
            ).isoformat()
        else:
            expires_at = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=max_queue_days)).isoformat()

        _record_pending_by_mode(
            db,
            search_mode,
            slug=g_slug,
            game_title=g_title,
            platform=platform,
            metascore=float(game.get("score") or 0),
            metascore_reviews=game.get("critic_review_count"),
            user_score=float(game.get("user_rating") or 0),
            user_reviews=game.get("user_review_count"),
            release_date=game.get("release_date"),
            expires_at=expires_at,
        )
        new_count += 1
        logger.debug(
            "Added pending game: '{}' (slug: {}, expires {})",
            g_title,
            g_slug,
            expires_at,
        )

    return new_count


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
    return (thresholds.get("min_metascore", 0) > 0 and result.metascore is None) or (
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
    ``min_user_reviews`` and ``min_metascore_reviews``.

    Returns True when the existing checks all pass.
    """
    if result is None:
        return False

    # Reject when a score value is None (TBD — not reviewed) but a
    # non-zero threshold is configured.  This prevents TBD games
    # (metascore=None, like WEBFISHING) from silently bypassing
    # min_metascore.
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
        threshold=thresholds.get("min_metascore_reviews", 0),
    ):
        return False

    checks: list[bool] = [
        c
        for c in [
            _check_score_threshold(result.metascore, thresholds["min_metascore"]),
            _check_score_threshold(result.metascore_review_count, thresholds["min_metascore_reviews"]),
            _check_score_threshold(result.user_score, thresholds["min_user_score"]),
            _check_score_threshold(result.user_review_count, thresholds["min_user_reviews"]),
        ]
        if c is not None
    ]
    if not checks:
        return False  # No review data — can't verify
    return all(checks)


def _fail_game_after_max_attempts(
    db: Database,
    game: Any,
    result: Any,
    attempts: int,
    result_details: str | None = None,
) -> None:
    """Record a game as permanently failed and remove from pending queue.

    When *result_details* is provided it overrides the auto-generated
    "below thresholds" message (e.g. for genre-rejected games).
    """
    game_slug = str(game.slug)
    if result_details is None:
        score_info = f"({result.metascore}, {result.user_score})" if _scores_present(result) else "(no scores)"
        result_details = f"Scores {score_info} below thresholds after {attempts} attempts"
    db.record_processed(
        source="metacritic",
        source_title=str(game.game_title),
        source_url=f"mc:{game_slug}",
        game_title=str(game.game_title),
        platform=str(game.platform),
        metascore=result.metascore,
        user_score=result.user_score,
        result="Failed",
        result_details=result_details,
    )
    if _scores_present(result):
        logger.debug(
            "Removed '{}' from queue \u2014 Metacritic scores ({}, {}) below thresholds after {} attempts",
            game.game_title,
            result.metascore,
            result.user_score,
            attempts,
        )
    else:
        logger.debug(
            "Removed '{}' from queue \u2014 no Metacritic review scores yet after {} attempts",
            game.game_title,
            attempts,
        )
    db.remove_backlog_pending(str(game.slug))


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


def _get_known_slugs_by_mode(db: Database, search_mode: str, *, source: str, platform: str) -> set[str]:
    """Get known slugs from the mode-specific pending table + history."""
    if search_mode == "backlog":
        return db.get_known_backlog_slugs(source=source, platform=platform)
    return db.get_known_latest_slugs(source=source, platform=platform)


def _remove_pending_by_mode(db: Database, slug: str, search_mode: str) -> None:
    """Remove a pending game from the mode-specific table."""
    if search_mode == "backlog":
        db.remove_backlog_pending(slug)
    else:
        db.remove_latest_pending(slug)


def _touch_pending_by_mode(db: Database, slug: str, search_mode: str) -> None:
    """Touch a pending game in the mode-specific table."""
    if search_mode == "backlog":
        db.touch_backlog_pending(slug)
    else:
        db.touch_latest_pending(slug)


def _record_pending_by_mode(
    db: Database,
    search_mode: str,
    slug: str,
    game_title: str,
    platform: str,
    metascore: float | None = None,
    metascore_reviews: int | None = None,
    user_score: float | None = None,
    user_reviews: int | None = None,
    release_date: str | None = None,
    expires_at: str | None = None,
) -> None:
    """Record a pending game in the mode-specific table."""
    if search_mode == "backlog":
        db.record_backlog_pending(
            slug=slug,
            game_title=game_title,
            platform=platform,
            metascore=metascore,
            metascore_reviews=metascore_reviews,
            user_score=user_score,
            user_reviews=user_reviews,
            release_date=release_date,
            expires_at=expires_at,
        )
    else:
        db.record_latest_pending(
            slug=slug,
            game_title=game_title,
            platform=platform,
            metascore=metascore,
            metascore_reviews=metascore_reviews,
            user_score=user_score,
            user_reviews=user_reviews,
            release_date=release_date,
            expires_at=expires_at,
        )


def _update_pending_scores_by_mode(
    db: Database,
    slug: str,
    result: Any,
    search_mode: str,
    fitgirl_max_queue_days: int,
) -> None:
    """Update scores and expiry for a pending game in the mode-specific table."""
    if search_mode == "backlog":
        db.update_backlog_pending_scores(
            slug=slug,
            metascore=result.metascore,
            metascore_reviews=result.metascore_review_count,
            user_score=result.user_score,
            user_reviews=result.user_review_count,
        )
        db.update_backlog_pending_expiry(slug, fitgirl_max_queue_days)
    else:
        db.update_latest_pending_scores(
            slug=slug,
            metascore=result.metascore,
            metascore_reviews=result.metascore_review_count,
            user_score=result.user_score,
            user_reviews=result.user_review_count,
        )
        db.update_latest_pending_expiry(slug, fitgirl_max_queue_days)


def _should_process_by_age(game: Any, age_recheck_weeks: int | None) -> bool:
    """Return True if *game* is old enough to be permanently processed.

    When *age_recheck_weeks* is ``None`` or ``0``, processing is disabled.
    Games without a ``release_date`` are never processed (we can't determine
    their age).
    """
    if age_recheck_weeks is None:
        return False
    release_date = getattr(game, "release_date", None)
    if not release_date:
        return False
    return _is_older_than(release_date, days=age_recheck_weeks * 7)


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
    cancel_event: threading.Event | None = None,
    *,
    search_mode: str = "backlog",
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
        search_mode: "backlog" or "latest" — determines which pending queue to age out.

    Returns the count of games processed.
    """
    if not cfg.age_recheck_weeks:
        return 0

    pending = (
        db.get_backlog_pending(platform=platform)
        if search_mode == "backlog"
        else db.get_latest_pending(platform=platform)
    )
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
        if search_mode == "backlog":
            db.remove_backlog_pending(str(game.slug))
        else:
            db.remove_latest_pending(str(game.slug))
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
    search_mode: str = "latest",
) -> bool:
    """Process one score-check result. Returns True if the game was removed.

    Games that fail the score check are kept in the pending queue for
    re-verification on subsequent cycles.

    Args:
        db: Database instance.
        game: The pending game row.
        result: ScoreResult from the Metacritic lookup.
        thresholds: Dict with score threshold keys.
        reject_genre: Genre substrings to reject (case-insensitive).
        reject_title: Title substrings to reject (case-insensitive).
        fitgirl_max_queue_days: Days to extend pending expiry when scores pass.
            Set to 0 for indefinite pending (far-future expiry).
        search_mode: "backlog" or "latest" — determines which pending queue to use.
    """
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
        _remove_pending_by_mode(db, str(game.slug), search_mode)
        return True

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
        _remove_pending_by_mode(db, str(game.slug), search_mode)
        return True

    if result is None:
        _touch_pending_by_mode(db, str(game.slug), search_mode)
        logger.debug(
            "Keeping '{}' in queue \u2014 game not found on Metacritic page",
            game.game_title,
        )
        return False

    if _scores_fail_check(result, thresholds):
        _touch_pending_by_mode(db, str(game.slug), search_mode)
        logger.debug(
            "Keeping '{}' in queue \u2014 Metacritic scores ({}, {}) below thresholds",
            game.game_title,
            result.metascore,
            result.user_score,
        )
        return False

    _update_pending_scores_by_mode(db, str(game.slug), result, search_mode, fitgirl_max_queue_days)
    logger.debug(
        "'{}' passed score check \u2014 ({}, {}) with ({} reviews, {} reviews)",
        game.game_title,
        result.metascore,
        result.user_score,
        result.metascore_review_count,
        result.user_review_count,
    )
    return False


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
    cancel_event: threading.Event | None = None,
    search_mode: str = "latest",
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
            result = fut.result()
            if result is not None:
                any_success = True
            if _process_verify_result(
                db,
                game,
                result,
                thresholds,
                reject_genre=reject_genre,
                reject_title=reject_title,
                fitgirl_max_queue_days=fitgirl_max_queue_days,
                search_mode=search_mode,
            ):
                removed += 1

        # Single summary line per batch instead of per-100-game progress spam.
        # Clamp subtraction: on cancellation, cache_hits from still-running
        # futures may exceed the count of consumed results.
        if checked > 0:
            logger.info(
                "Score check: {} verified ({} from cache, {} from Metacritic API)",
                checked,
                mc.cache_hits,
                max(0, checked - int(mc.cache_hits)),
            )
    finally:
        pool.shutdown(wait=False)

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
    cancel_event: threading.Event | None = None,
    search_mode: str = "latest",
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
    pending = (
        db.get_backlog_pending(platform=platform)
        if search_mode == "backlog"
        else db.get_latest_pending(platform=platform)
    )
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
        search_mode=search_mode,
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
    search_mode: str = "latest",
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
        if search_mode == "backlog":
            db.remove_backlog_pending(game_slug)
        else:
            db.remove_latest_pending(game_slug)
        logger.debug(
            "Skipped '{}' \u2014 scores missing or below thresholds",
            game_title,
        )
        return None
    # Update pending record with real detail-page scores
    if search_mode == "backlog":
        db.update_backlog_pending_scores(
            slug=game_slug,
            metascore=jit_result.metascore,
            metascore_reviews=jit_result.metascore_review_count,
            user_score=jit_result.user_score,
            user_reviews=jit_result.user_review_count,
        )
    else:
        db.update_latest_pending_scores(
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
    search_mode: str = "latest",
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
        search_mode=search_mode,
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
        search_mode=search_mode,
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
    search_mode: str = "latest",
    platform: str | None = None,
) -> list[dict[str, Any]]:
    """Match pending games against a torrent source index.

    For each non-expired pending game:
      1. Normalize its title
      2. Search ``source_titles`` for a match filtered by *source_name*
      3. On match: skip if already in library, otherwise verify the
         game's scores via *mc* before delivering (prevents games with
         wrong browse-only metrics from being downloaded)
      4. If no match: update ``last_checked_at``
      5. On expiry: move to history with ``result="Expired"``

    When *qbt* and *magnet_fetcher* are provided, matched games are
    delivered to qBittorrent.  Without them, only a history record
    is written (no download).

    Args:
        mc: MetacriticClient instance for just-in-time score verification.
        thresholds: Score thresholds for verification. Required when *mc*
            is provided.
        source_name: Name of the source to match against ("fitgirl").
        search_mode: "backlog" or "latest" — determines which pending queue to use.
        platform: Optional platform filter. When provided, only pending games
            matching this platform are processed.

    Returns a list of result dicts.
    """

    results: list[dict[str, Any]] = []

    can_deliver = qbt is not None and magnet_fetcher is not None

    # Match non-expired pending games whose scores have been checked
    # against the real Metacritic detail page.
    pending = (
        db.get_backlog_pending(platform=platform)
        if search_mode == "backlog"
        else db.get_latest_pending(platform=platform)
    )
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
            search_mode=search_mode,
        )
        if result is not None:
            results.append(result)

    # Expire overdue pending games
    results.extend(_process_expired_games(db, search_mode=search_mode))

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
    search_mode: str = "latest",
) -> dict[str, Any]:
    """Deliver a matched pending game to qBittorrent and emit notifications.

    Uses the pre-stored magnet from the source index if available,
    otherwise fetches the magnet from the source page.  Adds the
    torrent to qBittorrent, and sends a download notification on success
    or a failure notification on error.  Always returns a result dict and
    removes the pending row.

    Returns:
        A result dict with ``result`` set to ``"Passed"`` on successful
        delivery, or ``"Error"`` on magnet-fetch / qBittorrent failure.
    """
    source_url: str = str(best["url"])
    # Use pre-stored magnet if available, otherwise fetch from the source page.
    magnet = best.get("magnet") or magnet_fetcher(source_url)
    # Always consume the cached page title (avoids unbounded growth on failed fetches).
    source_title = _fitgirl_page_title_cache.pop(source_url, None) or best["title"] or game_title
    if not magnet:
        logger.warning("No magnet found for matched '{}' at {}", game_title, source_url)
        record_result = _record_delivery_error(
            db,
            game_slug=game_slug,
            game_title=game_title,
            game_platform=game_platform,
            game_metascore=game_metascore,
            game_user_score=game_user_score,
            best=best,
            search_mode=search_mode,
        )
        _safe_notify(
            notifier,
            "send_failure_notification",
            title=game_title,
            reason=f"No magnet found at {source_url}",
        )
        return record_result

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
            search_mode=search_mode,
        )
        _safe_notify(
            notifier,
            "send_failure_notification",
            title=game_title,
            reason=f"qBittorrent rejected: source={best['url']}",
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

    logger.info("\u2713 Sent matched '{}' to qBittorrent (tag: {})", _escape_markup(game_title), tag)
    record_result = _record_result(
        db,
        source="metacritic",
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
    if search_mode == "backlog":
        db.remove_backlog_pending(game_slug)
    else:
        db.remove_latest_pending(game_slug)
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


def _skip_for_reject_keywords(
    source_name: str,
    db: Database,
    best: dict[str, Any],
    game_title: str,
    game_slug: str,
    reject_keywords: list[str] | None,
    search_mode: str = "latest",
) -> bool:
    """Return True if the source match should be skipped due to rejected keywords.

    Only applies to FitGirl — FreeGOG pages don't have the same article body
    structure with HV/hypervisor keywords and use standard HTTPS (verify=True).
    """
    return source_name.casefold() == "fitgirl" and _check_reject_keywords(
        db, best, game_title, game_slug, reject_keywords, search_mode=search_mode
    )


def _check_reject_keywords(
    db: Database,
    best: dict[str, Any],
    game_title: str,
    game_slug: str,
    reject_keywords: list[str] | None,
    search_mode: str = "latest",
) -> bool:
    """Check whether a match should be skipped due to rejected keywords.

    Returns True if the match should be skipped, keeping the game
    pending for the next cycle.  Fetches the FitGirl HTML page and
    checks the **article body** first (most descriptive), then the
    HTML ``<title>`` tag, against *reject_keywords*.  When the page
    cannot be fetched, the match is kept pending for re-check in the
    next cycle (the sitemap title is too weak a signal).

    The check is deliberately scoped to the ``<article>`` element to
    exclude sidebar navigation (which contains "Hypervisor" links on
    every page) and the comments section (rogue comments should never
    block a download).  Article text is additionally truncated before
    the "Backwards Compatibility" section to avoid false positives from
    historical references to previous repacks.
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
            "Could not fetch page content for '{}' \u2014 keeping '{}' pending for re-check",
            best["url"],
            game_title,
        )
        _touch_pending_by_mode(db, game_slug, search_mode)
        return True

    # Truncate article text before "Backwards Compatibility" section.
    # FitGirl pages describe backwards compatibility with previous repacks
    # there, which may reference keywords (e.g. "HV", "hypervisor") from
    # older releases — causing false positives for current non-HV repacks.
    article_text = _truncate_at_backwards_compat(article_text)

    if article_text and (matched := _title_contains_keywords(article_text, reject_keywords)):
        logger.info(
            "Skipping match for '{}' \u2014 FitGirl article body contains rejected keyword '{}'",
            game_title,
            matched,
        )
        _touch_pending_by_mode(db, game_slug, search_mode)
        return True

    if page_title and (matched := _title_contains_keywords(page_title, reject_keywords)):
        logger.info(
            "Skipping match for '{}' \u2014 FitGirl page title '{}' contains rejected keyword '{}'",
            game_title,
            page_title,
            matched,
        )
        _touch_pending_by_mode(db, game_slug, search_mode)
        return True

    return False


def _truncate_at_backwards_compat(article_text: str | None) -> str | None:
    """Truncate *article_text* before the backwards-compatibility section.

    FitGirl article pages include a "Backwards Compatibility" section that
    describes how to migrate from a previous repack (which may have been an
    HV/hypervisor release). Checking keywords in that section produces false
    positives for the current (non-HV) repack.

    Returns the original text when the section marker is absent or when
    *article_text* is ``None``.
    """
    if not article_text:
        return article_text
    idx = article_text.lower().find("backwards compatibility")
    return article_text[:idx] if idx >= 0 else article_text


# Roman numeral -> Arabic substitution patterns used by _tokenize_title.
# Must match the order in gamarr.utils._ROMAN_TO_ARABIC (longer patterns first).
_ROMAN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bxii\b"), "12"),
    (re.compile(r"\bxi\b"), "11"),
    (re.compile(r"\bix\b"), "9"),
    (re.compile(r"\bviii\b"), "8"),
    (re.compile(r"\bvii\b"), "7"),
    (re.compile(r"\bvi\b"), "6"),
    (re.compile(r"\biv\b"), "4"),
    (re.compile(r"\biii\b"), "3"),
    (re.compile(r"\bii\b"), "2"),
    (re.compile(r"\bx\b"), "10"),
    (re.compile(r"\bv\b"), "5"),
    (re.compile(r"\bi\b"), "1"),
]


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
    for pattern, replacement in _ROMAN_PATTERNS:
        text = pattern.sub(replacement, text)
    tokens = {token.strip() for token in re.split(r"[^a-z0-9]+", text) if token.strip()}
    return tokens - _STOP_WORDS


def _titles_share_enough_tokens(
    title_a: str,
    title_b: str,
    min_tokens: int = 3,
) -> bool:
    """Return True if *title_a* and *title_b* share at least *min_tokens* word tokens."""
    tokens_a = _tokenize_title(title_a)
    tokens_b = _tokenize_title(title_b)
    return len(tokens_a & tokens_b) >= min_tokens


def _check_candidate_for_dlc_match(
    candidate: dict[str, str | None],
    normalized: str,
    *,
    substring_match: bool = True,
) -> bool:
    """Check whether *candidate* matches *normalized* via DLC-aware analysis.

    Returns True if the candidate's repack page covers *normalized*.

    When *substring_match* is True (candidate found via substring containment),
    the page-title DLC-keyword check is a reliable fast path — the sitemap
    title is a substring of the pending title, so they are the same game.

    When *substring_match* is False (candidate found via token overlap),
    the article body MUST be checked to prevent false positives from
    unrelated games that share token overlap with the pending title.
    """
    page_title, article_text = _fetch_fitgirl_page_content(str(candidate["url"]))
    # Page-title DLC keywords are a reliable signal only for substring matches.
    # For token-overlap candidates, always require article body confirmation
    # to prevent false positives across different games in the same franchise.
    if substring_match and _page_title_has_dlc_keywords(page_title):
        return True
    if article_text:
        article_norm = normalise_for_compare(article_text)
        # Named DLC match
        if normalized in article_norm:
            return True
        # All-DLCs match — including the page-title keyword pattern
        # for token-overlap candidates (the article body MUST confirm)
        if _article_contains_all_dlcs(article_text):
            return True
    return False


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

    Candidates are selected when the source title is a normalised
    substring of the pending title, OR when they share at least 3 word
    tokens (after Roman numeral conversion).  This prevents needless
    HTTP requests for games with no connection to any FitGirl repack.
    At most 3 article pages are fetched to limit HTTP overhead.

    Args:
        db: Database instance for source title lookup.
        source_name: Source identifier (e.g. ``"fitgirl"``).
        normalized: The normalised Metacritic game title.
        pending_title: The original (pre-normalisation) Metacritic game
            title, used for token overlap matching.

    Returns:
        A list with one match dict if found in article body, or an empty list.
    """
    candidates: list[tuple[dict[str, str | None], bool]] = []
    for entry in db.get_all_source_titles(source_name):
        entry_title = str(entry.get("title", ""))
        entry_norm = normalise_for_compare(entry_title)
        if not entry_norm or normalized == entry_norm:
            continue
        if entry_norm in normalized:
            candidates.append((entry, True))  # substring match
        elif _titles_share_enough_tokens(entry_title, pending_title):
            candidates.append((entry, False))  # token-overlap match

    # At most 3 HTTP requests to limit overhead
    for candidate, substring_match in candidates[:3]:
        if _check_candidate_for_dlc_match(candidate, normalized, substring_match=substring_match):
            return [candidate]

    return []


def _find_first_non_rejected_match(
    db: Database,
    source_name: str,
    matches: list[dict[str, str | None]],
    game_title: str,
    game_slug: str,
    reject_keywords: list[str] | None,
    search_mode: str,
) -> dict[str, str | None] | None:
    """Return the first match that passes reject_keywords, or None if all rejected.

    Iterates through *matches* in order, skipping any whose article body
    or page title contains a rejected keyword.  The first clean match is
    returned; ``None`` means every candidate was rejected and the game
    should stay pending.
    """
    for candidate in matches:
        if _skip_for_reject_keywords(
            source_name, db, candidate, game_title, game_slug, reject_keywords, search_mode=search_mode
        ):
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
    search_mode: str = "latest",
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
                search_mode=search_mode,
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
            search_mode=search_mode,
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
        search_mode=search_mode,
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
    search_mode: str = "latest",
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
        _touch_pending_by_mode(db, game_slug, search_mode)
        logger.info(
            "Title: '{}' passed Metacritic checks but has no {} match \u2014 staying in queue",
            game_title,
            _source_display(source_name),
        )
        return None

    # Iterate through matches — if one is rejected by keywords, try the next.
    best = _find_first_non_rejected_match(db, source_name, matches, game_title, game_slug, reject_keywords, search_mode)

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
        search_mode=search_mode,
    )


def _process_expired_games(
    db: Database,
    search_mode: str = "latest",
) -> list[dict[str, Any]]:
    """Move expired pending games to history and return their result dicts."""
    results: list[dict[str, Any]] = []
    expired = db.get_expired_backlog_pending() if search_mode == "backlog" else db.get_expired_latest_pending()
    for game in expired:
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
            result="Expired",
            result_details="Not available on any source within pending window",
        )
        record_result["slug"] = game_slug
        if search_mode == "backlog":
            db.remove_backlog_pending(game_slug)
        else:
            db.remove_latest_pending(game_slug)
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
    search_mode: str = "latest",
) -> dict[str, Any]:
    """Record that a matched game was already found in the local library."""
    record_result = _record_result(
        db,
        source="metacritic",
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
    if search_mode == "backlog":
        db.remove_backlog_pending(game_slug)
    else:
        db.remove_latest_pending(game_slug)
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
    search_mode: str = "latest",
) -> dict[str, Any]:
    """Record a matched game to history without delivering to qBittorrent."""
    record_result = _record_result(
        db,
        source="metacritic",
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
    if search_mode == "backlog":
        db.remove_backlog_pending(game_slug)
    else:
        db.remove_latest_pending(game_slug)
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
    search_mode: str = "latest",
) -> dict[str, Any]:
    """Record an error result for a failed delivery and remove the pending row.

    Returns the result dict with the ``slug`` key set.
    """
    record_result = _record_result(
        db,
        source="metacritic",
        source_title=game_title,
        source_url=f"mc:{game_slug}",
        game_title=game_title,
        platform=game_platform,
        metascore=game_metascore,
        user_score=game_user_score,
        result="Error",
        result_details=f"Match found at {best['url']} but delivery failed",
    )
    record_result["slug"] = game_slug
    if search_mode == "backlog":
        db.remove_backlog_pending(game_slug)
    else:
        db.remove_latest_pending(game_slug)
    return record_result


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


def _fetch_fitgirl_page_content(url: str) -> tuple[str | None, str | None]:
    """Fetch a FitGirl repack page and extract its <title> and <article> text.

    Returns ``(title, article_text)`` where both are ``None`` if the page
    could not be fetched or the tag is missing.  The title has HTML entities
    decoded (e.g. ``&#039;`` → ``'``, ``&quot;`` → ``"``).  The article
    text has HTML tags stripped and whitespace collapsed.

    Returns:
        A ``(title, article_text)`` tuple.
    """
    try:
        # Only fetch from FitGirl (uses self-signed cert — verify=False is intentional)
        if not url.startswith("https://fitgirl-repacks.site"):
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
        article_text = re.sub(r"<[^>]+>", " ", article_match.group(1))
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
        if url.startswith("https://fitgirl-repacks.site"):
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
