"""Tests for gamarr scheduler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gamarr.config import Config
from gamarr.scheduler import run, run_once


class TestSchedulerForeground:
    """Single-pass foreground mode."""

    def test_run_once_calls_acquisition(self) -> None:
        with patch("gamarr.scheduler.run_acquisition") as mock_acq:
            mock_acq.return_value = []
            config = _make_config(daemon_mode="foreground")
            run_once(config)
            mock_acq.assert_called_once()

    def test_run_foreground_calls_run_once(self) -> None:
        with patch("gamarr.scheduler.run_once") as mock_run_once:
            config = _make_config(daemon_mode="foreground")
            with patch("gamarr.scheduler.load_config", return_value=config):
                run(config_path="/tmp")
                mock_run_once.assert_called_once()


def _make_config(daemon_mode: str = "foreground") -> Config:
    """Build a minimal Config for testing."""
    from gamarr.config import (
        DatabaseConfig,
        FitGirlSourceConfig,
        GeneralConfig,
        MetacriticConfig,
        MetacriticPlatformConfig,
        NotificationConfig,
        QbittorrentConfig,
        ScheduleConfig,
        ScheduleTaskConfig,
        SourcesConfig,
        TorrentClientConfig,
    )

    return Config(
        general=GeneralConfig(daemon_mode=daemon_mode, log_path="", db_path=":memory:"),
        schedule=ScheduleConfig(
            acquisition=ScheduleTaskConfig(enabled=True, schedule_time_mins=60, run_on_start=True),
        ),
        sources=SourcesConfig(
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

    def test_build_kwargs_includes_notify_on_error(self) -> None:
        from gamarr.config import (
            Config,
            DatabaseConfig,
            FitGirlSourceConfig,
            GeneralConfig,
            MetacriticConfig,
            MetacriticPlatformConfig,
            NotificationConfig,
            QbittorrentConfig,
            ScheduleConfig,
            ScheduleTaskConfig,
            SourcesConfig,
            TorrentClientConfig,
        )
        from gamarr.scheduler import _build_kwargs
        config = Config(
            general=GeneralConfig(daemon_mode="foreground", db_path=":memory:"),
            schedule=ScheduleConfig(
                acquisition=ScheduleTaskConfig(enabled=True, schedule_time_mins=60),
            ),
            sources=SourcesConfig(
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


class TestResolveCachePath:

    def test_cache_path_with_memory(self) -> None:
        from gamarr.scheduler import _resolve_cache_path

        assert _resolve_cache_path(":memory:") == ":memory:"

    def test_cache_path_with_dir(self) -> None:
        from gamarr.scheduler import _resolve_cache_path

        result = _resolve_cache_path("/tmp/db")
        assert result == "/tmp/db/gamarr-cache.db"


class TestDaemonMode:
    """Daemon mode scheduling."""

    def test_run_daemon_creates_scheduler(self) -> None:
        """Mock BackgroundScheduler to test _run_daemon."""
        from gamarr.scheduler import _run_daemon
        from gamarr.config import Config

        with patch("gamarr.scheduler.BackgroundScheduler") as mock_sched_cls:
            mock_sched = MagicMock()
            mock_sched_cls.return_value = mock_sched
            with patch("gamarr.scheduler.signal") as mock_signal:
                mock_shutdown = MagicMock()
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
                config.metacritic.platform_overrides["pc"].browse_cache_ttl_hours = 4
                config.sources.fitgirl.rss_url = "http://example.com/feed"
                config.sources.fitgirl.platform = "pc"
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
                    mock_sched.shutdown.assert_called_once()

    def test_run_daemon_with_run_on_start_false(self) -> None:
        """When run_on_start is False, the first run is delayed."""
        from gamarr.scheduler import _run_daemon
        from gamarr.config import Config

        with patch("gamarr.scheduler.BackgroundScheduler") as mock_sched_cls:
            mock_sched = MagicMock()
            mock_sched_cls.return_value = mock_sched


    def test_run_daemon_with_run_on_start_false(self) -> None:
        """When run_on_start is False, the first run is delayed."""
        from gamarr.scheduler import _run_daemon
        from datetime import datetime, timedelta

        with patch("gamarr.scheduler.BackgroundScheduler") as mock_sched_cls, \
             patch("gamarr.scheduler.signal"):

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
            config.metacritic.platform_overrides["pc"].browse_cache_ttl_hours = 4
            config.sources.fitgirl.rss_url = "http://example.com/feed"
            config.sources.fitgirl.platform = "pc"
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
