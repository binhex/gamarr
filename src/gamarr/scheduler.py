"""APScheduler-based daemon for gamarr."""

from __future__ import annotations

import contextlib
import math
import os
import signal
import threading
from typing import TYPE_CHECKING, Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from gamarr.pipeline import run_acquisition

if TYPE_CHECKING:
    from gamarr.config import Config


def _write_pid(pid_path: str) -> None:
    """Write the current process PID to a file under *pid_path*."""
    pid_file = pid_path if os.path.splitext(pid_path)[1] else os.path.join(pid_path, "gamarr.pid")
    pid_dir = os.path.dirname(pid_file)
    if pid_dir:
        os.makedirs(pid_dir, exist_ok=True)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    logger.debug("PID {} written to '{}'.", os.getpid(), pid_file)


def _log_next_run_time(scheduler: Any, job_id: str) -> None:
    """Log the next scheduled run time for a completed job.

    Reads the job's ``next_run_time`` from APScheduler and emits an
    info-level message with the approximate wait duration (in minutes)
    and the absolute date/time of the next run.
    """
    from datetime import datetime, timezone

    job = scheduler.get_job(job_id)
    if job is None or job.next_run_time is None:
        return
    # Use the job's own timezone so the subtraction is safe:
    # ``datetime.now()`` is naive while ``job.next_run_time`` from
    # APScheduler is always timezone-aware.
    tz = job.next_run_time.tzinfo or timezone.utc
    now = datetime.now(tz)
    remaining = (job.next_run_time - now).total_seconds()
    wait_minutes = max(1, math.ceil(remaining / 60))
    next_str = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    logger.info(
        "Next acquisition cycle in ~{} minute(s) at {}",
        wait_minutes,
        next_str,
    )


def _cleanup_pid_file(pid_path: str | None) -> None:
    """Remove the PID file at *pid_path* if it exists."""
    if pid_path:
        pid_file = pid_path if os.path.splitext(pid_path)[1] else os.path.join(pid_path, "gamarr.pid")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(pid_file)


def run(config: Config) -> None:
    """Run a scan cycle, either as scheduled daemon or single pass.

    When ``schedule.acquisition.enabled`` is ``true`` in the config, runs
    in continuous scheduled mode using APScheduler. Otherwise runs a
    single scan pass and exits.

    The PID file is written before starting and cleaned up in a
    ``finally`` block.

    Args:
        config: Application configuration (may have CLI overrides applied).
    """
    pid_path = config.general.pid_path or None

    try:
        if pid_path:
            _write_pid(pid_path)
        if config.schedule.acquisition.enabled:
            _run_daemon(config)
        else:
            run_once(config)
    finally:
        _cleanup_pid_file(pid_path)


def _build_kwargs(config: Config) -> dict[str, Any]:
    """Extract acquisition pipeline kwargs from the config."""
    mc_cfg = config.metacritic.platform_overrides.get(
        config.download_sites.fitgirl.platform,
        config.metacritic.platform_overrides["pc"],
    )
    return {
        "fitgirl_rss_url": config.download_sites.fitgirl.rss_url,
        "platform": config.download_sites.fitgirl.platform,
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
        "cache_ttl_days": mc_cfg.cache_ttl_days,
        "cache_ttl_hours": mc_cfg.cache_ttl_hours,
        "enabled": mc_cfg.enabled,
        "max_games": mc_cfg.max_games,
        "cutoff_weeks": mc_cfg.cutoff_weeks,
        "reject_genre": mc_cfg.reject_genre,
        "reject_title": mc_cfg.reject_title,
        "recheck_days": mc_cfg.recheck_days,
        "apprise_urls": config.notification.apprise_urls,
        "notify_on_download": config.notification.on_download,
        "notify_on_failure": config.notification.on_failure,
        "notify_on_error": config.notification.on_error,
        "notify_on_scrape_failure": config.notification.on_scrape_failure,
        "fitgirl_cache_ttl_hours": config.download_sites.fitgirl.cache_ttl_hours,
        "fitgirl_reject_keywords": config.download_sites.fitgirl.reject_keywords,
        "fitgirl_recheck_days": config.download_sites.fitgirl.recheck_days,
        "library_paths": config.library.paths,
    }


def run_once(config: Config) -> None:
    """Run a single scan cycle (foreground mode)."""
    logger.info("gamarr running in single-pass mode.")
    kwargs = _build_kwargs(config)
    results = run_acquisition(**kwargs)
    passed = sum(1 for r in results if r["result"] == "Passed")
    failed = sum(1 for r in results if r["result"] == "Failed")
    errors = sum(1 for r in results if r["result"] == "Error")
    logger.info("Acquisition complete: {} passed, {} failed, {} errors", passed, failed, errors)


def _run_daemon(config: Config) -> None:
    """Run the scheduler in continuous schedule mode."""
    logger.info("gamarr starting in schedule mode.")
    scheduler = BackgroundScheduler()
    acq_cfg = config.schedule.acquisition
    kwargs = _build_kwargs(config)

    from datetime import datetime, timedelta

    from apscheduler.triggers.interval import IntervalTrigger

    if acq_cfg.run_on_start:
        _next_run = datetime.now()
    else:
        _next_run = datetime.now() + timedelta(minutes=acq_cfg.schedule_time_mins)

    cancel_event = threading.Event()
    scheduler.add_job(
        run_acquisition,
        trigger=IntervalTrigger(minutes=acq_cfg.schedule_time_mins),
        kwargs={**kwargs, "cancel_event": cancel_event},
        id="acquisition",
        name="Acquisition",
        next_run_time=_next_run,
    )

    scheduler.start()
    logger.info("Scheduler started (interval={} min)", acq_cfg.schedule_time_mins)

    # Log the next run time after each cycle completes so users know
    # when to expect the next acquisition.
    scheduler.add_listener(
        lambda event: _log_next_run_time(scheduler, event.job_id),
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
    )

    shutdown_event = _ShutdownEvent(cancel_event=cancel_event)
    signal.signal(signal.SIGINT, shutdown_event)
    signal.signal(signal.SIGTERM, shutdown_event)
    shutdown_event.wait()
    logger.info("Shutting down scheduler...")
    scheduler.shutdown(wait=True)


class _ShutdownEvent:
    """Simple event for waiting on shutdown signals."""

    def __init__(self, cancel_event: threading.Event | None = None) -> None:
        self._event = threading.Event()
        self._cancel_event = cancel_event

    def __call__(self, signum: int, _frame: object) -> None:
        logger.info("Received signal {}; shutting down...", signum)
        self._event.set()
        if self._cancel_event is not None:
            self._cancel_event.set()

    def wait(self) -> None:
        self._event.wait()
