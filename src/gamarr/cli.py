"""Command-line interface for gamarr."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

import click

from gamarr.logger import create_logger
from gamarr.utils import get_project_root


def _resolve_version() -> str:
    """Return the installed package version, or 'unknown' if not installed."""
    try:
        return _pkg_version("gamarr")
    except PackageNotFoundError:
        return "unknown"


_VERSION = _resolve_version()
_PROJECT_ROOT = get_project_root()
_DEFAULT_LOGS_PATH = f"{_PROJECT_ROOT}/logs/gamarr.log"


@click.command()
@click.option(
    "--config-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default="configs",
    show_default=True,
    metavar="<dir>",
    help="Directory containing gamarr.yml configuration file.",
)
@click.option(
    "--log-level",
    default=None,
    type=click.Choice(["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"], case_sensitive=False),
    show_default=False,
    metavar="<level>",
    help="Override the console log level (default from config).",
)
@click.option(
    "--log-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<path>",
    help="Override the log file path from config.",
)
@click.option(
    "--daemon",
    is_flag=True,
    default=False,
    help="Run in continuous scheduling mode (use systemd or Docker for daemonization).",
)
@click.option(
    "--test",
    is_flag=True,
    default=False,
    help="Validate configuration and exit without running any tasks.",
)
@click.version_option(version=_VERSION, prog_name="gamarr")
def cli(
    config_path: str,
    log_level: str | None,
    log_path: str | None,
    daemon: bool,
    test: bool,
) -> None:
    """gamarr — Metadata game downloader.

    Monitors FitGirl repacks RSS feed for new game releases, checks
    them against Metacritic scores, and adds qualifying games to
    qBittorrent.

    All runtime configuration lives in the YAML config file inside
    --config-path. Use --log-level to override the console log level
    at runtime without editing the config.
    """
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"
    effective_log_path = log_path or _DEFAULT_LOGS_PATH
    effective_log_level = log_level.upper() if log_level else "INFO"
    create_logger(
        log_format=log_format,
        log_level=effective_log_level,
        log_path=effective_log_path,
    )

    from gamarr.scheduler import run

    if test:
        from gamarr.config import load_config

        load_config(config_path)
        click.echo("Configuration loaded successfully. Test mode \u2014 exiting.")
        return

    run(config_path=config_path, daemon_mode="background" if daemon else None)


if __name__ == "__main__":
    cli()
