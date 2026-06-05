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
