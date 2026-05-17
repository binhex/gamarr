"""Command-line interface for gamarr."""

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

# Compute default database path (project_root/db/gamarr.db)
_PROJECT_ROOT = get_project_root()
_DEFAULT_DB_PATH = f"{_PROJECT_ROOT}/db/gamarr.db"
_DEFAULT_LOGS_PATH = f"{_PROJECT_ROOT}/logs/gamarr.log"


@click.command()
@click.option(
    "--database-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    required=False,
    default=_DEFAULT_DB_PATH,
    show_default=True,
    metavar="<path>",
    help="Path to SQLite database file for tracking processed files.",
)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"], case_sensitive=False),
    metavar="<level>",
    show_default=True,
    help="Logging level for console output",
)
@click.option(
    "--log-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    required=False,
    default=_DEFAULT_LOGS_PATH,
    show_default=True,
    metavar="<path>",
    help="Path to log file for tracking application events.",
)
@click.version_option(version=_VERSION, prog_name="gamarr")
def cli(
    database_path: str,
    log_level: str,
    log_path: str,
) -> None:
    """gamarr - Metadata game downloader.

    Downloads torrent metadata (torrent files and magnet links) for games.
    """

    # Logger format for consistent output styling
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"

    logger = create_logger(log_format=log_format, log_level=log_level, log_path=log_path)

    logger.info("WIP: CLI logic not yet implemented.")


if __name__ == "__main__":
    cli()
