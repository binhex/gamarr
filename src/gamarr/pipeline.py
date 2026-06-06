"""Acquisition pipeline for gamarr."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import requests
from loguru import logger

from gamarr.database import Database
from gamarr.metacritic import MetacriticClient
from gamarr.notifications import Notifier
from gamarr.qbittorrent import QBittorrentClient
from gamarr.sources.fitgirl import _USER_AGENT, FitGirlSource, _extract_magnet_from_html

if TYPE_CHECKING:
    from collections.abc import Callable

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
    """Evaluate a game's scores against thresholds.

    Returns:
        ``"Passed"`` if all checks pass, or a specific failure reason:
        ``"no_scores"``, ``"metascore_too_low"``, ``"metascore_reviews_too_few"``,
        ``"user_score_too_low"``, ``"user_reviews_too_few"``, ``"release_date_too_old"``.
    """
    if mc_result.metascore is None or mc_result.user_score is None:
        return "no_scores"

    if _score_check(mc_result.metascore, cfg.min_metascore):
        return "metascore_too_low"
    if _score_check(mc_result.metascore_review_count, cfg.min_metascore_reviews):
        return "metascore_reviews_too_few"
    if _score_check(mc_result.user_score, cfg.min_user_score):
        return "user_score_too_low"
    if _score_check(mc_result.user_review_count, cfg.min_user_reviews):
        return "user_reviews_too_few"

    if _is_older_than(getattr(mc_result, "release_date", None), cfg.days_since_release):
        return "release_date_too_old"

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

    def _run_discovery_phases(
        source: Any,
        mc: Any,
        db: Database,
        cfg: AcquisitionConfig,
        platform: str,
        qbt: Any,
        notifier: Any,
    ) -> list[dict[str, Any]]:
        """Run sitemap indexing, Metacritic browse, and pending matching.

        Returns combined results from sitemap discovery and pending-game matching.
        """
        source.fetch_sitemap(db)

        # Process sitemap entries directly — each entry gets checked against
        # Metacritic scores and delivered to qBittorrent if it qualifies.
        # This is the primary discovery path; browse-based discovery below
        # is a secondary fallback.
        sitemap_results = _process_sitemap_entries(
            source_name="fitgirl",
            platform=platform,
            library=library,
            mc=mc,
            db=db,
            cfg=cfg,
            qbt=qbt,
            notifier=notifier,
        )

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
                    browse_games,
                    platform,
                    db,
                    thresholds,
                    pending_days=cfg.pending_days,
                    days_since_release=cfg.days_since_release,
                )
                if new_pending:
                    logger.info("Browse added {} new pending games", new_pending)
        matched = _match_pending_games(db)
        if matched:
            logger.info("Matched {} pending games to sources", len(matched))

        return sitemap_results

    try:
        sitemap_results = _run_discovery_phases(source, mc, db, cfg, platform, qbt, notifier)

        entries = source.fetch_new()
        if not entries:
            logger.info("No new entries found.")
            return sitemap_results

        results: list[dict[str, Any]] = list(sitemap_results)
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


def _game_passes_thresholds(game: dict[str, Any], thresholds: dict[str, Any]) -> bool:
    """Check if a browse-page game dict passes all score thresholds."""
    metascore = game.get("score")
    user_score = game.get("user_rating")
    if metascore is None or user_score is None:
        return False
    return all(
        [
            metascore >= thresholds["min_metascore"],
            (game.get("critic_review_count") or 0) >= thresholds["min_metascore_reviews"],
            user_score >= thresholds["min_user_score"],
            (game.get("user_review_count") or 0) >= thresholds["min_user_reviews"],
        ]
    )


def _process_browse_games(
    browse_games: list[dict[str, Any]],
    platform: str,
    db: Database,
    thresholds: dict[str, Any],
    *,
    pending_days: int = 30,
    days_since_release: int = 0,
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

        if not _game_passes_thresholds(game, thresholds):
            continue

        # Skip games older than days_since_release (pre-filter before insert)
        if _is_older_than(game.get("release_date"), days_since_release):
            logger.debug(
                "Skipping '{}' ({}): older than {} days",
                title,
                game.get("release_date"),
                days_since_release,
            )
            continue

        expires_at = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=pending_days)).isoformat()

        db.record_pending(
            slug=slug,
            game_title=title,
            platform=platform,
            metascore=float(game.get("score", 0)),
            metascore_reviews=game.get("critic_review_count"),
            user_score=float(game.get("user_rating", 0)),
            user_reviews=game.get("user_review_count"),
            release_date=game.get("release_date"),
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
        game_title: str = str(game.game_title)
        game_slug: str = str(game.slug)
        game_platform: str = str(game.platform)
        game_metascore: float | None = game.metascore  # type: ignore[assignment]
        game_user_score: float | None = game.user_score  # type: ignore[assignment]

        normalized = _normalise_for_compare(game_title)
        matches = db.match_source_title("fitgirl", normalized)

        if matches:
            best = matches[0]
            logger.info(
                "Pending game '{}' matched to '{}' at {}",
                game_title,
                best["title"],
                best["url"],
            )

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
            results.append(record_result)
            logger.info("\u2713 Matched '{}' \u2014 recorded to history", game_title)
        else:
            db.touch_pending(game_slug)

    # Expire overdue pending games
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
            metascore=game.metascore,  # type: ignore[arg-type]
            user_score=game.user_score,  # type: ignore[arg-type]
            result="Expired",
            result_details="Not available on any source within pending window",
        )
        record_result["slug"] = game_slug
        db.remove_pending(game_slug)
        results.append(record_result)
        logger.info("Pending game '{}' expired \u2014 recorded to history", game_title)

    return results


_SITEMAP_TIMEOUT = 30.0


def _default_magnet_fetcher(url: str) -> str | None:
    """Fetch a FitGirl page and extract its magnet link."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_SITEMAP_TIMEOUT,
        )
        resp.raise_for_status()
        return _extract_magnet_from_html(resp.text)
    except Exception as exc:
        logger.warning("Failed to fetch magnet from '{}': {}", url, exc)
        return None


