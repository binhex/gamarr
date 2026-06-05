"""Acquisition pipeline for gamarr."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from gamarr.database import Database
from gamarr.metacritic import MetacriticClient
from gamarr.notifications import Notifier
from gamarr.qbittorrent import QBittorrentClient
from gamarr.sources.fitgirl import FitGirlSource

if TYPE_CHECKING:
    from gamarr.models import GameEntry


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


@dataclass
class AcquisitionConfig:
    """Thresholds and settings for the acquisition run."""

    min_metascore: int
    min_metascore_reviews: int
    min_user_score: float
    min_user_reviews: int
    days_since_release: int
    cache_ttl_days: int = 7
    browse_cache_ttl_hours: int = 4
    browse_enabled: bool = True
    pending_days: int = 30


def _score_check(value: float | None, threshold: float) -> bool:
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
    if mc_result.metascore is None or mc_result.user_score is None:
        return "Failed"

    if _score_check(mc_result.metascore, cfg.min_metascore):
        return "Failed"
    if _score_check(mc_result.metascore_review_count, cfg.min_metascore_reviews):
        return "Failed"
    if _score_check(mc_result.user_score, cfg.min_user_score):
        return "Failed"
    if _score_check(mc_result.user_review_count, cfg.min_user_reviews):
        return "Failed"

    return "Passed"


def run_acquisition(
    *,
    fitgirl_rss_url: str,
    platform: str = "pc",
    db_path: str = ":memory:",
    mc_cache_path: str = ":memory:",
    qbt_host: str = "localhost",
    qbt_port: int = 8080,
    qbt_username: str = "admin",
    qbt_password: str = "adminadmin",
    qbt_category: str = "games-gamarr",
    qbt_add_paused: bool = False,
    min_metascore: int = 75,
    min_metascore_reviews: int = 5,
    min_user_score: float = 7.5,
    min_user_reviews: int = 10,
    days_since_release: int = 90,
    cache_ttl_days: int = 7,
    browse_cache_ttl_hours: int = 4,
    browse_enabled: bool = True,
    pending_days: int = 30,
    apprise_urls: list[str] | None = None,
    notify_on_download: bool = True,
    notify_on_failure: bool = False,
    notify_on_error: bool = False,
    library_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Execute one acquisition cycle."""
    cfg = AcquisitionConfig(
        min_metascore=min_metascore,
        min_metascore_reviews=min_metascore_reviews,
        min_user_score=min_user_score,
        min_user_reviews=min_user_reviews,
        days_since_release=days_since_release,
        cache_ttl_days=cache_ttl_days,
        browse_cache_ttl_hours=browse_cache_ttl_hours,
        browse_enabled=browse_enabled,
        pending_days=pending_days,
    )

    logger.info("Starting acquisition cycle (platform='{}')", platform)

    source = FitGirlSource(rss_url=fitgirl_rss_url, platform=platform, db_path=db_path)
    mc = MetacriticClient(cache_path=mc_cache_path)
    db = Database(db_path)

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
        logger.warning("qBittorrent is not reachable; skipping acquisition.")
        notifier.send_error_notification("qBittorrent is not reachable")
        source.close()
        mc.close()
        db.close()
        return []

    from gamarr.library import LibraryScanner

    library = LibraryScanner(library_paths)

    try:
        # ── Phase 1: Build FitGirl source index ──
        source.fetch_sitemap(db)

        # ── Phase 2: Metacritic browse — discover new games ──
        if cfg.browse_enabled:
            browse_games = mc.scan_recent_games(
                platform,
                max_pages=10,
                browse_cache_ttl_hours=cfg.browse_cache_ttl_hours,
            )
            if browse_games:
                thresholds = {
                    "min_metascore": cfg.min_metascore,
                    "min_metascore_reviews": cfg.min_metascore_reviews,
                    "min_user_score": cfg.min_user_score,
                    "min_user_reviews": cfg.min_user_reviews,
                }
                new_pending = _process_browse_games(
                    browse_games, platform, db, thresholds,
                    pending_days=cfg.pending_days,
                )
                if new_pending:
                    logger.info("Browse added {} new pending games", new_pending)

        # ── Phase 3: Match pending games against sources ──
        matched = _match_pending_games(db, qbt=qbt, notifier=notifier)
        if matched:
            logger.info("Matched {} pending games to sources", len(matched))

        entries = source.fetch_new()
        if not entries:
            logger.info("No new entries found.")
            return []

        results: list[dict[str, Any]] = []
        for entry in entries:
            match = library.check_game(entry.title)
            if match:
                db.record_processed(
                    source=entry.source,
                    source_title=entry.source_title,
                    source_url=entry.source_url,
                    game_title=entry.title,
                    platform=entry.platform,
                    result="Already owned",
                    result_details=f"Found in library: {match.matched_path}",
                )
                logger.info(
                    "Already in library, skipping: '{}' (matched: '{}' at {})",
                    entry.title,
                    match.matched_name,
                    match.matched_path,
                )
                results.append(
                    {
                        "result": "Already owned",
                        "game_title": entry.title,
                        "result_details": f"Found in library: {match.matched_path}",
                    }
                )
                continue

            result = _process_entry(entry, cfg, mc, qbt, db, notifier)
            results.append(result)
        return results
    finally:
        source.close()
        mc.close()
        db.close()


