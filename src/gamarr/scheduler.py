"""APScheduler-based daemon for gamarr."""

from __future__ import annotations

import contextlib
import math
import os
import signal
import threading
from datetime import UTC
from typing import TYPE_CHECKING, Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from gamarr.pipeline import run_acquisition

if TYPE_CHECKING:
    from gamarr.config import Config


def _run_guarded(label: str, fn: Any, *args: Any) -> None:
    """Call fn(*args), logging any exception at ERROR level so one bad cycle cannot crash the scheduler."""
    try:
        fn(*args)
    except Exception:
        logger.exception("{} task failed.", label)


def _write_pid(pid_path: str) -> None:
    """Write the current process PID to a file under *pid_path*."""
    pid_file = pid_path if os.path.splitext(pid_path)[1] else os.path.join(pid_path, "gamarr.pid")
    pid_dir = os.path.dirname(pid_file)
    if pid_dir:
        os.makedirs(pid_dir, exist_ok=True)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    logger.debug("PID {} written to '{}'.", os.getpid(), pid_file)


def _log_next_run_time(scheduler: Any, job_id: str, label: str = "scheduled cycle") -> None:
    """Log the next scheduled run time for a completed job.

    Args:
        scheduler: The APScheduler instance.
        job_id: The job ID to query.
        label: Label for the log message (e.g. "scheduled cycle", "post_processing run").
    """
    from datetime import datetime

    job = scheduler.get_job(job_id)
    if job is None or job.next_run_time is None:
        return
    # Use the job's own timezone so the subtraction is safe:
    # ``datetime.now()`` is naive while ``job.next_run_time`` from
    # APScheduler is always timezone-aware.
    tz = job.next_run_time.tzinfo or UTC
    now = datetime.now(tz)
    remaining = (job.next_run_time - now).total_seconds()
    wait_minutes = max(1, math.ceil(remaining / 60))
    next_str = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    logger.info(
        "Next {} in ~{} minute(s) at {}",
        label,
        wait_minutes,
        next_str,
    )


def _reschedule_acquisition(
    scheduler: Any,
    event: Any,
    interval_mins: int,
) -> None:
    """Reschedule the acquisition job to run *interval_mins* from now.

    Only handles events for the "acquisition" job — other job events
    are ignored.
    """
    if event.job_id != "acquisition":
        return
    from apscheduler.jobstores.base import JobLookupError
    from apscheduler.triggers.interval import IntervalTrigger

    try:
        scheduler.reschedule_job(
            event.job_id,
            trigger=IntervalTrigger(minutes=interval_mins),
        )
    except JobLookupError:
        logger.debug(
            "Acquisition job '{}' not found for rescheduling (expected during shutdown; otherwise the job was removed)",
            event.job_id,
        )
        return

    # Log the next run time AFTER rescheduling, so the message
    # shows the correct wait time (interval from job end).
    _log_next_run_time(scheduler, event.job_id)


def _reschedule_post_processing(
    scheduler: Any,
    event: Any,
    interval_mins: int,
) -> None:
    """Reschedule the post-processing job to run *interval_mins* from now.

    Only handles events for the "post_processing" job — other job events
    are ignored.
    """
    if event.job_id != "post_processing":
        return

    from apscheduler.jobstores.base import JobLookupError
    from apscheduler.triggers.interval import IntervalTrigger

    try:
        scheduler.reschedule_job(
            event.job_id,
            trigger=IntervalTrigger(minutes=interval_mins),
        )
    except JobLookupError:
        logger.debug(
            "Post-processing job '{}' not found for rescheduling (expected during shutdown)",
            event.job_id,
        )
        return

    _log_next_run_time(scheduler, event.job_id, label="post_processing run")


def _cleanup_pid_file(pid_path: str | None) -> None:
    """Remove the PID file at *pid_path* if it exists."""
    if pid_path:
        pid_file = pid_path if os.path.splitext(pid_path)[1] else os.path.join(pid_path, "gamarr.pid")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(pid_file)