def _process_one_sitemap_entry(
    *,
    source_name: str,
    entry_title: str,
    entry_url: str,
    platform: str,
    library: Any,
    mc: MetacriticClient,
    db: Database,
    cfg: AcquisitionConfig,
    qbt: QBittorrentClient,
    notifier: Notifier,
    magnet_fetcher: Callable[[str], str | None],
) -> dict[str, Any]:
    """Look up a single sitemap entry on Metacritic, evaluate, and deliver.

    Returns a result dict with keys ``result``, ``game_title``, etc.
    """
    logger.info("Sitemap discovery: checking '{}'", entry_title)

    # Check library first — skip if already owned
    if library is not None:
        match = library.check_game(entry_title)
        if match:
            db.record_processed(
                source=source_name,
                source_title=entry_title,
                source_url=entry_url,
                game_title=entry_title,
                platform=platform,
                result="Already owned",
                result_details=f"Found in library: {match.matched_path}",
            )
            logger.info("Already in library, skipping: '{}'", entry_title)
            return {
                "result": "Already owned",
                "game_title": entry_title,
                "result_details": f"Found in library: {match.matched_path}",
            }

    mc_result = mc.lookup_game(
        title=entry_title,
        platform=platform,
        cache_ttl_days=cfg.cache_ttl_days,
        browse_cache_ttl_hours=cfg.browse_cache_ttl_hours,
    )

    if mc_result is None:
        db.record_processed(
            source=source_name,
            source_title=entry_title,
            source_url=entry_url,
            game_title=entry_title,
            platform=platform,
            result="Failed",
            result_details="Game not found on Metacritic",
        )
        return {"result": "Failed", "game_title": entry_title, "result_details": "Game not found on Metacritic"}

    _log_game_details(mc_result)

    score_result = _evaluate_scores(mc_result, cfg)
    if score_result != "Passed":
        logger.warning(
            "Sitemap '{}' failed check '{}': Metascore {}, User {}, Released {}",
            mc_result.title,
            score_result,
            mc_result.metascore,
            mc_result.user_score,
            getattr(mc_result, "release_date", "unknown"),
        )
        notifier.send_failure_notification(
            title=mc_result.title,
            reason=f"{score_result}: Metascore {mc_result.metascore}, User {mc_result.user_score}",
        )
        db.record_processed(
            source=source_name,
            source_title=entry_title,
            source_url=entry_url,
            game_title=mc_result.title,
            platform=platform,
            metascore=mc_result.metascore,
            user_score=mc_result.user_score,
            result="Failed",
            result_details=f"Failed: {score_result}",
        )
        return {"result": "Failed", "game_title": mc_result.title, "result_details": f"Failed: {score_result}"}

    magnet = magnet_fetcher(entry_url)
    if not magnet:
        db.record_processed(
            source=source_name,
            source_title=entry_title,
            source_url=entry_url,
            game_title=mc_result.title,
            platform=platform,
            metascore=mc_result.metascore,
            user_score=mc_result.user_score,
            result="Failed",
            result_details="No magnet URL available from source page",
        )
        return {"result": "Failed", "game_title": mc_result.title, "result_details": "No magnet URL available"}

    tag = qbt.add_torrent(magnet_url=magnet, title=mc_result.title)
    if not tag:
        db.record_processed(
            source=source_name,
            source_title=entry_title,
            source_url=entry_url,
            game_title=mc_result.title,
            platform=platform,
            metascore=mc_result.metascore,
            user_score=mc_result.user_score,
            result="Error",
            result_details="Failed to add torrent to qBittorrent",
        )
        return {"result": "Error", "game_title": mc_result.title, "result_details": "Failed to add torrent"}

    # Record success in DB BEFORE sending the notification, so that even if
    # the notification raises, the entry is recorded and won't be duplicated.
    result = {
        "result": "Passed",
        "game_title": mc_result.title,
        "metascore": mc_result.metascore,
        "user_score": mc_result.user_score,
        "result_details": f"Metascore {mc_result.metascore}, User score {mc_result.user_score}",
        "torrent_tag": str(tag),
    }
    db.record_processed(
        source=source_name,
        source_title=entry_title,
        source_url=entry_url,
        game_title=mc_result.title,
        platform=platform,
        metascore=mc_result.metascore,
        user_score=mc_result.user_score,
        result="Passed",
        result_details=f"Metascore {mc_result.metascore}, User score {mc_result.user_score}",
        magnet_url=magnet,
        torrent_tag=str(tag),
    )
    logger.info("\u2713 Sent '{}' to qBittorrent (tag: {})", mc_result.title, tag)
    notifier.send_download_notification(
        title=mc_result.title,
        platform=platform,
        metascore=mc_result.metascore,
        user_score=mc_result.user_score,
        magnet_url=magnet,
    )
    return result


