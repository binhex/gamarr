"""Tests for gamarr scheduler."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from gamarr.config import Config
from gamarr.scheduler import run, run_once

if TYPE_CHECKING:
    from pathlib import Path


class TestSchedulerForeground:
    """Single-pass foreground mode."""

    def test_run_once_calls_acquisition(self) -> None:
        with patch("gamarr.scheduler.run_acquisition") as mock_acq:
            mock_acq.return_value = []
            config = _make_config(acquisition_enabled=False)
            run_once(config)
            mock_acq.assert_called_once()

    def test_run_foreground_calls_run_once(self) -> None:
        with patch("gamarr.scheduler.run_once") as mock_run_once:
            config = _make_config(acquisition_enabled=False)
            run(config)
            mock_run_once.assert_called_once()

    def test_run_calls_daemon_when_schedule_enabled(self) -> None:
        """When schedule.acquisition.enabled=True, run() should call _run_daemon."""
        config = _make_config(acquisition_enabled=True)
        with (
            patch("gamarr.scheduler._run_daemon") as mock_daemon,
            patch("gamarr.scheduler.run_once") as mock_once,
        ):
            run(config)
            mock_daemon.assert_called_once()
            mock_once.assert_not_called()

    def test_run_calls_run_once_when_schedule_disabled(self) -> None:
        """When schedule.acquisition.enabled=False, run() should call run_once."""
        config = _make_config(acquisition_enabled=False)
        with (
            patch("gamarr.scheduler._run_daemon") as mock_daemon,
            patch("gamarr.scheduler.run_once") as mock_once,
        ):
            run(config)
            mock_once.assert_called_once()
            mock_daemon.assert_not_called()


def _make_config(acquisition_enabled: bool = False) -> Config:
    """Build a minimal Config for testing with *acquisition_enabled*."""
    from gamarr.config import (
        DatabaseConfig,
        DownloadSitesConfig,
        FitGirlSourceConfig,
        GeneralConfig,
        MetacriticConfig,
        MetacriticPlatformConfig,
        NotificationConfig,
        QbittorrentConfig,
        ScheduleConfig,
        ScheduleTaskConfig,
        TorrentClientConfig,
    )

    return Config(
        general=GeneralConfig(log_path="", db_path=":memory:", pid_path=""),
        schedule=ScheduleConfig(
            acquisition=ScheduleTaskConfig(enabled=acquisition_enabled, schedule_time_mins=60, run_on_start=True),
        ),
        download_sites=DownloadSitesConfig(
            fitgirl=FitGirlSourceConfig(enabled=True, rss_url="http://example.com/feed", platform="pc"),
        ),
        metacritic=MetacriticConfig(
            platform_overrides={"pc": MetacriticPlatformConfig()},
        ),
        torrent_client=TorrentClientConfig(
            qbittorrent=QbittorrentConfig(host="localhost", port=8080),
        ),
        notification=NotificationConfig(apprise_urls=[]),
        database=DatabaseConfig(processed_expiry_days=365),
    )


class TestShutdownEvent:
    """_ShutdownEvent construction and behavior."""

    def test_shutdown_event_constructs(self) -> None:
        from gamarr.scheduler import _ShutdownEvent

        evt = _ShutdownEvent()
        assert evt is not None

    def test_shutdown_event_call(self) -> None:
        from gamarr.scheduler import _ShutdownEvent

        evt = _ShutdownEvent()
        evt(15, None)  # SIGTERM
        # After being called, the event is set
        assert evt._event.is_set()


class TestBuildKwargs:
    """_build_kwargs config extraction."""

    def test_build_kwargs_includes_fitgirl_reject_keywords(self) -> None:
        """_build_kwargs should include fitgirl_reject_keywords, not fitgirl_exclude_keywords."""
        from gamarr.scheduler import _build_kwargs

        config = _make_config()
        kwargs = _build_kwargs(config)
        assert "fitgirl_reject_keywords" in kwargs
        assert "fitgirl_exclude_keywords" not in kwargs
        assert "exclude_keywords" not in kwargs

    def test_build_kwargs_includes_notify_on_error(self) -> None:
        from gamarr.config import (
            Config,
            DatabaseConfig,
            DownloadSitesConfig,
            FitGirlSourceConfig,
            GeneralConfig,
            MetacriticConfig,
            MetacriticPlatformConfig,
            NotificationConfig,
            QbittorrentConfig,
            ScheduleConfig,
            ScheduleTaskConfig,
            TorrentClientConfig,
        )
        from gamarr.scheduler import _build_kwargs

        config = Config(
            general=GeneralConfig(daemon_mode="foreground", db_path=":memory:"),
            schedule=ScheduleConfig(
                acquisition=ScheduleTaskConfig(enabled=True, schedule_time_mins=60),
            ),
            download_sites=DownloadSitesConfig(
                fitgirl=FitGirlSourceConfig(enabled=True, rss_url="http://example.com/feed"),
            ),
            metacritic=MetacriticConfig(
                platform_overrides={"pc": MetacriticPlatformConfig()},
            ),
            torrent_client=TorrentClientConfig(
                qbittorrent=QbittorrentConfig(host="localhost", port=8080),
            ),
            notification=NotificationConfig(apprise_urls=[], on_error=True),
            database=DatabaseConfig(),
        )
        kwargs = _build_kwargs(config)
        assert kwargs["notify_on_error"] is True
        assert kwargs["notify_on_download"] is True
        assert kwargs["notify_on_failure"] is False


class TestDaemonMode:
    """Daemon mode scheduling."""

    def test_run_daemon_creates_scheduler(self) -> None:
        """Mock BackgroundScheduler to test _run_daemon."""
        from gamarr.scheduler import _run_daemon

        with patch("gamarr.scheduler.BackgroundScheduler") as mock_sched_cls:
            mock_sched = MagicMock()
            mock_sched_cls.return_value = mock_sched
            with patch("gamarr.scheduler.signal") as mock_signal:
                mock_shutdown = MagicMock()  # noqa: F841
                mock_signal.signal.return_value = None

                config = MagicMock()
                config.schedule.acquisition.schedule_time_mins = 60
                config.schedule.acquisition.run_on_start = True
                config.metacritic.platform_overrides = {"pc": MagicMock()}
                config.metacritic.platform_overrides["pc"].min_metascore = 75
                config.metacritic.platform_overrides["pc"].min_metascore_reviews = 5
                config.metacritic.platform_overrides["pc"].min_user_score = 7.5
                config.metacritic.platform_overrides["pc"].min_user_reviews = 10
                config.metacritic.platform_overrides["pc"].days_since_release = 90
                config.metacritic.platform_overrides["pc"].cache_ttl_days = 7
                config.metacritic.platform_overrides["pc"].cache_ttl_hours = 4
                config.download_sites.fitgirl.rss_url = "http://example.com/feed"
                config.download_sites.fitgirl.platform = "pc"
                config.general.db_path = ":memory:"
                config.torrent_client.qbittorrent.host = "localhost"
                config.torrent_client.qbittorrent.port = 8080
                config.torrent_client.qbittorrent.username = "admin"
                config.torrent_client.qbittorrent.password = "adminadmin"
                config.torrent_client.qbittorrent.category = "games-gamarr"
                config.torrent_client.qbittorrent.add_paused = False
                config.notification.apprise_urls = []
                config.notification.on_download = True
                config.notification.on_failure = False
                config.notification.on_error = False

                # Interrupt the wait() to prevent infinite loop
                mock_shutdown_event = MagicMock()
                mock_shutdown_event.wait.return_value = None

                with patch("gamarr.scheduler._ShutdownEvent", return_value=mock_shutdown_event):
                    _run_daemon(config)
                    mock_sched.add_job.assert_called_once()
                    mock_sched.start.assert_called_once()
                    mock_signal.signal.assert_any_call(mock_signal.SIGINT, mock_shutdown_event)
                    mock_sched.shutdown.assert_called_once_with(wait=True)
            mock_sched_cls.return_value = mock_sched

    def test_run_daemon_with_run_on_start_false(self) -> None:
        """When run_on_start is False, the first run is delayed."""

        from gamarr.scheduler import _run_daemon

        with patch("gamarr.scheduler.BackgroundScheduler") as mock_sched_cls, patch("gamarr.scheduler.signal"):
            mock_sched = MagicMock()
            mock_sched_cls.return_value = mock_sched

            config = MagicMock()
            config.schedule.acquisition.schedule_time_mins = 60
            config.schedule.acquisition.run_on_start = False
            config.metacritic.platform_overrides = {"pc": MagicMock()}
            config.metacritic.platform_overrides["pc"].min_metascore = 75
            config.metacritic.platform_overrides["pc"].min_metascore_reviews = 5
            config.metacritic.platform_overrides["pc"].min_user_score = 7.5
            config.metacritic.platform_overrides["pc"].min_user_reviews = 10
            config.metacritic.platform_overrides["pc"].days_since_release = 90
            config.metacritic.platform_overrides["pc"].cache_ttl_days = 7
            config.metacritic.platform_overrides["pc"].cache_ttl_hours = 4
            config.download_sites.fitgirl.rss_url = "http://example.com/feed"
            config.download_sites.fitgirl.platform = "pc"
            config.general.db_path = ":memory:"
            config.torrent_client.qbittorrent.host = "localhost"
            config.torrent_client.qbittorrent.port = 8080
            config.torrent_client.qbittorrent.username = "admin"
            config.torrent_client.qbittorrent.password = "adminadmin"
            config.torrent_client.qbittorrent.category = "games-gamarr"
            config.torrent_client.qbittorrent.add_paused = False
            config.notification.apprise_urls = []
            config.notification.on_download = True
            config.notification.on_failure = False
            config.notification.on_error = False

            mock_shutdown_event = MagicMock()
            mock_shutdown_event.wait.return_value = None

            with patch("gamarr.scheduler._ShutdownEvent", return_value=mock_shutdown_event):
                _run_daemon(config)
                mock_sched.add_job.assert_called_once()
                mock_sched.start.assert_called_once()


class TestPidFile:
    """PID file write/cleanup tests."""

    def test_write_pid_creates_file(self, tmp_path: Path) -> None:
        from gamarr.scheduler import _write_pid

        pid_dir = tmp_path / "pids"
        pid_dir.mkdir()
        _write_pid(str(pid_dir))
        pid_file = pid_dir / "gamarr.pid"
        assert pid_file.exists()
        content = pid_file.read_text().strip()
        assert content == str(os.getpid())

    def test_write_pid_full_path(self, tmp_path: Path) -> None:
        from gamarr.scheduler import _write_pid

        pid_file = tmp_path / "custom.pid"
        _write_pid(str(pid_file))
        assert pid_file.exists()
        assert pid_file.read_text().strip() == str(os.getpid())

    def test_cleanup_pid_file_removes(self, tmp_path: Path) -> None:
        from gamarr.scheduler import _cleanup_pid_file

        pid_file = tmp_path / "gamarr.pid"
        pid_file.write_text("12345")
        assert pid_file.exists()
        _cleanup_pid_file(str(tmp_path))
        assert not pid_file.exists()

    def test_cleanup_pid_file_nonexistent(self, tmp_path: Path) -> None:
        """Should not raise when PID file doesn't exist."""
        from gamarr.scheduler import _cleanup_pid_file

        _cleanup_pid_file(str(tmp_path / "nonexistent.pid"))  # should not raise

    def test_cleanup_pid_file_none(self) -> None:
        """Should not raise when pid_path is None."""
        from gamarr.scheduler import _cleanup_pid_file

        _cleanup_pid_file(None)  # should not raise


class TestCancelEvent:
    """Cancel event wiring in _run_daemon and _ShutdownEvent."""

    def test_shutdown_event_sets_cancel_event(self) -> None:
        """When _ShutdownEvent.__call__ is invoked, it should set the
        associated cancel_event."""
        import threading

        from gamarr.scheduler import _ShutdownEvent

        cancel_event = threading.Event()
        evt = _ShutdownEvent(cancel_event=cancel_event)
        # The cancel_event should NOT be set yet
        assert not cancel_event.is_set()

        # Simulate a signal
        evt(15, None)

        # After the signal fires, the cancel_event should ALSO be set
        assert cancel_event.is_set()
