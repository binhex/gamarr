"""Command-line interface for gamarr."""

from __future__ import annotations

import os
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any

import click
from loguru import logger

from gamarr.logger import create_logger
from gamarr.utils import get_project_root

if TYPE_CHECKING:
    from gamarr.config import Config


def _clear_caches(db_path: str, clear_cache: str) -> None:
    """Clear cache entries specified by ``--clear-cache`` flag value."""
    from gamarr.database import Database

    db: Database | None = None
    try:
        db = Database(db_path=db_path)
        sources = [s.strip().casefold() for s in clear_cache.split(",")]
        for source in sources:
            if source == "all":
                for s in ("fitgirl", "freegog", "metacritic"):
                    db.clear_cache(s)
            elif source in ("fitgirl", "freegog", "metacritic"):
                db.clear_cache(source)
            else:
                logger.warning("Unknown cache source '{}' — skipping", source)
    finally:
        if db is not None:
            db.close()


def _resolve_version() -> str:
    """Return the gamarr version.

    Reads from pyproject.toml (the source of truth) when available
    (editable installs), falling back to installed package metadata
    (wheel/PyPI installs), then ``"unknown"`` as a last resort.
    """
    # Priority 1: pyproject.toml — source of truth for dev/editable installs
    try:
        pyproject_path = get_project_root() / "pyproject.toml"
        with pyproject_path.open("rb") as fh:
            data = tomllib.load(fh)
        return str(data["project"]["version"])
    except Exception:
        pass
    # Priority 2: importlib.metadata — works for wheel/PyPI installs
    try:
        return _pkg_version("gamarr")
    except PackageNotFoundError:
        return "unknown"


_VERSION = _resolve_version()
_PROJECT_ROOT = get_project_root()
_LOG_BASENAME = "gamarr.log"
_DEFAULT_LOGS_DIR = f"{_PROJECT_ROOT}/logs"


def _apply_general_overrides(config: Config, overrides: dict[str, Any]) -> None:
    if overrides.get("db_path") is not None:
        config.general.db_path = str(overrides["db_path"])
    if overrides.get("pid_path") is not None:
        config.general.pid_path = str(overrides["pid_path"])
    paths_override = overrides.get("library_path_list")
    if paths_override:
        config.library.paths = [os.path.abspath(p.strip()) for p in paths_override.split("|") if p.strip()]


def _apply_qbt_overrides(config: Config, overrides: dict[str, Any]) -> None:
    if overrides.get("qbt_host") is not None:
        config.torrent_client.qbittorrent.host = str(overrides["qbt_host"])
    if overrides.get("qbt_port") is not None:
        config.torrent_client.qbittorrent.port = int(overrides["qbt_port"])
    if overrides.get("qbt_username") is not None:
        config.torrent_client.qbittorrent.username = str(overrides["qbt_username"])
    if overrides.get("qbt_password") is not None:
        config.torrent_client.qbittorrent.password = str(overrides["qbt_password"])


def _apply_cli_overrides(config: Config, **overrides: Any) -> None:
    """Apply non-None CLI override values onto *config* in-place."""
    _apply_general_overrides(config, overrides)
    _apply_qbt_overrides(config, overrides)


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
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<dir>",
    help="Override the log file directory from config (logs are written to <dir>/gamarr.log).",
)
@click.option(
    "--test",
    is_flag=True,
    default=False,
    help="Validate configuration and exit without running any tasks.",
)
@click.option(
    "--db-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<dir>",
    help="Override the database directory from config.",
)
@click.option(
    "--pid-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<dir>",
    help="Override the PID file directory from config.",
)
@click.option(
    "--library-path",
    "library_path_list",
    default=None,
    type=str,
    show_default=False,
    metavar="<paths>",
    help='Override library paths from config (pipe-separated: --library-path "/a|/b").',
)
@click.option(
    "--qbt-host",
    default=None,
    show_default=False,
    metavar="<host>",
    help="Override qBittorrent host from config.",
)
@click.option(
    "--qbt-port",
    type=int,
    default=None,
    show_default=False,
    metavar="<port>",
    help="Override qBittorrent WebUI port from config.",
)
@click.option(
    "--qbt-username",
    default=None,
    show_default=False,
    metavar="<user>",
    help="Override qBittorrent username from config.",
)
@click.option(
    "--qbt-password",
    default=None,
    show_default=False,
    hide_input=True,
    metavar="<pass>",
    help="Override qBittorrent password from config.",
)
@click.option(
    "--clear-cache",
    default=None,
    show_default=False,
    metavar="<sources>",
    help="Clear cached data before running. Comma-separated: fitgirl, freegog, metacritic, or all.",
)
@click.version_option(version=_VERSION, prog_name="gamarr")
def cli(
    config_path: str,
    log_level: str | None,
    log_path: str | None,
    test: bool,
    db_path: str | None = None,
    pid_path: str | None = None,
    library_path_list: str | None = None,
    qbt_host: str | None = None,
    qbt_port: int | None = None,
    qbt_username: str | None = None,
    qbt_password: str | None = None,
    clear_cache: str | None = None,
) -> None:
    """gamarr — Metadata game downloader.

    Monitors download sources for new game releases, checks
    them against Metacritic scores, and adds qualifying games to
    qBittorrent.

    Runs in scheduled mode when ``schedule.enabled`` is
    ``true`` in the config file, or as a single pass otherwise.
    """
    import loguru

    from gamarr.config import load_config

    loguru.logger.remove()
    loguru.logger.add(
        sink=lambda message: print(message, end=""),
        level=log_level.upper() if log_level else "INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        colorize=True,
    )

    config = load_config(config_path)

    _apply_cli_overrides(
        config,
        db_path=db_path,
        pid_path=pid_path,
        library_path_list=library_path_list,
        qbt_host=qbt_host,
        qbt_port=qbt_port,
        qbt_username=qbt_username,
        qbt_password=qbt_password,
    )

    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"
    effective_log_dir = log_path or _DEFAULT_LOGS_DIR
    effective_log_path = os.path.join(effective_log_dir, _LOG_BASENAME)
    effective_log_level = log_level.upper() if log_level else config.general.log_level_console
    create_logger(
        log_format=log_format,
        log_level=effective_log_level,
        log_path=effective_log_path,
    )

    if test:
        click.echo("Configuration loaded successfully. Test mode \u2014 exiting.")
        return

    if clear_cache:
        _clear_caches(str(config.general.db_path), clear_cache)

    from gamarr.scheduler import run

    run(config)


if __name__ == "__main__":
    cli()