def _process_browse_games(
    browse_games: list[dict[str, Any]],
    platform: str,
    db: Database,
    thresholds: dict[str, Any],
    *,
    pending_days: int = 30,
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

    Returns:
        Number of new pending games added.
    """
    new_count = 0
    for game in browse_games:
        slug = game.get("slug", "")
        title = game.get("title", "")
        if not slug or not title:
            continue

        if db.is_processed("metacritic", f"mc:{slug}") or db.is_pending(slug):
            continue

        metascore = game.get("score")
        metascore_reviews = game.get("critic_review_count")
        user_score = game.get("user_rating")
        user_reviews = game.get("user_review_count")

        if metascore is None or user_score is None:
            continue
        if metascore < thresholds["min_metascore"]:
            continue
        if (metascore_reviews or 0) < thresholds["min_metascore_reviews"]:
            continue
        if user_score < thresholds["min_user_score"]:
            continue
        if (user_reviews or 0) < thresholds["min_user_reviews"]:
            continue

        expires_at = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=pending_days)).isoformat()

        db.record_pending(
            slug=slug,
            game_title=title,
            platform=platform,
            metascore=float(metascore) if metascore is not None else None,
            metascore_reviews=metascore_reviews,
            user_score=float(user_score) if user_score is not None else None,
            user_reviews=user_reviews,
            expires_at=expires_at,
        )
        new_count += 1
        logger.info(
            "Added pending game: '{}' (slug: {}, expires {})",
            title,
            slug,
            expires_at,
        )

    return new_count


def _match_pending_games(
    db: Database,
    *,
    pending_days: int = 30,
    qbt: Any = None,
    notifier: Any = None,
) -> list[dict[str, Any]]:
    """Match pending games against torrent source indices.

    For each non-expired pending game:
      1. Normalize its title
      2. Search ``source_titles`` for a match (currently FitGirl only)
      3. On match: record in history, remove from pending
      4. If no match: update ``last_checked_at``
      5. On expiry: move to history with ``result="Expired"``

    Returns a list of result dicts.
    """
    from gamarr.metacritic import _normalise_for_compare

    results: list[dict[str, Any]] = []

    # Match non-expired pending games
    pending = db.get_pending()
    for game in pending:
        normalized = _normalise_for_compare(game.game_title)
        matches = db.match_source_title("fitgirl", normalized)

        if matches:
            best = matches[0]
            logger.info(
                "Pending game '{}' matched to '{}' at {}",
                game.game_title, best["title"], best["url"],
            )

            record_result = _record_result(
                db,
                source="metacritic",
                source_title=game.game_title,
                source_url=f"https://www.metacritic.com/game/{game.slug}/",
                game_title=game.game_title,
                platform=game.platform,
                metascore=game.metascore,
                user_score=game.user_score,
                result="Passed",
                result_details=f"Matched source: {best['url']}",
            )
            record_result["slug"] = game.slug
            db.remove_pending(game.slug)
            results.append(record_result)
            logger.info("\u2713 Matched '{}' \u2014 recorded to history", game.game_title)
        else:
            db.touch_pending(game.slug)

    # Expire overdue pending games
    expired = db.get_expired_pending()
    for game in expired:
        record_result = _record_result(
            db,
            source="metacritic",
            source_title=game.game_title,
            source_url=f"https://www.metacritic.com/game/{game.slug}/",
            game_title=game.game_title,
            platform=game.platform,
            metascore=game.metascore,
            user_score=game.user_score,
            result="Expired",
            result_details="Not available on any source within pending window",
        )
        record_result["slug"] = game.slug
        db.remove_pending(game.slug)
        results.append(record_result)
        logger.info("Pending game '{}' expired \u2014 recorded to history", game.game_title)

    return results


def _record_result(
    db: Database,
    *,
    source: str,
    source_title: str,
    source_url: str,
    game_title: str | None,
    platform: str,
    metascore: float | None = None,
    user_score: float | None = None,
    result: str = "Passed",
    result_details: str = "",
    magnet_url: str | None = None,
    torrent_tag: str | None = None,
) -> dict[str, Any]:
    """Record a result in the database and return a result dict."""
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


def _process_entry(
    entry: GameEntry,
    cfg: AcquisitionConfig,
    mc: MetacriticClient,
    qbt: QBittorrentClient,
    db: Database,
    notifier: Notifier,
) -> dict[str, Any]:
    """Process a single game entry through the pipeline."""
    logger.info("Processing entry: '{}'", entry.title)

    mc_result = mc.lookup_game(
        title=entry.title,
        platform=entry.platform,
        cache_ttl_days=cfg.cache_ttl_days,
        browse_cache_ttl_hours=cfg.browse_cache_ttl_hours,
    )

    if mc_result is None:
        return _handle_game_not_found(db, entry)

    _log_game_details(mc_result)

    game_title = mc_result.title
    metascore = mc_result.metascore
    user_score = mc_result.user_score

    score_result = _evaluate_scores(mc_result, cfg)
    if score_result == "Failed":
        return _handle_score_failure(db, notifier, entry, game_title, metascore, user_score)

    return _handle_delivery(db, qbt, notifier, entry, game_title, metascore, user_score)


def _handle_game_not_found(db: Database, entry: GameEntry) -> dict[str, Any]:
    """Record that a game was not found on Metacritic."""
    details = "Game not found on Metacritic"
    return _record_result(
        db,
        source=entry.source,
        source_title=entry.source_title,
        source_url=entry.source_url,
        game_title=entry.title,
        platform=entry.platform,
        result="Failed",
        result_details=details,
    )


def _handle_score_failure(
    db: Database,
    notifier: Notifier,
    entry: GameEntry,
    game_title: str,
    metascore: float | None,
    user_score: float | None,
) -> dict[str, Any]:
    """Record that a game failed score checks."""
    ms = f"{metascore}" if metascore is not None else "TBD"
    us = f"{user_score}" if user_score is not None else "TBD"
    details = f"Metascore {ms}, User score {us} below thresholds"
    notifier.send_failure_notification(title=game_title, reason=details)
    return _record_result(
        db,
        source=entry.source,
        source_title=entry.source_title,
        source_url=entry.source_url,
        game_title=game_title,
        platform=entry.platform,
        metascore=metascore,
        user_score=user_score,
        result="Failed",
        result_details="Score below thresholds",
    )


def _handle_delivery(
    db: Database,
    qbt: QBittorrentClient,
    notifier: Notifier,
    entry: GameEntry,
    game_title: str,
    metascore: float | None,
    user_score: float | None,
) -> dict[str, Any]:
    """Handle magnet delivery to qBittorrent or record failure."""
    magnet_url = entry.magnet_url or ""
    if not magnet_url:
        return _record_result(
            db,
            source=entry.source,
            source_title=entry.source_title,
            source_url=entry.source_url,
            game_title=game_title,
            platform=entry.platform,
            metascore=metascore,
            user_score=user_score,
            result="Failed",
            result_details="No magnet URL available",
        )
    tag = qbt.add_torrent(magnet_url=magnet_url, title=game_title)
    if tag:
        notifier.send_download_notification(
            title=game_title,
            platform=entry.platform,
            metascore=metascore,
            user_score=user_score,
            magnet_url=magnet_url,
        )
        logger.info("✓ Sent '{}' to qBittorrent (tag: {})", game_title, tag)
        result = _record_result(
            db,
            source=entry.source,
            source_title=entry.source_title,
            source_url=entry.source_url,
            game_title=game_title,
            platform=entry.platform,
            metascore=metascore,
            user_score=user_score,
            result="Passed",
            result_details=f"Metascore {metascore}, User score {user_score}",
            magnet_url=magnet_url,
            torrent_tag=str(tag),
        )
        result["torrent_tag"] = str(tag)
        return result
    return _record_result(
        db,
        source=entry.source,
        source_title=entry.source_title,
        source_url=entry.source_url,
        game_title=game_title,
        platform=entry.platform,
        metascore=metascore,
        user_score=user_score,
        result="Error",
        result_details="Failed to add torrent to qBittorrent",
    )
