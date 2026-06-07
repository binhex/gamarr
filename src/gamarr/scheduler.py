"""APScheduler-based daemon for gamarr."""

from __future__ import annotations

import signal
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from gamarr.config import Config, load_config
from gamarr.pipeline import run_acquisition


def run(config_path: str = "configs", daemon_mode: str | None = None) -> None:
    """Start the acquisition scheduler in daemon or foreground mode.

    Args:
        config_path: Path to the config directory or file.
        daemon_mode: Override the daemon mode from config. Pass "background"
            to run in daemon mode without modifying the config file on disk.
    """
    config = load_config(config_path)

    if daemon_mode:
        config.general.daemon_mode = daemon_mode

    if config.general.daemon_mode == "background":
        _run_daemon(config)
    else:
        run_once(config)


def _build_kwargs(config: Config) -> dict[str, Any]:
    """Extract acquisition pipeline kwargs from the config."""
    mc_cfg = config.metacritic.platform_overrides.get(
        config.sources.fitgirl.platform,
        config.metacritic.platform_overrides["pc"],
    )
    return {
        "fitgirl_rss_url": config.sources.fitgirl.rss_url,
        "platform": config.sources.fitgirl.platform,
        "db_path": config.general.db_path,
        "qbt_host": config.torrent_client.qbittorrent.host,
        "qbt_port": config.torrent_client.qbittorrent.port,
        "qbt_username": config.torrent_client.qbittorrent.username,
        "qbt_password": config.torrent_client.qbittorrent.password,
        "qbt_category": config.torrent_client.qbittorrent.category,
        "qbt_add_paused": config.torrent_client.qbittorrent.add_paused,
        "min_metascore": mc_cfg.min_metascore,
        "min_metascore_reviews": mc_cfg.min_metascore_reviews,
        "min_user_score": mc_cfg.min_user_score,
        "min_user_reviews": mc_cfg.min_user_reviews,
        "days_since_release": mc_cfg.days_since_release,
        "cache_ttl_days": mc_cfg.cache_ttl_days,
        "cache_ttl_hours": mc_cfg.cache_ttl_hours,
        "enabled": mc_cfg.enabled,
        "max_games": mc_cfg.max_games,
        "max_score_checks": mc_cfg.max_score_checks,
        "cutoff_date": mc_cfg.cutoff_date,
        "exclude_keywords": mc_cfg.exclude_keywords,
        "pending_days": mc_cfg.pending_days,
        "apprise_urls": config.notification.apprise_urls,
        "notify_on_download": config.notification.on_download,
        "notify_on_failure": config.notification.on_failure,
        "notify_on_error": config.notification.on_error,
        "fitgirl_cache_ttl_hours": config.sources.fitgirl.cache_ttl_hours,
        "fitgirl_exclude_keywords": config.sources.fitgirl.exclude_keywords,
        "library_paths": config.library.paths,
    }


def run_once(config: Config) -> None:
    """Run a single acquisition cycle (foreground mode)."""
    logger.info("gamarr running in single-pass mode.")
    kwargs = _build_kwargs(config)
    results = run_acquisition(**kwargs)
    passed = sum(1 for r in results if r["result"] == "Passed")
    failed = sum(1 for r in results if r["result"] == "Failed")
    errors = sum(1 for r in results if r["result"] == "Error")
    logger.info("Acquisition complete: {} passed, {} failed, {} errors", passed, failed, errors)


def _run_daemon(config: Config) -> None:
    """Run the scheduler in continuous daemon mode."""
    logger.info("gamarr starting in daemon mode.")
    scheduler = BackgroundScheduler()
    acq_cfg = config.schedule.acquisition
    kwargs = _build_kwargs(config)

    from datetime import datetime, timedelta

    from apscheduler.triggers.interval import IntervalTrigger

    if acq_cfg.run_on_start:
        _next_run = datetime.now()
    else:
        _next_run = datetime.now() + timedelta(minutes=acq_cfg.schedule_time_mins)

    scheduler.add_job(
        run_acquisition,
        trigger=IntervalTrigger(minutes=acq_cfg.schedule_time_mins),
        kwargs=kwargs,
        id="acquisition",
        name="Acquisition",
        next_run_time=_next_run,
    )

    scheduler.start()
    logger.info("Scheduler started (interval={} min)", acq_cfg.schedule_time_mins)

    shutdown_event = _ShutdownEvent()
    signal.signal(signal.SIGINT, shutdown_event)
    signal.signal(signal.SIGTERM, shutdown_event)
    shutdown_event.wait()
    logger.info("Shutting down scheduler...")
    scheduler.shutdown(wait=False)


class _ShutdownEvent:
    """Simple event for waiting on shutdown signals."""

    def __init__(self) -> None:
        import threading

        self._event = threading.Event()

    def __call__(self, signum: int, _frame: object) -> None:
        logger.info("Received signal {}; shutting down...", signum)
        self._event.set()

    def wait(self) -> None:
        self._event.wait()
