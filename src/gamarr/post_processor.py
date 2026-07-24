"""Post-processor for gamarr.

Polls qBittorrent for completed game downloads, copies them to the
library with configurable path templates, and cleans up source torrents
after seeding goals are met.
"""

from __future__ import annotations

import datetime
import os
import re
from datetime import UTC
from typing import TYPE_CHECKING, Any

from loguru import logger

from gamarr.file_utils import copy_with_verify, make_directory

if TYPE_CHECKING:
    from gamarr.config import Config
    from gamarr.database import Database, HistoryRow
    from gamarr.qbittorrent import QBittorrentClient

__all__ = ["run_post_processing"]

_RE_PATH_UNSAFE = re.compile(r'[/\\<>:"|?*\x00]|\.\.')


def _safe_path_component(value: str) -> str:
    """Strip characters that are unsafe in a filesystem path component."""
    stripped = _RE_PATH_UNSAFE.sub("", value).strip()
    if not stripped or not stripped.strip("."):
        return "Unknown"
    return stripped


def _build_destination_path(
    *,
    template: str,
    source: str,
    platform: str,
    genres: str | None,
    game_title: str,
) -> str:
    """Resolve a library path template into a concrete filesystem path.

    Supported placeholders: {site}, {platform}, {genre}, {title}.
    {genre} uses only the first genre from a comma-separated list.
    """
    if not template:
        return ""
    first_genre = genres.split(",")[0].strip() if genres else "Unknown"
    replacements = {
        "site": source,
        "platform": platform,
        "genre": first_genre,
        "title": game_title,
    }
    result = template
    for key, value in replacements.items():
        result = result.replace("{" + key + "}", _safe_path_component(value))
    return result


def _compile_exclusion_regexes(patterns: list[str], label: str) -> list[re.Pattern[str]]:
    """Compile *patterns* into case-insensitive regexes."""
    result: list[re.Pattern[str]] = []
    for r in patterns:
        try:
            result.append(re.compile(r, re.IGNORECASE))
        except re.error:
            logger.warning("Invalid {} regex '{}'; skipping.", label, r)
    return result


def _file_excluded(
    rel_path: str,
    folder_part: str,
    file_size_kb: int,
    file_regexes: list[re.Pattern[str]],
    folder_regexes: list[re.Pattern[str]],
    min_kb: int,
) -> bool:
    """Return True if this file matches an exclusion rule."""
    if any(rx.search(rel_path) for rx in file_regexes):
        return True
    if any(rx.search(folder_part) for rx in folder_regexes):
        return True
    return bool(min_kb and file_size_kb < min_kb)


