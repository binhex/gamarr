"""Post-processor for gamarr.

Polls qBittorrent for completed game downloads, copies them to the
library with configurable path templates, and cleans up source torrents
after seeding goals are met.
"""

from __future__ import annotations

import contextlib
import datetime
import ntpath
import os
import re
from datetime import UTC
from os.path import relpath
from typing import TYPE_CHECKING, Any

from loguru import logger

from gamarr.file_utils import copy_with_verify, make_directory
from gamarr.models import SOURCE_DISPLAY

if TYPE_CHECKING:
    from typing import TypeGuard

    from gamarr.config import Config
    from gamarr.database import Database, HistoryRow
    from gamarr.qbittorrent import QBittorrentClient

__all__ = ["run_post_processing"]

_RE_PATH_UNSAFE = re.compile(r'[/\\<>:"|?*\x00]|\.\.')
_RE_TEMPLATE_PLACEHOLDER = re.compile(r"\{(site|platform|genre|title)\}")
_RE_WINDOWS_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE)
_MAX_PATH_COMPONENT_BYTES = 255


def _safe_path_component(value: str) -> str:
    """Return a filesystem-safe path component for all supported platforms."""
    stripped = _RE_PATH_UNSAFE.sub("", value).strip().rstrip(" .")
    if not stripped or not stripped.strip("."):
        return "Unknown"
    if _RE_WINDOWS_RESERVED.fullmatch(stripped):
        stripped = f"_{stripped}"
    if len(stripped.encode("utf-8")) > _MAX_PATH_COMPONENT_BYTES:
        stripped = stripped.encode("utf-8")[:_MAX_PATH_COMPONENT_BYTES].decode("utf-8", errors="ignore").rstrip(" .")
    return stripped or "Unknown"


def _format_path_value(value: str, key: str, path_case: str) -> str:
    """Format a single template value according to *path_case*.

    In ``"pretty"`` mode, only the ``site`` key is transformed via the
    display-name lookup table.  All other keys pass through unchanged
    (their values are already correctly capitalised from Metacritic /
    user config).  In ``"lowercase"`` mode, every value is downcased.
    """
    if path_case == "lowercase":
        return value.lower()
    # default: "pretty" or unknown (treat as pretty for forward compat)
    if key == "site":
        return SOURCE_DISPLAY.get(value, value)
    return value


