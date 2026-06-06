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
    browse_max_pages: int = 200


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
    browse_max_pages: int = 200,
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
        browse_max_pages=browse_max_pages,
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
        if cfg.browse_enabled:
            browse_games = mc.scan_recent_games(
                platform,
                max_pages=cfg.browse_max_pages,
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
        # After the browse step, re-verify every pending game against
        # the real Metacritic detail page.  Browse-page Nuxt data does
        # NOT carry standard 0\u2013100 metascores or 0\u201310 user scores
        # \u2014 the \"score\" fields are internal browse-only metrics.
        # Games whose detail-page scores fail the configured thresholds
        # are removed from pending.
        if db.get_pending():
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
            )
            if removed:
                logger.info(
                    "Removed {} pending games that failed real-score check",
                    removed,
                )

        library: Any = None
        if library_paths:
            from gamarr.library import LibraryScanner

            library = LibraryScanner(library_paths)
        # Only fetch the FitGirl sitemap if there are pending games to
        # match against it. This is the Metacritic-first ordering the
        # user wants: Metacritic browse happens first, and FitGirl is
        # only touched when there is something to look up.
        if db.get_pending():
            source.fetch_sitemap(db)
        matched = _match_pending_games(
            db,
            qbt=qbt,
            magnet_fetcher=_default_magnet_fetcher,
            notifier=notifier,
            library=library,
        )
        if matched:
            logger.info("Matched {} pending games to sources", len(matched))

        return matched

    try:
        # Metacritic-first acquisition: discover games via Metacritic browse,
        # match against the FitGirl sitemap, and only then deliver to qBittorrent.
        # FitGirl RSS entries must NOT drive per-entry Metacritic lookups.
        return _run_discovery_phases(source, mc, db, cfg, platform, qbt, notifier)
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
        logger.debug(
            "Added pending game: '{}' (slug: {}, expires {})",
            title,
            slug,
            expires_at,
        )

    return new_count


def _scores_present(
    result: Any,
) -> bool:
    """Return True if the ScoreResult has at least one valid score (> 0)."""
    return result is not None and (
        (result.metascore is not None and result.metascore > 0.0)
        or (result.user_score is not None and result.user_score > 0.0)
    )


def _check_score_threshold(
    value: Any,
    threshold: Any,
) -> bool | None:
    """Compare a score value against a threshold if the value is meaningful (> 0).

    Returns True/False when the value passes/fails, or None when the
    value is absent or zero (no review data to check).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and value <= 0:
        return None
    return value >= threshold  # type: ignore[no-any-return]


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


def _verify_pending_scores(
    db: Database,
    mc: MetacriticClient,
    platform: str,
    thresholds: dict[str, Any],
    *,
    cache_ttl_days: int = 7,
) -> int:
    """Re-verify every pending game's scores against the real Metacritic detail page.

    Browse-page Nuxt data does NOT carry standard 0\u2013100 metascores or
    0\u201310 user scores — the \"score\" fields are internal browse-only
    metrics.  Games whose detail-page scores fail the configured thresholds
    (or cannot be found at all on the detail page) are removed from pending.

    Games that pass the real-score check have their pending record updated
    with the correct scores from the detail page.

    Returns the number of games removed.
    """
    removed = 0
    for game in db.get_pending(platform=platform):
        result = mc.lookup_game(
            str(game.game_title),
            platform=platform,
            slug=str(game.slug),
            cache_ttl_days=cache_ttl_days,
            direct_only=True,
        )
        if result is None:
            db.remove_pending(str(game.slug))
            removed += 1
            logger.debug(
                "Removed pending '{}' \u2014 not found on Metacritic detail page",
                game.game_title,
            )
            continue

        # Check real scores against thresholds.
        # If the detail page has no valid scores (all None/0), the game
        # hasn't been reviewed yet — remove from pending.
        if not (_scores_present(result) and _real_scores_pass_thresholds(result, thresholds)):
            db.remove_pending(str(game.slug))
            removed += 1
            if _scores_present(result):
                logger.debug(
                    "Removed pending '{}' \u2014 real scores ({}, {}) fail thresholds",
                    game.game_title,
                    result.metascore,
                    result.user_score,
                )
            else:
                logger.debug(
                    "Removed pending '{}' \u2014 no Metacritic scores yet",
                    game.game_title,
                )
            continue

        # Real scores pass \u2014 update pending record with correct values.
        db.update_pending_scores(
            slug=str(game.slug),
            metascore=result.metascore,
            metascore_reviews=result.metascore_review_count,
            user_score=result.user_score,
            user_reviews=result.user_review_count,
        )

    return removed


def _match_pending_games(
    db: Database,
    *,
    qbt: Any = None,
    magnet_fetcher: Callable[[str], str | None] | None = None,
    notifier: Any = None,
    library: Any = None,
) -> list[dict[str, Any]]:
    """Match pending games against torrent source indices.

    For each non-expired pending game:
      1. Normalize its title
      2. Search ``source_titles`` for a match (currently FitGirl only)
      3. On match: skip if already in library, otherwise fetch the magnet
         link and deliver to qBittorrent (and emit notifications)
      4. If no match: update ``last_checked_at``
      5. On expiry: move to history with ``result="Expired"``

    When *qbt* and *magnet_fetcher* are provided, matched games are
    delivered to qBittorrent.  Without them, only a history record
    is written (no download).

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
        game_metascore_reviews: int | None = game.metascore_reviews  # type: ignore[assignment]
        game_user_score: float | None = game.user_score  # type: ignore[assignment]
        game_user_reviews: int | None = game.user_reviews  # type: ignore[assignment]
        game_release_date: str | None = game.release_date  # type: ignore[assignment]

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

            # Check library first — skip if already owned
            if library is not None:
                lib_match = library.check_game(game_title)
                if lib_match is not None:
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
                    results.append(record_result)
                    logger.info(
                        "Pending game '{}' already in library ({}); skipping",
                        game_title,
                        lib_match.matched_path,
                    )
                    continue

            # If qbt and magnet_fetcher are provided, deliver the torrent
            if qbt is not None and magnet_fetcher is not None:
                result_dict = _deliver_match(
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
                    game_release_date=game_release_date,
                )
                results.append(result_dict)
                continue

            # No qbt/magnet_fetcher — record match without delivery
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
        logger.warning("Failed to add matched '{}' to qBittorrent", game_title)
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

    # Log game details (metascore, user score, release date) before
    # the delivery confirmation so the user can see what passed.
    title = _escape_markup(game_title)
    ms = _escape_or(game_metascore, "TBD")
    ms_r = _escape_or(game_metascore_reviews, "?")
    us = _escape_or(game_user_score, "TBD")
    us_r = _escape_or(game_user_reviews, "?")
    release = _escape_or(game_release_date, "N/A")
    sep = " <dim>|</dim> "
    logger.opt(colors=True).info(
        f"<cyan><bold>{title}</bold></cyan>"
        f"{sep}<green>Metascore: <bold>{ms}</bold></green> <dim>({ms_r} reviews)</dim>"
        f"{sep}<yellow>User: <bold>{us}</bold></yellow> <dim>({us_r} reviews)</dim>"
        f"{sep}Released: <dim>{release}</dim>"
    )

    logger.info("\u2713 Sent matched '{}' to qBittorrent (tag: {})", title, tag)
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
    except Exception as exc:  # noqa: BLE001 — notifier failures must not abort the cycle
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
        resp = requests.get(  # noqa: S310 — URL is sourced from the FitGirl sitemap index.
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_SITEMAP_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Magnet fetch failed for {}: {}", url, exc)
        return None
    return _extract_magnet_from_html(resp.text)
