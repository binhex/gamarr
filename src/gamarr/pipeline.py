"""Acquisition pipeline for gamarr."""

from __future__ import annotations

import datetime
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import requests
from loguru import logger

from gamarr.database import Database
from gamarr.metacritic import MetacriticClient
from gamarr.metacritic_cache import MetacriticCache
from gamarr.notifications import Notifier
from gamarr.qbittorrent import QBittorrentClient
from gamarr.sources.fitgirl import _USER_AGENT, FitGirlSource, _extract_magnet_from_html
from gamarr.utils import normalise_for_compare

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
        f"<cyan><bold>{title}</bold></cyan>"
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
    days_since_release: int
    cache_ttl_days: int = 7
    cache_ttl_hours: int = 6
    enabled: bool = True
    pending_days: int = 30
    max_games: int = 1000
    cutoff_weeks: int | None = None
    reject_genre: list[str] | None = None
    reject_title: list[str] | None = None  # ← new
    fitgirl_pending_days: int = 60


def _is_below_threshold(value: float | None, threshold: float) -> bool:
    """Return True if value is below threshold (or None — missing data fails).

    When value is None the check fails (no data to verify against).
    """
    if value is None:
        return True
    return value < threshold


def _evaluate_scores(
    mc_result: Any,
    cfg: AcquisitionConfig,
) -> str:
    """Evaluate a game's scores against thresholds.

    Returns:
        ``"Passed"`` if all checks pass, or a specific failure reason:
        ``"no_scores"``, ``"metascore_too_low"``, ``"metascore_reviews_too_few"``,
        ``"user_score_too_low"``, ``"user_reviews_too_few"``, ``"release_date_too_old"``.
    """
    if mc_result.metascore is None or mc_result.user_score is None:
        return "no_scores"

    if _is_below_threshold(mc_result.metascore, cfg.min_metascore):
        return "metascore_too_low"
    if _is_below_threshold(mc_result.metascore_review_count, cfg.min_metascore_reviews):
        return "metascore_reviews_too_few"
    if _is_below_threshold(mc_result.user_score, cfg.min_user_score):
        return "user_score_too_low"
    if _is_below_threshold(mc_result.user_review_count, cfg.min_user_reviews):
        return "user_reviews_too_few"

    if _is_older_than(getattr(mc_result, "release_date", None), cfg.days_since_release):
        return "release_date_too_old"

    return "Passed"