def _build_destination_path(
    *,
    template: str,
    source: str,
    platform: str,
    genres: str | None,
    game_title: str,
    path_case: str = "pretty",
) -> str:
    """Resolve a library path template into a concrete filesystem path.

    Supported placeholders: {site}, {platform}, {genre}, {title}.
    {genre} uses only the first genre from a comma-separated list.
    ``path_case`` controls casing: ``"pretty"`` (default) for display
    names, ``"lowercase"`` to downcase all values.
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
    formatted_replacements = {
        key: _safe_path_component(_format_path_value(value, key, path_case)) for key, value in replacements.items()
    }
    return _RE_TEMPLATE_PLACEHOLDER.sub(lambda match: formatted_replacements[match.group(1)], template)


def _compile_exclusion_regexes(patterns: list[str], label: str) -> list[re.Pattern[str]]:
    """Compile *patterns* into case-insensitive regexes."""
    result: list[re.Pattern[str]] = []
    for r in patterns:
        if not r.strip():
            logger.warning("Empty {} regex; skipping.", label)
            continue
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
    """Return hours since *copied_at*, treating naive timestamps as UTC."""
    if not copied_at:
        return 0.0
    try:
        copied_dt = datetime.datetime.fromisoformat(copied_at)
        if copied_dt.tzinfo is None:
            copied_dt = copied_dt.replace(tzinfo=UTC)
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
    if not _is_processable_row(row, tag):
        return None

    if row.post_process_state is None and config.post_process.copy_completed:
        if config.post_process.library_path:
            if _run_copy_phase(torrent, config, row, db):
                return "copied"
            logger.warning("Copy failed for '{}'; retaining torrent for retry.", row.game_title)
            return None
        logger.info("Empty library_path; skipping copy for '{}'.", row.game_title)

    if _is_delete_eligible(row, config) and _run_delete_phase(torrent, config, qbt, row, db):
        return "deleted"
    return None


def _is_delete_eligible(row: HistoryRow, config: Config) -> bool:
    """Return True when deletion is enabled and copying completed or was skipped."""
    if not config.post_process.remove_completed:
        return False
    if row.post_process_state == "copied":
        return True
    # Allow direct delete when copying is disabled or library_path is empty.
    return row.post_process_state is None and (
        not config.post_process.copy_completed or not config.post_process.library_path
    )


def _is_processable_row(row: HistoryRow | None, tag: str) -> TypeGuard[HistoryRow]:
    """Return whether *row* is active and can be post-processed."""
    if row is None:
        logger.debug("No history record for tag '{}'; skipping.", tag)
        return False
    if row.post_process_state == "deleted":
        logger.info("Torrent '{}' already deleted; skipping.", tag)
        return False
    return True


def _run_copy_phase(
    torrent: dict,
    config: Config,
    row: HistoryRow,
    db: Database,
) -> bool:
    """Copy completed torrent files to the library.

    Returns True if files were successfully copied (or already present),
    False only if a retryable error occurred.
    """
    pp = config.post_process
    tag = torrent["torrent_tag"]

    dst_dir = _build_destination_path(
        template=pp.library_path,
        source=row.source,
        platform=row.platform,
        genres=row.genres,
        game_title=row.game_title or "Unknown",
        path_case=pp.path_case,
    )
    if not dst_dir:
        logger.info("Empty library_path; skipping copy for '{}'.", row.game_title)
        return False

    src_files = _build_copy_list(torrent, pp)
    if not src_files:
        logger.debug("No files to copy for '{}'.", row.game_title)
        return False

    if not make_directory(dst_dir):
        logger.error("Cannot create destination directory '{}'; skipping.", dst_dir)
        return False

    created_paths: list[str] = []
    created_dirs: list[str] = []
    all_ok = _copy_all_files(src_files, dst_dir, torrent["torrent_save_path"], created_paths, created_dirs)
    if all_ok:
        copied_at = datetime.datetime.now(tz=UTC).isoformat()
        db.set_post_process_state(tag, "copied", copied_at=copied_at)
        logger.info("Copied '{}' to '{}'.", row.game_title, dst_dir)
        return True
    else:
        # Remove only files created by this attempt; preserve pre-existing data.
        logger.warning("Copy failed for '{}'; retaining existing destination files.", row.game_title)
        _remove_created_files(created_paths, created_dirs)
        return False


def _copy_destination_path(src_path: str, dst_dir: str, save_path: str) -> str | None:
    """Return a contained destination path for *src_path*, or ``None``."""
    try:
        rel_path = relpath(src_path, save_path)
    except ValueError:
        logger.error("Cannot calculate relative path for '{}'; aborting.", src_path)
        return None
    safe_rel_path = _safe_relative_path(rel_path)
    if safe_rel_path is None:
        logger.error("Unsafe torrent file path '{}'; aborting.", rel_path)
        return None
    if not _is_path_within(src_path, save_path):
        logger.error("Source path '{}' escapes save path; aborting.", src_path)
        return None
    dst_path = os.path.join(dst_dir, safe_rel_path)
    if not _is_path_within(dst_path, dst_dir):
        logger.error("Destination path '{}' escapes destination; aborting.", dst_path)
        return None
    if os.path.islink(dst_path):
        logger.error("Destination path '{}' is a symlink; aborting.", dst_path)
        return None
    return dst_path


def _track_created_copy_paths(
    dst_path: str,
    dst_dir: str,
    created_paths: list[str] | None,
    created_dirs: list[str] | None,
) -> None:
    """Track destination files and directories created by a copy attempt."""
    if created_dirs is not None:
        _record_missing_parent_dirs(dst_path, dst_dir, created_dirs)
    if created_paths is not None and not os.path.lexists(dst_path):
        created_paths.append(dst_path)


def _copy_all_files(
    src_files: list[str],
    dst_dir: str,
    save_path: str,
    created_paths: list[str] | None = None,
    created_dirs: list[str] | None = None,
) -> bool:
    """Copy files into *dst_dir*, preserving structure and tracking new paths.

    Each source file's path relative to *save_path* is mirrored under
    *dst_dir* so nested folders (e.g. ``DLC/``) are kept instead of
    flattened into the destination root. Newly created destination paths
    and parent directories are tracked when supplied for safe failure
    cleanup.

    Returns True if all succeeded, False on first failure.
    """
    for src_path in src_files:
        dst_path = _copy_destination_path(src_path, dst_dir, save_path)
        if dst_path is None:
            return False
        _track_created_copy_paths(dst_path, dst_dir, created_paths, created_dirs)
        if not copy_with_verify(src_path, dst_path):
            logger.error("Copy/verify failed for '{}'; aborting.", src_path)
            return False
    return True


def _run_delete_phase(
    torrent: dict,
    config: Config,
    qbt: QBittorrentClient,
    row: HistoryRow,
    db: Database,
) -> bool:
    """Delete source torrent if seeding goal is met or timeout exceeded.

    Returns True if the torrent was deleted, False otherwise.
    """
    torrent_state = torrent.get("torrent_state", "")
    pp = config.post_process
    tag = torrent["torrent_tag"]

    should_delete = torrent_state in ("pausedUP", "stoppedUP")
    # Use the copy timestamp when available; skipped-copy rows fall back to
    # their acquisition timestamp so max_seed_wait_hours remains effective.
    if not should_delete:
        age = _copied_age_hours(row.post_process_copied_at or row.processed_at)
        if pp.max_seed_wait_hours > 0 and age >= pp.max_seed_wait_hours:
            logger.info(
                "Seed wait timeout ({} >= {}h) for '{}'; deleting.",
                age,
                pp.max_seed_wait_hours,
                row.game_title,
            )
            should_delete = True

    if should_delete:
        if qbt.delete_torrent(torrent["torrent_hash"], delete_data=True):
            db.set_post_process_state(tag, "deleted")
            logger.info("Deleted torrent '{}' after post-processing.", row.game_title)
            return True
        return False
    else:
        logger.info(
            "Torrent '{}' still seeding (state={}); waiting for seeding to finish.",
            row.game_title,
            torrent_state,
        )
        return False


def _record_missing_parent_dirs(path: str, root: str, created_dirs: list[str]) -> None:
    """Record missing parent directories under *root* before a copy creates them."""
    parent = os.path.dirname(path)
    while parent and _is_path_within(parent, root) and not os.path.lexists(parent):
        created_dirs.append(parent)
        next_parent = os.path.dirname(parent)
        if next_parent == parent:
            break
        parent = next_parent


def _remove_created_files(file_paths: list[str], directory_paths: list[str]) -> None:
    """Remove only files and empty directories created by a copy attempt."""
    for path in file_paths:
        with contextlib.suppress(OSError):
            if os.path.lexists(path):
                os.unlink(path)
    for path in directory_paths:
        with contextlib.suppress(OSError):
            if os.path.isdir(path) and not os.path.islink(path):
                os.rmdir(path)


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


def _is_path_within(path: str, root: str) -> bool:
    """Return whether *path* remains within *root* after symlink resolution."""
    try:
        normalized_path = os.path.normcase(os.path.realpath(path))
        normalized_root = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath((normalized_path, normalized_root)) == normalized_root
    except ValueError:
        return False


def _has_absolute_path_syntax(path: str) -> bool:
    """Return whether *path* uses absolute POSIX or Windows syntax."""
    if os.path.isabs(path):
        return True
    if ntpath.isabs(path):
        return True
    drive, _ = ntpath.splitdrive(path)
    return bool(drive)


def _has_parent_path_component(path: str) -> bool:
    """Return whether *path* contains an explicit parent traversal."""
    return any(part == ".." for part in path.replace("\\", "/").split("/"))


def _normalized_path_is_unsafe(path: str) -> bool:
    """Return whether a normalized relative path is empty or escaping."""
    if path in ("", "."):
        return True
    if path == os.pardir:
        return True
    return path.startswith(os.pardir + os.sep)


def _safe_relative_path(rel_path: str) -> str | None:
    """Normalize a torrent-relative path, rejecting absolute and traversal paths."""
    if not rel_path:
        return None
    if "\x00" in rel_path:
        return None
    if _has_absolute_path_syntax(rel_path):
        return None
    if _has_parent_path_component(rel_path):
        return None
    normalized = os.path.normpath(rel_path.replace("\\", os.sep))
    return None if _normalized_path_is_unsafe(normalized) else normalized


def _process_file_entry(
    file_entry: dict,
    save_path: str,
    min_kb: int,
    file_regexes: list[re.Pattern[str]],
    folder_regexes: list[re.Pattern[str]],
) -> str | None:
    """Process a single safe file entry and return its absolute path."""
    rel_path = file_entry.get("file_name") or ""
    if not isinstance(rel_path, str):
        return None
    safe_rel_path = _safe_relative_path(rel_path)
    if safe_rel_path is None:
        logger.warning("Skipping unsafe torrent file path '{}'.", rel_path)
        return None
    abs_path = os.path.join(save_path, safe_rel_path)
    if not _is_path_within(abs_path, save_path):
        logger.warning("Skipping torrent file outside save path '{}'.", rel_path)
        return None
    try:
        file_size = int(file_entry.get("file_size") or 0)
    except (ValueError, TypeError):
        file_size = 0
    file_size_kb = file_size >> 10
    folder_part = os.path.dirname(safe_rel_path)
    if _file_excluded(safe_rel_path, folder_part, file_size_kb, file_regexes, folder_regexes, min_kb):
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
