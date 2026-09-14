"""File system utilities for gamarr post-processing."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from loguru import logger

__all__ = ["copy_with_verify", "make_directory"]

_CHUNK_SIZE = 65_536  # 64 KiB


def _sha256(file_path: Path) -> str:
    """Return the SHA-256 hex digest of *file_path*."""
    h = hashlib.sha256()
    total = file_path.stat().st_size
    next_pct = 25
    with file_path.open("rb") as fh:
        done = 0
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            h.update(chunk)
            if total > 0:
                done += len(chunk)
                pct = done * 100 // total
                while next_pct <= pct and next_pct <= 75:
                    logger.info("Verifying '{}' checksum: {}% complete.", file_path, next_pct)
                    next_pct += 25
    return h.hexdigest()


def make_directory(path: str | Path) -> bool:
    """Create *path* and all parents. Returns True on success."""
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        logger.debug("Created directory '{}'.", target)
        return True
    except PermissionError as exc:
        logger.warning("Permission denied creating '{}': {}.", target, exc)
    except OSError as exc:
        logger.warning("OS error creating '{}': {}.", target, exc)
    return False


def _verify_existing(src: Path, dst: Path) -> bool | None:
    """Check if *dst* already matches *src* by SHA-256.

    Returns:
        True  — dst is identical to src; skip copy.
        None  — dst was mismatched; proceed to replace it after verification.
        False — source file disappeared during verification.
    """
    logger.info("Verifying existing destination '{}' checksum.", dst)
    try:
        src_hash = _sha256(src)
    except OSError:
        logger.error("Source file disappeared during verification: '{}'", src)
        return False
    dst_hash = _sha256(dst)
    if src_hash == dst_hash:
        logger.info("Destination '{}' already matches source (sha256={}); skipping copy.", dst, src_hash[:12])
        return True
    logger.warning(
        "Destination '{}' checksum mismatch (src={}, dst={}); re-copying.", dst, src_hash[:12], dst_hash[:12]
    )
    return None


def _do_copy(src: Path, dst: Path) -> None:
    """Copy *src* to *dst* with chunked progress logging."""
    total = src.stat().st_size
    next_pct = 25
    logger.info("Copying '{}' -> '{}'.", src, dst)
    with src.open("rb") as fsrc, dst.open("wb") as fdst:
        done = 0
        for chunk in iter(lambda: fsrc.read(_CHUNK_SIZE), b""):
            fdst.write(chunk)
            done += len(chunk)
            if total > 0:
                pct = done * 100 // total
                while next_pct <= pct and next_pct <= 75:
                    logger.info("Copying '{}' -> '{}': {}% complete.", src, dst, next_pct)
                    next_pct += 25
    shutil.copystat(str(src), str(dst))
    logger.info("Copied '{}' -> '{}'.", src, dst)


def _remove_temp_copy(temp_path: Path | None) -> None:
    """Remove a temporary copy when one exists, ignoring cleanup errors."""
    if temp_path is not None:
        with contextlib.suppress(OSError):
            temp_path.unlink()


def _copy_to_temporary(src: Path, dst: Path) -> Path | None:
    """Copy *src* to a temporary sibling; return its path or ``None`` on failure."""
    temp_path: Path | None = None
    try:
        file_descriptor, temp_name = tempfile.mkstemp(
            dir=dst.parent,
            prefix=f".{dst.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        os.close(file_descriptor)
        _do_copy(src, temp_path)
        return temp_path
    except FileNotFoundError as exc:
        logger.warning("Source '{}' not found during copy: {}.", src, exc)
    except PermissionError as exc:
        logger.warning("Permission denied copying '{}' -> '{}': {}.", src, dst, exc)
    except OSError as exc:
        logger.warning("OS error copying '{}' -> '{}': {}.", src, dst, exc)
    except BaseException:
        _remove_temp_copy(temp_path)
        raise
    _remove_temp_copy(temp_path)
    return None


def _hash_source_for_verification(src: Path) -> str | None:
    """Return the source hash, or ``None`` after logging an ``OSError``."""
    try:
        return _sha256(src)
    except OSError:
        logger.error("Source file disappeared during post-copy verification: '{}'", src)
        return None


def _hash_temporary_for_verification(temp_path: Path) -> str | None:
    """Return the temporary-copy hash, or ``None`` after logging a verification failure."""
    try:
        return _sha256(temp_path)
    except OSError as exc:
        logger.warning("Could not verify temporary destination '{}': {}.", temp_path, exc)
        return None


def _replace_with_verified_copy(temp_path: Path, dst: Path) -> bool:
    """Replace *dst* with the verified temporary copy."""
    try:
        os.replace(temp_path, dst)
        return True
    except OSError as exc:
        logger.warning("Could not replace destination '{}' with verified copy: {}.", dst, exc)
        return False


def _perform_copy(src: Path, dst: Path) -> bool:
    """Copy *src* to a temporary sibling, verify it, then replace *dst*."""
    temp_path = _copy_to_temporary(src, dst)
    if temp_path is None:
        return False
    try:
        logger.info("Verifying copy integrity for '{}'.", dst)
        src_hash = _hash_source_for_verification(src)
        if src_hash is None:
            return False
        dst_hash = _hash_temporary_for_verification(temp_path)
        if dst_hash is None:
            return False
        if src_hash != dst_hash:
            logger.warning("Post-copy checksum mismatch for '{}': src={}, dst={}.", dst, src_hash[:12], dst_hash[:12])
            return False
        if not _replace_with_verified_copy(temp_path, dst):
            return False
        logger.info("Verified '{}' (sha256={}).", dst, dst_hash[:12])
        return True
    finally:
        _remove_temp_copy(temp_path)


def copy_with_verify(src: str | Path, dst: str | Path) -> bool:
    """Copy *src* to *dst* with SHA-256 pre/post verification.

    - If *dst* already exists and checksums match, the copy is skipped.
    - If *dst* exists but checksums differ, it is replaced after the new
      copy passes verification.
    - After copying, checksums are compared again to confirm integrity.

    Returns:
        True if the file is present at *dst* with the correct checksum.
    """
    src_path = Path(src)
    dst_path = Path(dst)
    if not make_directory(dst_path.parent):
        return False
    if dst_path.is_file():
        existing = _verify_existing(src_path, dst_path)
        if existing is True:
            return True
        if existing is False:
            return False
    return _perform_copy(src_path, dst_path)