def _process_sitemap_entries(
    source_name: str,
    platform: str = "pc",
    library: Any = None,
    mc: MetacriticClient = None,  # type: ignore[assignment]
    db: Database = None,  # type: ignore[assignment]
    cfg: AcquisitionConfig = None,  # type: ignore[assignment]
    qbt: QBittorrentClient = None,  # type: ignore[assignment]
    notifier: Notifier = None,  # type: ignore[assignment]
    *,
    max_entries: int = 50,
    magnet_fetcher: Callable[[str], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Iterate sitemap entries and process qualifying games through the pipeline.

    For each unprocessed sitemap entry:
    1. Look up the game on Metacritic
    2. Evaluate scores (respecting ``days_since_release``)
    3. If qualifying, fetch the magnet link and send to qBittorrent

    This replaces the ineffective browse-first discovery which was broken
    because Metacritic browse pages 1-10 show mostly unreviewed indie games
    (pages 1-10 of \"newest first\" have ~2 games with scores out of ~240).

    Args:
        source_name: Source identifier (e.g. "fitgirl").
        mc: Metacritic client for score lookups.
        db: Database instance.
        cfg: Acquisition configuration (thresholds + days_since_release).
        qbt: qBittorrent client for torrent delivery.
        notifier: Notification dispatcher.
        max_entries: Max sitemap entries to process per run.
        magnet_fetcher: Callable that extracts magnet from a source URL.
            Defaults to fetching the page and searching for magnet links.

    Returns:
        List of result dicts for qualifying entries.
    """
    if magnet_fetcher is None:
        magnet_fetcher = _default_magnet_fetcher

    entries = db.get_all_source_titles(source_name)
    if not entries:
        return []

    results: list[dict[str, Any]] = []
    processed = 0
    passed_count = 0

    for entry in entries:
        if processed >= max_entries:
            break

        source_url: str = entry["url"]
        title: str = entry["title"]

        if db.is_processed(source_name, source_url):
            logger.debug("Sitemap entry already processed: '{}'", title)
            continue

        processed += 1
        try:
            result = _process_one_sitemap_entry(
                source_name=source_name,
                entry_title=title,
                entry_url=source_url,
                platform=platform,
                library=library,
                mc=mc,
                db=db,
                cfg=cfg,
                qbt=qbt,
                notifier=notifier,
                magnet_fetcher=magnet_fetcher,
            )
        except Exception as exc:
            logger.error("Sitemap discovery: error processing '{}': {}", title, exc)
            result = {"result": "Error", "game_title": title, "result_details": str(exc)}
        results.append(result)
        if result["result"] == "Passed":
            passed_count += 1

    logger.info("Sitemap discovery: {} processed, {} passed", processed, passed_count)
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
    if score_result != "Passed":
        return _handle_score_failure(db, notifier, entry, game_title, metascore, user_score, score_result)

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
    score_result: str = "Score below thresholds",
) -> dict[str, Any]:
    """Record that a game failed score/release-date checks."""
    ms = f"{metascore}" if metascore is not None else "TBD"
    us = f"{user_score}" if user_score is not None else "TBD"
    details = f"{score_result}: Metascore {ms}, User {us}"
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
        result_details=f"Failed: {score_result}",
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
