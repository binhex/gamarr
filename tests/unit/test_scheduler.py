"""Tests for gamarr scheduler."""

from __future__ import annotations

from unittest.mock import patch

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