def _copied_age_hours(copied_at: str | None) -> float:
    """Return hours since *copied_at* (ISO-8601 timestamp), or 0 if unknown."""
    if not copied_at:
        return 0.0
    try:
        copied_dt = datetime.datetime.fromisoformat(copied_at)
        return (datetime.datetime.now(tz=UTC) - copied_dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return 0.0


def _process_one(
    torrent: dict,
    config: Config,
    qbt: QBittorrentClient,
    db: Database,
) -> str | None:
    """Handle a single completed torrent: copy or delete based on state.

    Returns:
        "copied" if files were successfully copied.
        "deleted" if source torrent was deleted after seeding.
        None if no action was taken (no-op).
    """
    tag = torrent["torrent_tag"]

    row: HistoryRow | None = db.find_by_tag(tag)
    if _is_no_op_row(row, tag):
        return None
    assert row is not None  # narrowed by _is_no_op_row

    if row.post_process_state is None and config.post_process.copy_completed:
        if _run_copy_phase(torrent, config, row):
            return "copied"
        return None

    if _is_delete_eligible(row, config) and _run_delete_phase(torrent, config, qbt, row):
        return "deleted"
    return None


def _is_delete_eligible(row: HistoryRow, config: Config) -> bool:
    """Return True if the torrent is in the copied state and deletion is enabled."""
    return row.post_process_state == "copied" and config.post_process.remove_completed


def _is_no_op_row(row: HistoryRow | None, tag: str) -> bool:
    """Return True if the row should be skipped (no history or already deleted)."""
    if row is None:
        logger.warning("No history record for tag '{}'; skipping.", tag)
        return True
    if row.post_process_state == "deleted":
        logger.info("Torrent '{}' already deleted; skipping.", tag)
        return True
    return False


def _run_copy_phase(
    torrent: dict,
    config: Config,
    row: HistoryRow,
) -> bool:
    """Copy completed torrent files to the library.

    Returns True if files were successfully copied, False otherwise.
    """
    pp = config.post_process

    dst_dir = _build_destination_path(
        template=pp.library_path,
        source=row.source,
        platform=row.platform,
        genres=row.genres,
        game_title=row.game_title or "Unknown",
    )
    if not dst_dir:
        logger.info("Empty library_path; skipping copy for '{}'.", row.game_title)
        return False

    if os.path.isdir(dst_dir):
        logger.info("Destination '{}' already exists; skipping '{}'.", dst_dir, row.game_title)
        return False

    src_files = _build_copy_list(torrent, pp)
    if not src_files:
        logger.debug("No files to copy for '{}'.", row.game_title)
        return False

    if not make_directory(dst_dir):
        logger.error("Cannot create destination directory '{}'; skipping.", dst_dir)
        return False

    all_ok = _copy_all_files(src_files, dst_dir)
    if all_ok:
        row.post_process_state = "copied"
        row.post_process_copied_at = datetime.datetime.now(tz=UTC).isoformat()
        logger.info("Copied '{}' to '{}'.", row.game_title, dst_dir)
        return True
    else:
        logger.warning("Copy failed for '{}'; will retry on next cycle.", row.game_title)
        return False


def _copy_all_files(src_files: list[str], dst_dir: str) -> bool:
    """Copy all files to dst_dir. Returns True if all succeeded, False on first failure."""
    for src_path in src_files:
        fname = os.path.basename(src_path)
        dst_path = os.path.join(dst_dir, fname)
        if not copy_with_verify(src_path, dst_path):
            logger.error("Copy/verify failed for '{}'; aborting.", src_path)
            return False
    return True


def _run_delete_phase(
    torrent: dict,
    config: Config,
    qbt: QBittorrentClient,
    row: HistoryRow,
) -> bool:
    """Delete source torrent if seeding goal is met or timeout exceeded.

    Returns True if the torrent was deleted, False otherwise.
    """
    torrent_state = torrent.get("torrent_state", "")
    pp = config.post_process

    should_delete = torrent_state in ("pausedUP", "stoppedUP")
    if not should_delete:
        age = _copied_age_hours(row.post_process_copied_at)
        if pp.max_seed_wait_hours > 0 and age >= pp.max_seed_wait_hours:
            logger.info(
                "Seed wait timeout ({} >= {}h) for '{}'; deleting.",
                age,
                pp.max_seed_wait_hours,
                row.game_title,
            )
            should_delete = True

    if should_delete:
        qbt.delete_torrent(torrent["torrent_hash"], delete_data=True)
        row.post_process_state = "deleted"
        logger.info("Deleted torrent '{}' after post-processing.", row.game_title)
        return True
    else:
        logger.info(
            "Torrent '{}' still seeding (state={}); waiting for seeding to finish.",
            row.game_title,
            torrent_state,
        )
        return False


def _build_copy_list(torrent: dict, pp: Any) -> list[str]:
    """Return absolute file paths that pass exclusion rules."""
    save_path = torrent.get("torrent_save_path") or ""
    if not save_path:
        tag = torrent.get("torrent_tag", "unknown")
        logger.warning("torrent_save_path is empty for tag '{}'; skipping copy.", tag)
        return []

    file_list = torrent.get("torrent_file_list") or []
    min_kb = pp.exclude_file_min_kb
    file_regexes = _compile_exclusion_regexes(pp.exclude_file_regex_list, "file-exclude")
    folder_regexes = _compile_exclusion_regexes(pp.exclude_folder_regex_list, "folder-exclude")

    result: list[str] = []
    for f in file_list:
        abs_path = _process_file_entry(f, save_path, min_kb, file_regexes, folder_regexes)
        if abs_path:
            result.append(abs_path)
    return result


def _process_file_entry(
    file_entry: dict,
    save_path: str,
    min_kb: int,
    file_regexes: list[re.Pattern[str]],
    folder_regexes: list[re.Pattern[str]],
) -> str | None:
    """Process a single file entry. Returns absolute path or None if excluded."""
    rel_path = file_entry.get("file_name") or ""
    if not rel_path:
        return None
    abs_path = os.path.join(save_path, rel_path)
    try:
        file_size = int(file_entry.get("file_size") or 0)
    except (ValueError, TypeError):
        file_size = 0
    file_size_kb = file_size >> 10
    folder_part = os.path.dirname(rel_path)
    if _file_excluded(rel_path, folder_part, file_size_kb, file_regexes, folder_regexes, min_kb):
        return None
    return abs_path


def run_post_processing(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    """Main post-processing entry point.

    Polls qBittorrent for completed gamarr downloads and handles
    copy-to-library and source-torrent cleanup in a two-phase lifecycle.
    """
    pp = config.post_process
    if not pp.post_process_enabled:
        logger.debug("Post-processing disabled; skipping.")
        return

    if not qbt.is_connected():
        logger.warning("qBittorrent is unreachable; skipping post-processing.")
        return

    completed, total_gamarr = qbt.list_completed()
    if total_gamarr == 0:
        logger.debug("No completed torrents to post-process.")
        return

    downloading = total_gamarr - len(completed)
    copied = 0
    deleted = 0
    errors = 0

    for torrent in completed:
        try:
            result = _process_one(torrent, config, qbt, db)
            if result == "copied":
                copied += 1
            elif result == "deleted":
                deleted += 1
        except Exception:  # noqa: BLE001
            logger.exception("Post-processing failed for torrent '{}'.", torrent.get("torrent_tag", "unknown"))
            errors += 1

    logger.info(
        "Post-processing: {} downloading, {} copied, {} deleted, {} errors",
        downloading,
        copied,
        deleted,
        errors,
    )
