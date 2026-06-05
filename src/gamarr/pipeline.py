"""Acquisition pipeline for gamarr."""

from __future__ import annotations

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
    ms = _escape_markup(mc_result.metascore) if mc_result.metascore is not None else "TBD"
    ms_r = _escape_markup(mc_result.metascore_review_count) if mc_result.metascore_review_count is not None else "?"
    us = _escape_markup(mc_result.user_score) if mc_result.user_score is not None else "TBD"
    us_r = _escape_markup(mc_result.user_review_count) if mc_result.user_review_count is not None else "?"
    genre = ", ".join(mc_result.genres) if mc_result.genres else "N/A"
    must_play = "<green><bold>Yes</bold></green>" if mc_result.must_play else "<dim>No</dim>"
    release = _escape_markup(mc_result.release_date) if mc_result.release_date else "N/A"
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