def run(config: Config) -> None:
    """Run a scan cycle, either as scheduled daemon or single pass.

    When ``schedule.enabled`` is ``true`` in the config, runs
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
        if config.schedule.enabled:
            _run_daemon(config)
        else:
            run_once(config)
    finally:
        _cleanup_pid_file(pid_path)


def _find_fitgirl_entry(config: Config) -> Any | None:
    """Find the fitgirl download site entry from the ordered config list.

    Args:
        config: The application config.

    Returns:
        The fitgirl source config entry, or None if not found.
    """
    for entry in config.download_sites:
        if entry.name.casefold() == "fitgirl":
            return entry
    return None


def _build_kwargs(config: Config) -> dict[str, Any]:
    """Extract acquisition pipeline kwargs from the config."""
    fitgirl_entry = _find_fitgirl_entry(config)

    mc_cfg = config.review_sites.metacritic.platform_overrides.get(
        fitgirl_entry.platform if fitgirl_entry else "pc",
        config.review_sites.metacritic.platform_overrides["pc"],
    )
    return {
        "platform": fitgirl_entry.platform if fitgirl_entry else "pc",
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
        "cache_details_days": mc_cfg.cache_details_days,
        "cache_pages_hours": mc_cfg.cache_pages_hours,
        "enabled": mc_cfg.enabled,
        "max_pages": mc_cfg.max_pages,
        "max_cycle_pages": mc_cfg.max_cycle_pages,
        "sort_order": mc_cfg.sort_order,
        "search_mode": mc_cfg.search_mode,
        "reject_genre": mc_cfg.reject_genre,
        "reject_title": mc_cfg.reject_title,
        "max_queue_days": mc_cfg.max_queue_days,
        "apprise_urls": config.notification.apprise_urls,
        "notify_on_download": config.notification.on_download,
        "notify_on_failure": config.notification.on_failure,
        "notify_on_error": config.notification.on_error,
        "notify_on_scrape_failure": config.notification.on_scrape_failure,
        "fitgirl_cache_pages_hours": fitgirl_entry.cache_pages_hours if fitgirl_entry else 6,
        "fitgirl_reject_keywords": fitgirl_entry.reject_keywords if fitgirl_entry else [],
        "fitgirl_max_queue_days": fitgirl_entry.max_queue_days if fitgirl_entry else 60,
        "library_paths": config.library.paths,
        "download_sites": list(config.download_sites),
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

    from gamarr.database import Database
    from gamarr.post_processor import run_post_processing
    from gamarr.qbittorrent import QBittorrentClient

    qbt = QBittorrentClient(
        host=kwargs["qbt_host"],
        port=kwargs["qbt_port"],
        username=kwargs["qbt_username"],
        password=kwargs["qbt_password"],
        category=kwargs["qbt_category"],
    )
    db = Database(kwargs["db_path"])
    _run_guarded("Post-processing", run_post_processing, config, qbt, db)


def _run_daemon(config: Config) -> None:
    """Run the scheduler in continuous schedule mode."""
    logger.info("gamarr starting in schedule mode.")
    scheduler = BackgroundScheduler()
    acq_cfg = config.schedule
    kwargs = _build_kwargs(config)

    from datetime import datetime, timedelta

    from apscheduler.triggers.interval import IntervalTrigger

    if acq_cfg.run_on_start:
        _next_run = datetime.now(UTC)
    else:
        _next_run = datetime.now(UTC) + timedelta(minutes=acq_cfg.schedule_time_mins)

    cancel_event = threading.Event()
    scheduler.add_job(
        run_acquisition,
        trigger=IntervalTrigger(minutes=acq_cfg.schedule_time_mins),
        kwargs={**kwargs, "cancel_event": cancel_event},
        id="acquisition",
        name="Acquisition",
        next_run_time=_next_run,
    )

    from gamarr.database import Database
    from gamarr.post_processor import run_post_processing
    from gamarr.qbittorrent import QBittorrentClient

    pp_cfg = config.post_process
    pp_qbt = QBittorrentClient(
        host=kwargs["qbt_host"],
        port=kwargs["qbt_port"],
        username=kwargs["qbt_username"],
        password=kwargs["qbt_password"],
        category=kwargs["qbt_category"],
    )
    pp_db = Database(kwargs["db_path"])
    scheduler.add_job(
        lambda: _run_guarded("Post-processing", run_post_processing, config, pp_qbt, pp_db),
        trigger=IntervalTrigger(minutes=pp_cfg.schedule_time_mins),
        id="post_processing",
        name="Post-processing (copy to library)",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC)
        if pp_cfg.run_on_start
        else datetime.now(UTC) + timedelta(minutes=pp_cfg.schedule_time_mins),
    )

    # Register the listener BEFORE starting the scheduler to avoid missing
    # completion events from a fast first cycle (run_on_start+immediate).
    scheduler.add_listener(
        lambda event: _reschedule_acquisition(scheduler, event, acq_cfg.schedule_time_mins),
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
    )
    scheduler.add_listener(
        lambda event: _reschedule_post_processing(scheduler, event, pp_cfg.schedule_time_mins),
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
    )

    scheduler.start()
    logger.info("Scheduler started (interval={} min)", acq_cfg.schedule_time_mins)

    shutdown_event = _ShutdownEvent(
        cancel_event=cancel_event,
        pid_path=config.general.pid_path or None,
    )
    signal.signal(signal.SIGINT, shutdown_event)
    signal.signal(signal.SIGTERM, shutdown_event)
    shutdown_event.wait()
    logger.info("Shutting down scheduler...")
    scheduler.shutdown(wait=False)


class _ShutdownEvent:
    """Simple event for waiting on shutdown signals."""

    def __init__(
        self,
        cancel_event: threading.Event | None = None,
        pid_path: str | None = None,
    ) -> None:
        self._event = threading.Event()
        self._cancel_event = cancel_event
        self._pid_path = pid_path

    def __call__(self, signum: int, _frame: object) -> None:
        if self._event.is_set():
            # Second signal — process is already shutting down.
            # Force-exit immediately. Clean up PID file first since
            # os._exit() skips finally blocks.
            logger.warning(
                "Received signal {} again — forcing exit (first shutdown still in progress)",
                signum,
            )
            _cleanup_pid_file(self._pid_path)
            os._exit(128 + signum)
        logger.info("Received signal {}; shutting down...", signum)
        self._event.set()
        if self._cancel_event is not None:
            self._cancel_event.set()

    def wait(self) -> None:
        self._event.wait()
