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
    """Return True if value is below threshold (or None).

    When value is None the check passes (no data to fail against).
    """
    return value is not None and value < threshold


def _evaluate_scores(
    mc_result: Any,
    cfg: AcquisitionConfig,
) -> str:
    if mc_result.metascore is None and mc_result.user_score is None:
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

    try:
        entries = source.fetch_new()
        if not entries:
            logger.info("No new entries found.")
            return []

        results: list[dict[str, Any]] = []
        for entry in entries:
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
        source=source, source_title=source_title, source_url=source_url,
        game_title=game_title, platform=platform,
        metascore=metascore, user_score=user_score,
        result=result, result_details=result_details,
        magnet_url=magnet_url, torrent_tag=torrent_tag,
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

    game_title = mc_result.title if mc_result else entry.title
    metascore = mc_result.metascore if mc_result else None
    user_score = mc_result.user_score if mc_result else None

    if mc_result is None:
        return _handle_game_not_found(db, entry, mc_result)

    score_result = _evaluate_scores(mc_result, cfg)
    if score_result == "Failed":
        return _handle_score_failure(db, notifier, entry, game_title, metascore, user_score)

    return _handle_delivery(db, qbt, notifier, entry, game_title, metascore, user_score)


def _handle_game_not_found(db: Database, entry: GameEntry,
                            mc_result: Any) -> dict[str, Any]:
    """Record that a game was not found on Metacritic."""
    details = "Game not found on Metacritic"
    if not entry.magnet_url:
        details += " (no magnet URL)"
    return _record_result(
        db, source=entry.source, source_title=entry.source_title,
        source_url=entry.source_url, game_title=entry.title,
        platform=entry.platform, result="Failed", result_details=details,
    )


def _handle_score_failure(db: Database, notifier: Notifier, entry: GameEntry,
                           game_title: str, metascore: float | None,
                           user_score: float | None) -> dict[str, Any]:
    """Record that a game failed score checks."""
    details = f"Metascore {metascore}, User score {user_score} below thresholds"
    notifier.send_failure_notification(title=game_title, reason=details)
    return _record_result(
        db, source=entry.source, source_title=entry.source_title,
        source_url=entry.source_url, game_title=game_title,
        platform=entry.platform, metascore=metascore, user_score=user_score,
        result="Failed", result_details="Score below thresholds",
    )


def _handle_delivery(db: Database, qbt: QBittorrentClient, notifier: Notifier,
                      entry: GameEntry, game_title: str,
                      metascore: float | None,
                      user_score: float | None) -> dict[str, Any]:
    """Handle magnet delivery to qBittorrent or record failure."""
    magnet_url = entry.magnet_url or ""
    if not magnet_url:
        return _record_result(
            db, source=entry.source, source_title=entry.source_title,
            source_url=entry.source_url, game_title=game_title,
            platform=entry.platform, metascore=metascore, user_score=user_score,
            result="Failed", result_details="No magnet URL available",
        )
    tag = qbt.add_torrent(magnet_url=magnet_url, title=game_title)
    if tag:
        notifier.send_download_notification(
            title=game_title, platform=entry.platform,
            metascore=metascore, user_score=user_score,
            magnet_url=magnet_url,
        )
        logger.info("✓ Sent '{}' to qBittorrent (tag: {})", game_title, tag)
        result = _record_result(
            db, source=entry.source, source_title=entry.source_title,
            source_url=entry.source_url, game_title=game_title,
            platform=entry.platform, metascore=metascore, user_score=user_score,
            result="Passed",
            result_details=f"Metascore {metascore}, User score {user_score}",
            magnet_url=magnet_url, torrent_tag=str(tag),
        )
        result["torrent_tag"] = str(tag)
        return result
    return _record_result(
        db, source=entry.source, source_title=entry.source_title,
        source_url=entry.source_url, game_title=game_title,
        platform=entry.platform, metascore=metascore, user_score=user_score,
        result="Error", result_details="Failed to add torrent to qBittorrent",
    )