def run_acquisition(
    *,
    fitgirl_rss_url: str,
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
    days_since_release: int = 90,
    cache_ttl_days: int = 7,
    cache_ttl_hours: int = 6,
    enabled: bool = True,
    pending_days: int = 30,
    fitgirl_pending_days: int = 60,
    max_games: int = 1000,
    cutoff_weeks: int | None = None,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,  # ← new
    apprise_urls: list[str] | None = None,
    notify_on_download: bool = True,
    notify_on_failure: bool = False,
    notify_on_error: bool = False,
    library_paths: list[str] | None = None,
    fitgirl_cache_ttl_hours: int = 6,
    fitgirl_reject_keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Execute one scan cycle.

    Discovers games by browsing Metacritic (newest-first, up to
    ``max_games`` entries), verifies each game's real
    Metacritic detail-page scores against the configured thresholds,
    then matches survivors against the FitGirl sitemap and delivers
    to qBittorrent.
    """
    cfg = AcquisitionConfig(
        min_metascore=min_metascore,
        min_metascore_reviews=min_metascore_reviews,
        min_user_score=min_user_score,
        min_user_reviews=min_user_reviews,
        days_since_release=days_since_release,
        cache_ttl_days=cache_ttl_days,
        cache_ttl_hours=cache_ttl_hours,
        enabled=enabled,
        pending_days=pending_days,
        fitgirl_pending_days=fitgirl_pending_days,
        max_games=max_games,
        cutoff_weeks=cutoff_weeks,
        reject_genre=reject_genre,
        reject_title=reject_title,  # ← new
    )

    logger.info("Starting scan cycle (platform='{}')", platform)

    db = Database(db_path)
    source = FitGirlSource(
        rss_url=fitgirl_rss_url,
        platform=platform,
        db=db,
        cache_ttl_hours=fitgirl_cache_ttl_hours,
    )
    mc = MetacriticClient(cache=MetacriticCache(db))

    notifier = Notifier(
        apprise_urls=apprise_urls,
        on_download=notify_on_download,
        on_failure=notify_on_failure,
        on_error=notify_on_error,
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
        source.close()
        mc.close()
        db.close()
        return []

    def _run_discovery_phases(
        source: Any,
        mc: Any,
        db: Database,
        cfg: AcquisitionConfig,
        platform: str,
        qbt: Any,
        notifier: Any,
    ) -> list[dict[str, Any]]:
        """Run Metacritic browse, sitemap fetch (if needed), and pending matching.

        Returns combined results from pending-game matching.

        Metacritic-first: browse Metacritic first; only fetch the FitGirl
        sitemap if Metacritic produced at least one game to match.
        """
        # Metacritic-first discovery: browse Metacritic for games that pass
        # score thresholds and age filter. The FitGirl sitemap is fetched
        # only if Metacritic produced games to match against.
        browse_games: list[dict[str, Any]] = []
        if cfg.enabled:
            # Compute absolute cutoff date from cutoff_weeks (if set and > 0)
            cutoff_date: str | None = None
            if cfg.cutoff_weeks is not None and cfg.cutoff_weeks > 0:
                cutoff_date = (
                    datetime.datetime.now(tz=datetime.UTC).date() - datetime.timedelta(weeks=cfg.cutoff_weeks)
                ).isoformat()

            browse_games = mc.scan_recent_games(
                platform,
                max_games=cfg.max_games,
                cache_ttl_hours=cfg.cache_ttl_hours,
                cutoff_date=cutoff_date,
            )
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
                    pending_days=cfg.pending_days,
                    days_since_release=cfg.days_since_release,
                    reject_title=cfg.reject_title,  # ← new
                )
                if new_pending:
                    logger.info(
                        "{} of {} collected games passed title/age filters — added to pending queue",
                        new_pending,
                        len(browse_games),
                    )
        # After the browse step, re-verify every pending game against
        # the real Metacritic detail page.  Browse-page Nuxt data does
        # NOT carry standard 0\u2013100 metascores or 0\u201310 user scores
        # \u2014 the \"score\" fields are internal browse-only metrics.
        # Games whose detail-page scores fail the configured thresholds
        # are removed from pending.
        pending_games = db.get_pending(platform=platform)
        if pending_games:
            logger.info(
                "Proceeding to verify {} pending games against real Metacritic scores",
                len(pending_games),
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
                cache_ttl_days=cfg.cache_ttl_days,
                max_verify=len(pending_games) if cfg.max_games == 0 else min(len(pending_games), cfg.max_games),
                reject_genre=cfg.reject_genre,
                reject_title=cfg.reject_title,  # ← new
                fitgirl_pending_days=cfg.fitgirl_pending_days,  # ← new
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
        # Only fetch the FitGirl sitemap if there are score-checked games
        # to match against it. Games whose real Metacritic scores haven't
        # been checked yet wait for the next cycle and do NOT trigger the
        # FitGirl sitemap fetch.
        if db.has_verified_pending(platform=platform):
            source.fetch_sitemap(db)
        match_thresholds = {
            "min_metascore": cfg.min_metascore,
            "min_metascore_reviews": cfg.min_metascore_reviews,
            "min_user_score": cfg.min_user_score,
            "min_user_reviews": cfg.min_user_reviews,
        }
        matched = _match_pending_games(
            db,
            qbt=qbt,
            magnet_fetcher=_default_magnet_fetcher,
            notifier=notifier,
            library=library,
            mc=mc,
            thresholds=match_thresholds,
            reject_keywords=fitgirl_reject_keywords or None,
        )
        if matched:
            logger.info("{} queued games found on FitGirl", len(matched))

        return matched

    try:
        # Metacritic-first acquisition: discover games via Metacritic browse,
        # match against the FitGirl sitemap, and only then deliver to qBittorrent.
        # FitGirl RSS entries must NOT drive per-entry Metacritic lookups.
        return _run_discovery_phases(source, mc, db, cfg, platform, qbt, notifier)
    finally:
        source.close()  # source._db.close() disposes the shared Database engine
        mc.close()
        if source._db is not db:  # Only close separately if not shared
            db.close()


def _title_contains_keywords(title: str, keywords: list[str] | None) -> bool:
    """Return True if *title* case-insensitively matches any *keywords*."""
    if not keywords:
        return False
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in keywords)


def _title_matches_reject(title: str, reject_title: list[str] | None) -> bool:
    """Return True if *title* case-insensitively matches any reject_title entry."""
    if not reject_title:
        return False
    title_lower = title.lower()
    return any(term.lower() in title_lower for term in reject_title)


def _game_passes_thresholds(game: dict[str, Any], thresholds: dict[str, Any]) -> bool:
    """Check if a browse-page game dict passes all score thresholds."""
    metascore = game.get("score")
    user_score = game.get("user_rating")
    if metascore is None or user_score is None:
        return False
    # ``critic_review_count`` and ``user_review_count`` are not always
    # available in the Metacritic browse page Nuxt data (the browse
    # listing only carries ``criticScoreSummary.score`` and
    # ``userScore.score``).  When the field is missing we skip that
    # specific check rather than treating it as 0, which would silently
    # drop every browse-page game.
    critic_reviews = game.get("critic_review_count")
    user_reviews = game.get("user_review_count")
    return all(
        [
            metascore >= thresholds["min_metascore"],
            critic_reviews is None or critic_reviews >= thresholds["min_metascore_reviews"],
            user_score >= thresholds["min_user_score"],
            user_reviews is None or user_reviews >= thresholds["min_user_reviews"],
        ]
    )


def _is_game_eligible(
    game: dict[str, Any],
    db: Database,
    thresholds: dict[str, Any],
    days_since_release: int,
) -> bool:
    """Return True if *game* passes all filters and should be added to pending."""
    slug = game.get("slug", "")
    title = game.get("title", "")
    if not slug or not title:
        return False
    if db.is_processed("metacritic", f"mc:{slug}") or db.is_pending(slug):
        return False
    if not _game_passes_thresholds(game, thresholds):
        return False
    if _is_older_than(game.get("release_date"), days_since_release):
        logger.debug(
            "Skipping '{}' ({}): older than {} days",
            title,
            game.get("release_date"),
            days_since_release,
        )
        return False
    return True


def _process_browse_games(
    browse_games: list[dict[str, Any]],
    platform: str,
    db: Database,
    thresholds: dict[str, Any],
    *,
    pending_days: int = 30,
    days_since_release: int = 0,
    reject_title: list[str] | None = None,  # ← new
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
        pending_days: How many days to keep the game pending before expiry.
        days_since_release: Max age in days. Games older than this are skipped.
        reject_title: Titles matching any of these are skipped.

    Returns:
        Number of new pending games added.
    """
    new_count = 0
    for game in browse_games:
        if not _is_game_eligible(game, db, thresholds, days_since_release):
            continue
        if _title_matches_reject(game.get("title", ""), reject_title):
            logger.debug("Skipping '{}' — matches reject_title", game.get("title", ""))
            continue

        g_slug = game.get("slug", "")
        g_title = game.get("title", "")
        if pending_days <= 0:
            expires_at = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=9999)).isoformat()
        else:
            expires_at = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=pending_days)).isoformat()

        db.record_pending(
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
    """Log periodic progress during score verification."""
    if verified > 0 and verified % 10 == 0:
        logger.info(
            "Fetching Metacritic details for {} of {} games...",
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
    """Compare a score value against a threshold if the value is meaningful (> 0).

    Returns True/False when the value passes/fails, or None when the
    value is absent or zero (no review data to check).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and value <= 0:
        return None
    return value >= threshold


def _real_scores_pass_thresholds(
    result: Any,
    thresholds: dict[str, Any],
) -> bool:
    """Check a ScoreResult (from the detail page) against configured thresholds.

    When a score value is None or 0.0 (unreviewed game), that specific
    check is skipped rather than treated as a failure.

    Returns True when the existing checks all pass.
    """
    if result is None:
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
    db.remove_pending(str(game.slug))


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
                logger.info(
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
            logger.info(
                "Removing '{}' — title matches reject_title '{}'",
                game.game_title,
                term,
            )
            return str(term)
    return None


def _scores_fail_check(result: Any, thresholds: dict[str, Any]) -> bool:
    """Return True when scores are missing or fail the configured thresholds."""
    return not _scores_present(result) or not _real_scores_pass_thresholds(result, thresholds)


def _process_verify_result(
    db: Database,
    game: Any,
    result: Any,
    thresholds: dict[str, Any],
    *,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,  # ← new
    fitgirl_pending_days: int = 60,  # ← new
) -> bool:
    """Process one score-check result. Returns True if the game was removed.

    Games that fail the score check are kept in the pending queue for
    re-verification on subsequent cycles.

    Args:
        fitgirl_pending_days: Days to extend pending expiry when scores pass.
            Set to 0 for indefinite pending (far-future expiry).
    """
    matched_genre = _reject_by_genre(game, result, reject_genre)
    if matched_genre is not None:
        attempts = db.increment_verify_attempts(str(game.slug))
        _fail_game_after_max_attempts(
            db,
            game,
            result,
            attempts=attempts,
            result_details=f"Game '{game.game_title}' — genre '{matched_genre}' is in reject_genre list",
        )
        return True

    matched_title = _reject_by_title(game, reject_title)
    if matched_title is not None:
        db.increment_verify_attempts(str(game.slug))
        _fail_game_after_max_attempts(
            db,
            game,
            result,
            attempts=0,
            result_details=f"Game '{game.game_title}' — title matches reject_title '{matched_title}'",
        )
        return True

    if result is None:
        logger.debug(
            "Keeping '{}' in queue \u2014 game not found on Metacritic page",
            game.game_title,
        )
        return False

    if _scores_fail_check(result, thresholds):
        logger.debug(
            "Keeping '{}' in queue \u2014 Metacritic scores ({}, {}) below thresholds",
            game.game_title,
            result.metascore,
            result.user_score,
        )
        return False

    db.update_pending_scores(
        slug=str(game.slug),
        metascore=result.metascore,
        metascore_reviews=result.metascore_review_count,
        user_score=result.user_score,
        user_reviews=result.user_review_count,
    )
    db.reset_verify_attempts(str(game.slug))
    db.update_pending_expiry(str(game.slug), fitgirl_pending_days)
    logger.debug(
        "'{}' passed score check \u2014 ({}, {}) with ({} reviews, {} reviews)",
        game.game_title,
        result.metascore,
        result.user_score,
        result.metascore_review_count,
        result.user_review_count,
    )
    return False


def _verify_pending_scores(
    db: Database,
    mc: MetacriticClient,
    platform: str,
    thresholds: dict[str, Any],
    *,
    cache_ttl_days: int = 7,
    max_verify: int = 50,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,  # ← new
    fitgirl_pending_days: int = 60,  # ← new
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
        cache_ttl_days: TTL for the detail-page cache.
        max_verify: Maximum number of games to verify per cycle.
            Set to 0 to skip verification entirely.
        reject_genre: List of genre substrings to reject (case-insensitive).
            Games whose genre contains any entry are removed immediately.
            E.g. ``["RPG"]`` matches ``"Action RPG"``, ``"JRPG"``, etc.
        reject_title: List of title substrings to reject (case-insensitive).
            Games whose title contains any entry are removed immediately.
        fitgirl_pending_days: Passed through to _process_verify_result for
            expiry recalculation when scores pass.

    Returns the number of games removed.
    """
    if max_verify <= 0:
        return 0

    removed = 0
    verified = 0
    pending = db.get_pending(platform=platform)
    total_pending = len(pending)

    # Collect the batch of games to check this cycle
    batch = list(pending[:max_verify])

    if not batch:
        return 0

    # Submit all Metacritic lookups concurrently (I/O-bound HTTP requests)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [
            pool.submit(
                mc.lookup_game,
                str(game.game_title),
                platform=platform,
                slug=str(game.slug),
                cache_ttl_days=cache_ttl_days,
                direct_only=True,
            )
            for game in batch
        ]

        # Process results in original batch order
        for verified, (game, fut) in enumerate(zip(batch, futures, strict=True)):
            _log_verify_progress(verified, max_verify, total_pending)
            result = fut.result()
            if _process_verify_result(
                db,
                game,
                result,
                thresholds,
                reject_genre=reject_genre,
                reject_title=reject_title,  # ← new
                fitgirl_pending_days=fitgirl_pending_days,  # ← new
            ):
                removed += 1

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
    user_reviews, genres, must_play, description) on success, or ``None``
    when the game was removed.  When *mc* or *thresholds* is not
    provided, returns an empty tuple ``()`` signalling "use original
    scores, skip verification".
    """
    if mc is None or thresholds is None:
        return ()
    jit_result = mc.lookup_game(
        game_title,
        platform=game_platform,
        slug=game_slug,
        direct_only=True,
    )
    if jit_result is None or not (_scores_present(jit_result) and _real_scores_pass_thresholds(jit_result, thresholds)):
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
) -> list[dict[str, Any]]:
    """Match pending games against torrent source indices.

    For each non-expired pending game:
      1. Normalize its title
      2. Search ``source_titles`` for a match (currently FitGirl only)
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

    Returns a list of result dicts.
    """

    results: list[dict[str, Any]] = []

    can_deliver = qbt is not None and magnet_fetcher is not None

    # Match non-expired pending games whose scores have been checked
    # against the real Metacritic detail page.
    pending = db.get_pending()
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
        )
        if result is not None:
            results.append(result)

    # Expire overdue pending games
    results.extend(_process_expired_games(db))

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
) -> dict[str, Any]:
    """Deliver a matched pending game to qBittorrent and emit notifications.

    Fetches the magnet link from the source URL, adds the torrent to
    qBittorrent, and sends a download notification on success or a
    failure notification on error.  Always returns a result dict and
    removes the pending row.

    Returns:
        A result dict with ``result`` set to ``"Passed"`` on successful
        delivery, or ``"Error`` on magnet-fetch / qBittorrent failure.
    """
    source_url: str = str(best["url"])
    magnet = magnet_fetcher(source_url)
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
        )
        _safe_notify(
            notifier,
            "send_failure_notification",
            title=game_title,
            reason=f"No magnet found at {source_url}",
        )
        return record_result

    tag = qbt.add_torrent(magnet_url=magnet, title=game_title)
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
        user_score=game_user_score,
        magnet_url=magnet,
    )
    return record_result


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
) -> dict[str, Any] | None:
    """Match one pending game against FitGirl sitemap and either deliver or touch."""
    normalized = normalise_for_compare(game_title)
    matches = db.match_source_title("fitgirl", normalized)
    if not matches:
        db.touch_pending(game_slug)
        logger.debug(
            "'{}' has no FitGirl match \u2014 staying in queue",
            game_title,
        )
        return None

    best = matches[0]
    logger.info(
        "FitGirl match: '{}' \u2192 '{}' ({})",
        game_title,
        best["title"],
        best["url"],
    )

    # Skip matches whose FitGirl title contains rejected keywords
    if _title_contains_keywords(best["title"], reject_keywords):
        logger.info(
            "Skipping match for '{}' \u2014 FitGirl title '{}' contains rejected keyword",
            game_title,
            best["title"],
        )
        db.touch_pending(game_slug)
        return None

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
            )

    # If qbt and magnet_fetcher are provided, deliver the torrent
    if can_deliver:
        assert qbt is not None and magnet_fetcher is not None
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
    )


def _process_expired_games(db: Database) -> list[dict[str, Any]]:
    """Move expired pending games to history and return their result dicts."""
    results: list[dict[str, Any]] = []
    expired = db.get_expired_pending()
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
        db.remove_pending(game_slug)
        results.append(record_result)
        logger.info("'{}' expired \u2014 queued too long, no FitGirl match found", game_title)
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
        result_details=f"Matched source: {best['url']}",
    )
    record_result["slug"] = game_slug
    db.remove_pending(game_slug)
    logger.info("\u2713 '{}' matched to FitGirl \u2014 logged (no downloader configured)", game_title)
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
    db.remove_pending(game_slug)
    return record_result


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
    )
    return {
        "result": result,
        "game_title": game_title,
        "metascore": metascore,
        "user_score": user_score,
        "result_details": result_details,
    }


_SITEMAP_TIMEOUT = 30.0


def _default_magnet_fetcher(url: str) -> str | None:
    """Fetch a FitGirl page and extract its magnet link."""
    try:
        if not url.startswith("https://"):
            logger.warning("Skipping non-HTTPS magnet source: {}", url)
            return None
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_SITEMAP_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Magnet fetch failed for {}: {}", url, exc)
        return None
    return _extract_magnet_from_html(resp.text)
