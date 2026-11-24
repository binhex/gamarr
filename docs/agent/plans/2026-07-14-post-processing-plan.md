# Post-Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a post-processing thread that polls qBittorrent for completed game downloads, copies them to a library with configurable path templates, and cleans up after seeding goals are met.

**Architecture:** Two new modules (`file_utils.py`, `post_processor.py`) plus extensions to five existing files (config, database, qBittorrent, pipeline, scheduler). The post-processor runs as a separate APScheduler job alongside acquisition. Files are copied with SHA-256 verification. Two-phase lifecycle: copy as soon as download completes, delete only after seeding goals are met.

**Tech Stack:** Python 3.12+, SQLAlchemy, APScheduler, qbittorrentapi, Pydantic, loguru, pytest

---

### Task 1: PostProcessConfig Model

**Files:**
- Read: `src/gamarr/config.py`
- Modify: `src/gamarr/config.py:180-198` (add model before `Config`, add field on `Config`)

- [ ] **Step 1: Define the PostProcessConfig model**

Add this class right before the `Config` class (after `LibraryConfig`, around line 177):

```python
class PostProcessConfig(BaseModel):
    """Post-processing settings for copying completed downloads to library."""

    post_process_enabled: bool = True
    schedule_time_mins: int = Field(default=5, gt=0, description="Polling interval in minutes (must be > 0).")
    run_on_start: bool = True
    library_path: str = ""
    copy_completed: bool = True
    remove_completed: bool = True
    max_seed_wait_hours: int = Field(default=168, ge=0, description="Fallback: delete after hours even if seeding. 0 = never.")
    exclude_file_min_kb: int = 0
    exclude_file_regex_list: list[str] = Field(default_factory=list)
    exclude_folder_regex_list: list[str] = Field(default_factory=list)
```

- [ ] **Step 2: Add the post_process field to Config**

Add `post_process` after the `library` field in the `Config` class:

```python
class Config(BaseModel):
    # ... existing fields unchanged ...
    library: LibraryConfig = Field(default_factory=LibraryConfig)
    post_process: PostProcessConfig = Field(default_factory=PostProcessConfig)  # NEW
```

- [ ] **Step 3: Verify the model loads correctly**

```bash
cd /data/gamarr && python3 -c "
from gamarr.config import Config
c = Config()
print('post_process_enabled:', c.post_process.post_process_enabled)
print('schedule_time_mins:', c.post_process.schedule_time_mins)
print('library_path:', repr(c.post_process.library_path))
"
```
Expected: `post_process_enabled: True`, `schedule_time_mins: 5`, `library_path: ''`

- [ ] **Step 4: Run existing tests to confirm no regression**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_config.py -v --tb=short 2>&1 | tail -20
```
Expected: All tests pass, no failures.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py
git commit -m "feat: add PostProcessConfig model with template path and schedule settings"
```

---

### Task 2: file_utils.py — copy_with_verify and make_directory

**Files:**
- Create: `src/gamarr/file_utils.py`
- Create: `tests/unit/test_file_utils.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/unit/test_file_utils.py`:

```python
"""Tests for gamarr file utilities."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

from gamarr.file_utils import copy_with_verify, make_directory


class TestMakeDirectory:
    """Tests for make_directory."""

    def test_creates_directory_and_parents(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c"
        assert make_directory(str(target)) is True
        assert target.is_dir()

    def test_existing_directory_returns_true(self, tmp_path: Path) -> None:
        target = tmp_path / "exists"
        target.mkdir()
        assert make_directory(str(target)) is True


class TestCopyWithVerify:
    """Tests for copy_with_verify."""

    def test_fresh_copy_succeeds(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst" / "src.bin"
        src.write_bytes(b"hello world test data")
        assert copy_with_verify(str(src), str(dst)) is True
        assert dst.is_file()
        assert dst.read_bytes() == b"hello world test data"

    def test_skip_when_dest_exists_and_matches(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        dst = dst_dir / "src.bin"
        data = b"skip match test data here"
        src.write_bytes(data)
        dst.write_bytes(data)
        with patch("gamarr.file_utils._sha256", return_value="abc123") as mock_sha:
            assert copy_with_verify(str(src), str(dst)) is True
        # _sha256 should have been called twice (src + dst), but _do_copy should NOT have been called
        assert mock_sha.call_count >= 2

    def test_re_copy_when_dest_exists_and_mismatches(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        dst = dst_dir / "src.bin"
        src.write_bytes(b"new correct data goes here")
        dst.write_bytes(b"old stale data")
        assert copy_with_verify(str(src), str(dst)) is True
        assert dst.read_bytes() == b"new correct data goes here"

    def test_returns_false_when_src_missing(self, tmp_path: Path) -> None:
        src = tmp_path / "nonexistent.bin"
        dst = tmp_path / "dst" / "nonexistent.bin"
        assert copy_with_verify(str(src), str(dst)) is False

    def test_creates_dst_parent_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "deep" / "nested" / "dst.bin"
        assert copy_with_verify(str(src), str(dst)) is True
        assert dst.is_file()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_file_utils.py -v --tb=short 2>&1 | tail -25
```
Expected: FAIL — `ModuleNotFoundError: No module named 'gamarr.file_utils'`

- [ ] **Step 3: Write the file_utils module**

Create `src/gamarr/file_utils.py`:

```python
"""File system utilities for gamarr post-processing."""

from __future__ import annotations

import hashlib
import shutil
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
        None  — dst was mismatched and deleted; proceed to copy.
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
    logger.warning("Destination '{}' checksum mismatch (src={}, dst={}); re-copying.", dst, src_hash[:12], dst_hash[:12])
    try:
        dst.unlink()
    except OSError as exc:
        logger.warning("Could not delete mismatched destination '{}': {}; overwriting.", dst, exc)
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


def _perform_copy(src: Path, dst: Path) -> bool:
    """Copy *src* to *dst* and verify with SHA-256. Returns True on success."""
    try:
        _do_copy(src, dst)
    except FileNotFoundError as exc:
        logger.warning("Source '{}' not found during copy: {}.", src, exc)
        return False
    except PermissionError as exc:
        logger.warning("Permission denied copying '{}' -> '{}': {}.", src, dst, exc)
        return False
    except OSError as exc:
        logger.warning("OS error copying '{}' -> '{}': {}.", src, dst, exc)
        return False

    logger.info("Verifying copy integrity for '{}'.", dst)
    try:
        src_hash = _sha256(src)
    except OSError:
        logger.error("Source file disappeared during post-copy verification: '{}'", src)
        return False
    dst_hash = _sha256(dst)
    if src_hash != dst_hash:
        logger.warning("Post-copy checksum mismatch for '{}': src={}, dst={}.", dst, src_hash[:12], dst_hash[:12])
        return False
    logger.info("Verified '{}' (sha256={}).", dst, dst_hash[:12])
    return True


def copy_with_verify(src: str | Path, dst: str | Path) -> bool:
    """Copy *src* to *dst* with SHA-256 pre/post verification.

    - If *dst* already exists and checksums match, the copy is skipped.
    - If *dst* exists but checksums differ, the destination is deleted and
      the file is re-copied.
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_file_utils.py -v --tb=short 2>&1 | tail -25
```
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/file_utils.py tests/unit/test_file_utils.py
git commit -m "feat: add file_utils.py with copy_with_verify and make_directory"
```

---

### Task 3: Database — HistoryRow Columns and find_by_tag

**Files:**
- Modify: `src/gamarr/database.py:24-43` (add columns to HistoryRow)
- Modify: `src/gamarr/database.py:182-210` (add column migration)
- Modify: `src/gamarr/database.py:1193-1222` (add genres param to record_processed)
- Modify: `tests/unit/test_database.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_database.py` at the end of the file (before the final blank line):

```python
class TestPostProcessColumns:
    """Tests for post_process_state and post_process_copied_at columns."""

    def test_history_columns_migrated(self, tmp_path: Path) -> None:
        """New columns should exist after Database init on an existing DB."""
        from sqlalchemy import create_engine, text

        db_path = str(tmp_path / "old_history.db")
        # Create a DB with old schema (no new columns)
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE history ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "source VARCHAR NOT NULL, source_title VARCHAR NOT NULL, "
                "source_url VARCHAR, game_title VARCHAR, platform VARCHAR NOT NULL, "
                "metascore FLOAT, user_score FLOAT, result VARCHAR NOT NULL, "
                "result_details TEXT, magnet_url VARCHAR, torrent_tag VARCHAR, "
                "processed_at VARCHAR NOT NULL)"
            ))
            conn.execute(text("INSERT INTO history (source, source_title, platform, result, processed_at) "
                            "VALUES ('fitgirl', 'Test Game', 'pc', 'Passed', '2025-01-01T00:00:00')"))
            conn.commit()

        db = Database(db_path)
        # After init, new columns should exist
        with db._session() as session:
            columns = [row[0] for row in session.execute(text("PRAGMA table_info(history)"))]
        assert "genres" in columns
        assert "post_process_state" in columns
        assert "post_process_copied_at" in columns
        db.close()

    def test_record_processed_with_genres(self, tmp_path: Path) -> None:
        """record_processed should accept and store a genres string."""
        from sqlalchemy import text

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.record_processed(
            source="fitgirl",
            source_title="Elden Ring [Repack]",
            source_url="http://example.com/elden",
            game_title="Elden Ring",
            platform="pc",
            result="Passed",
            torrent_tag="gamarr-test123",
            genres="Action, RPG",
        )
        with db._session() as session:
            row = session.execute(
                text("SELECT genres, torrent_tag FROM history WHERE torrent_tag = 'gamarr-test123'")
            ).first()
        assert row is not None
        assert row[0] == "Action, RPG"
        db.close()

    def test_find_by_tag_returns_row(self, tmp_path: Path) -> None:
        """find_by_tag should return the matching HistoryRow."""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.record_processed(
            source="fitgirl",
            source_title="Test Game",
            source_url="http://example.com/test",
            game_title="Test Game",
            platform="pc",
            result="Passed",
            torrent_tag="gamarr-findme",
            genres="Action",
        )
        row = db.find_by_tag("gamarr-findme")
        assert row is not None
        assert row.game_title == "Test Game"
        assert row.source == "fitgirl"
        assert row.platform == "pc"
        assert row.genres == "Action"
        db.close()

    def test_find_by_tag_returns_none_for_unknown_tag(self, tmp_path: Path) -> None:
        """find_by_tag should return None for a non-existent tag."""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        row = db.find_by_tag("gamarr-nope")
        assert row is None
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_database.py::TestPostProcessColumns -v --tb=short 2>&1 | tail -25
```
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'find_by_tag'` and column assertion failures.

- [ ] **Step 3: Add columns to HistoryRow ORM**

In `src/gamarr/database.py`, add three new mapped columns to `HistoryRow` (after `torrent_tag` at line 40):

```python
    torrent_tag: Mapped[str | None] = mapped_column(String, nullable=True)
    genres: Mapped[str | None] = mapped_column(String, nullable=True)  # NEW
    post_process_state: Mapped[str | None] = mapped_column(String, nullable=True)  # NEW
    post_process_copied_at: Mapped[str | None] = mapped_column(String, nullable=True)  # NEW
    processed_at: Mapped[str] = mapped_column(String, nullable=False)
```

- [ ] **Step 4: Add column migration to Database.__init__**

In `src/gamarr/database.py`, find the `__init__` method (around line 182) and add after the existing migration calls:

```python
        self._migrate_history_post_process()
```

Then add the migration method (place alongside other `_migrate_*` methods):

```python
    def _migrate_history_post_process(self) -> None:
        """Add genres, post_process_state, post_process_copied_at columns to history if missing."""
        from sqlalchemy import inspect, text

        inspector = inspect(self._engine)
        if "history" not in inspector.get_table_names():
            return
        columns = [c["name"] for c in inspector.get_columns("history")]
        for col_name in ("genres", "post_process_state", "post_process_copied_at"):
            if col_name not in columns:
                with self._session() as session:
                    session.execute(text(f"ALTER TABLE history ADD COLUMN {col_name} VARCHAR"))
                logger.debug("Added {} column to history", col_name)
```

- [ ] **Step 5: Add genres param to record_processed**

In `src/gamarr/database.py`, add `genres` parameter to `record_processed()`:

```python
    def record_processed(
        self,
        *,
        source: str,
        source_title: str,
        source_url: str | None = None,
        game_title: str | None = None,
        platform: str = "pc",
        metascore: float | None = None,
        user_score: float | None = None,
        result: str = "Passed",
        result_details: str = "",
        magnet_url: str | None = None,
        torrent_tag: str | None = None,
        genres: str | None = None,  # NEW
    ) -> None:
        with self._session() as session:
            row = HistoryRow(
                source=source,
                source_title=source_title,
                source_url=source_url if source_url is not None else source_title,
                game_title=game_title,
                platform=platform,
                metascore=metascore,
                user_score=user_score,
                result=result,
                result_details=result_details,
                magnet_url=magnet_url,
                torrent_tag=torrent_tag,
                genres=genres,  # NEW
                processed_at=datetime.datetime.now(tz=datetime.UTC).isoformat(),
            )
            session.add(row)
            session.commit()
```

- [ ] **Step 6: Add find_by_tag method to Database**

Add this method to the `Database` class (alongside other query methods):

```python
    def find_by_tag(self, tag: str) -> HistoryRow | None:
        """Look up a history row by its torrent tag.

        Returns None if no matching row is found.
        """
        with self._session() as session:
            return session.query(HistoryRow).filter(HistoryRow.torrent_tag == tag).first()
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_database.py::TestPostProcessColumns -v --tb=short 2>&1 | tail -20
```
Expected: All 4 tests PASS.

- [ ] **Step 8: Run existing database tests to confirm no regression**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_database.py -v --tb=short 2>&1 | tail -20
```
Expected: All tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/gamarr/database.py tests/unit/test_database.py
git commit -m "feat: add post-process columns to history, find_by_tag, genres param on record_processed"
```

---

### Task 4: QBittorrentClient — list_completed and delete_torrent

**Files:**
- Modify: `src/gamarr/qbittorrent.py`
- Modify: `tests/unit/test_qbittorrent.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_qbittorrent.py` at the end:

```python
class TestListCompleted:
    """Tests for list_completed method."""

    def test_returns_empty_when_api_fails(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        client._client.torrents_info.side_effect = Exception("API down")
        result = client.list_completed()
        assert result == []

    def test_skips_non_gamarr_tags(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        fake_torrent = MagicMock()
        fake_torrent.tags = "other-tag, no-gamarr"
        fake_torrent.amount_left = 0
        client._client.torrents_info.return_value = [fake_torrent]
        result = client.list_completed()
        assert result == []

    def test_skips_incomplete_torrents(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        fake_torrent = MagicMock()
        fake_torrent.tags = "gamarr-abc123"
        fake_torrent.amount_left = 1024  # not done
        client._client.torrents_info.return_value = [fake_torrent]
        result = client.list_completed()
        assert result == []

    def test_returns_completed_gamarr_torrents(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        fake = MagicMock()
        fake.tags = "gamarr-xyz789"
        fake.amount_left = 0
        fake.hash = "deadbeef"
        fake.name = "Game Title [Repack]"
        fake.state = "uploading"
        fake.save_path = "/downloads/Game"
        client._client.torrents_info.return_value = [fake]

        fake_file = MagicMock()
        fake_file.name = "setup.exe"
        fake_file.size = 123456
        client._client.torrents_files.return_value = [fake_file]

        fake_props = MagicMock()
        fake_props.save_path = "/downloads/Game"
        client._client.torrents_properties.return_value = fake_props

        result = client.list_completed()
        assert len(result) == 1
        entry = result[0]
        assert entry["torrent_tag"] == "gamarr-xyz789"
        assert entry["torrent_hash"] == "deadbeef"
        assert entry["torrent_name"] == "Game Title [Repack]"
        assert entry["torrent_state"] == "uploading"
        assert entry["torrent_save_path"] == "/downloads/Game"
        assert len(entry["torrent_file_list"]) == 1
        assert entry["torrent_file_list"][0]["file_name"] == "setup.exe"
        assert entry["torrent_file_list"][0]["file_size"] == 123456


class TestDeleteTorrent:
    """Tests for delete_torrent method."""

    def test_delete_torrent_with_data(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        client.delete_torrent("abc123", delete_data=True)
        client._client.torrents_delete.assert_called_once_with(
            delete_files=True, torrent_hashes="abc123"
        )

    def test_delete_torrent_without_data(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        client.delete_torrent("abc123", delete_data=False)
        client._client.torrents_delete.assert_called_once_with(
            delete_files=False, torrent_hashes="abc123"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_qbittorrent.py::TestListCompleted tests/unit/test_qbittorrent.py::TestDeleteTorrent -v --tb=short 2>&1 | tail -20
```
Expected: FAIL — `AttributeError: 'QBittorrentClient' object has no attribute 'list_completed'`

- [ ] **Step 3: Add extract_gamarr_tag helper and list_completed**

In `src/gamarr/qbittorrent.py`, add after the existing imports:

```python
import re  # if not already imported
```

Add the tag extraction helper (after `_TAG_PREFIX`):

```python
def _extract_gamarr_tag(tags_str: str) -> str:
    """Return the first 'gamarr-' tag from a comma-separated tag string, or ''."""
    return next(
        (t.strip() for t in tags_str.split(",") if t.strip().startswith(_TAG_PREFIX)),
        "",
    )
```

Add `list_completed` method to `QBittorrentClient`:

```python
    def list_completed(self) -> list[dict[str, Any]]:
        """Return details for all 100%-complete gamarr-tagged torrents.

        Queries by category, filters to gamarr-tagged torrents with
        ``amount_left == 0`` — no status filter (mirrors movarr).
        """
        try:
            all_torrents = self._client.torrents_info(category=self._category)
        except Exception as exc:
            logger.warning("Failed to list completed torrents: {}", exc)
            return []

        results: list[dict[str, Any]] = []
        for torrent in all_torrents:
            tag = _extract_gamarr_tag(torrent.tags)
            if not tag:
                continue
            if int(torrent.amount_left) != 0:
                continue

            try:
                files = self._client.torrents_files(torrent.hash)
                props = self._client.torrents_properties(torrent.hash)
            except Exception as exc:
                logger.warning("Failed to fetch metadata for torrent '{}': {}; skipping.", torrent.hash, exc)
                continue

            results.append({
                "torrent_tag": tag,
                "torrent_hash": torrent.hash,
                "torrent_name": torrent.name,
                "torrent_save_path": props.save_path or torrent.save_path,
                "torrent_state": torrent.state,
                "torrent_file_list": [
                    {"file_name": f.name, "file_size": f.size}
                    for f in files
                ],
            })
        return results
```

- [ ] **Step 4: Add delete_torrent method**

```python
    def delete_torrent(self, torrent_hash: str, *, delete_data: bool = False) -> None:
        """Delete a torrent and optionally its downloaded data.

        Args:
            torrent_hash: The torrent hash to delete.
            delete_data: If True, also delete the downloaded files.
        """
        try:
            self._client.torrents_delete(delete_files=delete_data, torrent_hashes=torrent_hash)
            logger.info("Deleted torrent '{}' (delete_data={}).", torrent_hash, delete_data)
        except Exception as exc:
            logger.warning("Failed to delete torrent '{}': {}", torrent_hash, exc)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_qbittorrent.py::TestListCompleted tests/unit/test_qbittorrent.py::TestDeleteTorrent -v --tb=short 2>&1 | tail -20
```
Expected: All 6 tests PASS.

- [ ] **Step 6: Run existing qbittorrent tests**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_qbittorrent.py -v --tb=short 2>&1 | tail -20
```
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/qbittorrent.py tests/unit/test_qbittorrent.py
git commit -m "feat: add list_completed and delete_torrent to QBittorrentClient"
```

---

### Task 5: Pipeline — Pass Genres at Delivery

**Files:**
- Modify: `src/gamarr/pipeline.py:2598-2630` (`_record_result` function)
- Modify: `src/gamarr/pipeline.py:1882-1894` (the call site in `_deliver_match`)
- Modify: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_pipeline.py`:

```python
class TestRecordResultGenres:
    """Tests that _record_result passes genres to record_processed."""

    def test_record_result_passes_genres(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, ANY

        from gamarr.database import Database
        from gamarr.pipeline import _record_result

        db = Database(str(tmp_path / "test.db"))
        db.record_processed = MagicMock()

        _record_result(
            db,
            source="fitgirl",
            source_title="Test Game",
            source_url="mc:test-game",
            game_title="Test Game",
            platform="pc",
            result="Passed",
            torrent_tag="gamarr-test",
            genres="Action, RPG",
        )
        db.record_processed.assert_called_once()
        call_kwargs = db.record_processed.call_args.kwargs
        assert call_kwargs.get("genres") == "Action, RPG"

    def test_record_result_passes_none_genres(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _record_result

        db = Database(str(tmp_path / "test.db"))
        db.record_processed = MagicMock()

        _record_result(
            db,
            source="fitgirl",
            source_title="Test Game",
            source_url="mc:test-game",
            game_title="Test Game",
            platform="pc",
            result="Passed",
            torrent_tag="gamarr-test",
            genres=None,
        )
        call_kwargs = db.record_processed.call_args.kwargs
        assert call_kwargs.get("genres") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py::TestRecordResultGenres -v --tb=short 2>&1 | tail -20
```
Expected: FAIL — `TypeError: _record_result() got an unexpected keyword argument 'genres'`

- [ ] **Step 3: Add genres to _record_result**

In `src/gamarr/pipeline.py`, modify `_record_result` (line 2598):

```python
def _record_result(
    db: Database,
    *,
    source: str,
    source_title: str,
    source_url: str,
    game_title: str,
    platform: str,
    metascore: float | None = None,
    user_score: float | None = None,
    result: str,
    result_details: str = "",
    magnet_url: str | None = None,
    torrent_tag: str | None = None,
    genres: str | None = None,  # NEW
) -> dict[str, Any]:
    """Persist a result row and return the result dict for the caller."""
    db.record_processed(
        source=source,
        source_title=source_title,
        source_url=source_url,
        game_title=game_title,
        platform=platform,
        metascore=metascore,
        user_score=user_score,
        result=result,
        result_details=result_details,
        magnet_url=magnet_url,
        torrent_tag=torrent_tag,
        genres=genres,  # NEW
    )
    # ... rest unchanged ...
```

- [ ] **Step 4: Pass game_genres at the call site**

In `_deliver_match` (around line 1882), modify the `_record_result` call to pass `genres`:

```python
    record_result = _record_result(
        db,
        source="metacritic",
        source_title=game_title,
        source_url=f"mc:{game_slug}",
        game_title=game_title,
        platform=game_platform,
        metascore=game_metascore,
        user_score=game_user_score,
        result="Passed",
        result_details=f"Downloaded from {best['url']}",
        magnet_url=magnet,
        torrent_tag=str(tag),
        genres=", ".join(game_genres) if game_genres else None,  # NEW
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py::TestRecordResultGenres -v --tb=short 2>&1 | tail -20
```
Expected: Both tests PASS.

- [ ] **Step 6: Run full test suite to confirm no regression**

```bash
cd /data/gamarr && uv run pytest -x -q --tb=short 2>&1 | tail -10
```
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: pass genres through _record_result to history at delivery time"
```

---

### Task 6: post_processor.py — Core Post-Processing Logic

**Files:**
- Create: `src/gamarr/post_processor.py`
- Create: `tests/unit/test_post_processor.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/unit/test_post_processor.py`:

```python
"""Tests for gamarr post-processor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gamarr.post_processor import (
    _build_destination_path,
    _compile_exclusion_regexes,
    _file_excluded,
    _safe_path_component,
    run_post_processing,
)


class TestSafePathComponent:
    """Tests for filesystem-safe path component sanitization."""

    def test_strips_unsafe_chars(self) -> None:
        assert _safe_path_component("Game: Title/With*Bad?Chars") == "Game TitleWithBadChars"

    def test_strips_dotdot(self) -> None:
        assert _safe_path_component("../etc/passwd") == "etcpasswd"

    def test_preserves_normal_text(self) -> None:
        assert _safe_path_component("Elden Ring") == "Elden Ring"

    def test_empty_returns_unknown(self) -> None:
        assert _safe_path_component("") == "Unknown"

    def test_dots_only_returns_unknown(self) -> None:
        assert _safe_path_component("...") == "Unknown"


class TestBuildDestinationPath:
    """Tests for template-based path building."""

    def test_resolves_all_placeholders(self) -> None:
        result = _build_destination_path(
            template="/lib/{site}/{platform}/{genre}/{title}",
            source="fitgirl",
            platform="pc",
            genres="Action, RPG",
            game_title="Elden Ring",
        )
        assert result == "/lib/fitgirl/pc/Action/Elden Ring"

    def test_uses_first_genre_only(self) -> None:
        result = _build_destination_path(
            template="/lib/{genre}",
            source="fitgirl",
            platform="pc",
            genres="Strategy, Action, RPG",
            game_title="Civ VI",
        )
        assert result == "/lib/Strategy"

    def test_missing_genre_defaults_to_unknown(self) -> None:
        result = _build_destination_path(
            template="/lib/{genre}/{title}",
            source="fitgirl",
            platform="pc",
            genres=None,
            game_title="Test Game",
        )
        assert result == "/lib/Unknown/Test Game"

    def test_empty_library_path_returns_empty(self) -> None:
        result = _build_destination_path(
            template="",
            source="fitgirl",
            platform="pc",
            genres="Action",
            game_title="Test",
        )
        assert result == ""


class TestFileExclusion:
    """Tests for file exclusion logic."""

    def test_min_kb_excludes_small_files(self) -> None:
        exclude_file_regexes = _compile_exclusion_regexes([], "file")
        exclude_folder_regexes = _compile_exclusion_regexes([], "folder")
        assert _file_excluded("setup.exe", ".", 50, exclude_file_regexes, exclude_folder_regexes, 100) is True
        assert _file_excluded("setup.exe", ".", 200, exclude_file_regexes, exclude_folder_regexes, 100) is False

    def test_file_regex_excludes_matching(self) -> None:
        exclude_file_regexes = _compile_exclusion_regexes(["sample", "proof"], "file")
        exclude_folder_regexes = _compile_exclusion_regexes([], "folder")
        assert _file_excluded("Sample.mkv", ".", 999999, exclude_file_regexes, exclude_folder_regexes, 0) is True
        assert _file_excluded("game.iso", ".", 999999, exclude_file_regexes, exclude_folder_regexes, 0) is False

    def test_folder_regex_excludes_matching(self) -> None:
        exclude_file_regexes = _compile_exclusion_regexes([], "file")
        exclude_folder_regexes = _compile_exclusion_regexes(["subs", "extras"], "folder")
        assert _file_excluded("movie.mkv", "Subs", 999999, exclude_file_regexes, exclude_folder_regexes, 0) is True
        assert _file_excluded("movie.mkv", "Bonus", 999999, exclude_file_regexes, exclude_folder_regexes, 0) is False


class TestRunPostProcessing:
    """Tests for the main post-processing entry point."""

    def test_disabled_returns_immediately(self) -> None:
        from gamarr.config import Config

        config = Config()
        config.post_process.post_process_enabled = False
        qbt = MagicMock()
        db = MagicMock()
        run_post_processing(config, qbt, db)
        qbt.is_connected.assert_not_called()

    def test_unreachable_qbt_logs_and_returns(self) -> None:
        from gamarr.config import Config

        config = Config()
        config.post_process.post_process_enabled = True
        qbt = MagicMock()
        qbt.is_connected.return_value = False
        db = MagicMock()
        run_post_processing(config, qbt, db)
        qbt.list_completed.assert_not_called()

    def test_no_completed_torrents_returns_early(self) -> None:
        from gamarr.config import Config

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.library_path = "/lib/{title}"
        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = []
        db = MagicMock()
        run_post_processing(config, qbt, db)
        db.find_by_tag.assert_not_called()

    def test_skip_when_no_db_record(self) -> None:
        from gamarr.config import Config

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.library_path = "/lib/{title}"
        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [{
            "torrent_tag": "gamarr-unknown",
            "torrent_hash": "abc",
            "torrent_name": "Unknown Game",
            "torrent_save_path": "/dl",
            "torrent_state": "uploading",
            "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
        }]
        db = MagicMock()
        db.find_by_tag.return_value = None
        run_post_processing(config, qbt, db)
        # find_by_tag was called, but no copy attempted
        db.find_by_tag.assert_called_once_with("gamarr-unknown")

    def test_copy_phase_success(self) -> None:
        from datetime import datetime, timezone

        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.library_path = "/lib/{title}"
        config.post_process.exclude_file_min_kb = 0
        config.post_process.exclude_file_regex_list = []
        config.post_process.exclude_folder_regex_list = []

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [{
            "torrent_tag": "gamarr-test",
            "torrent_hash": "abc",
            "torrent_name": "Elden Ring",
            "torrent_save_path": "/dl/Elden Ring",
            "torrent_state": "uploading",
            "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
        }]

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.source = "fitgirl"
        fake_row.platform = "pc"
        fake_row.genres = "Action, RPG"
        fake_row.game_title = "Elden Ring"
        fake_row.post_process_state = None
        fake_row.post_process_copied_at = None
        db.find_by_tag.return_value = fake_row

        with (
            patch("gamarr.post_processor.make_directory", return_value=True),
            patch("gamarr.post_processor.copy_with_verify", return_value=True),
            patch("gamarr.post_processor.os.path.isdir", return_value=False),
        ):
            run_post_processing(config, qbt, db)

        assert fake_row.post_process_state == "copied"
        assert fake_row.post_process_copied_at is not None

    def test_skip_when_dest_exists(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.library_path = "/lib/{title}"

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [{
            "torrent_tag": "gamarr-test",
            "torrent_hash": "abc",
            "torrent_name": "Elden Ring",
            "torrent_save_path": "/dl/Elden Ring",
            "torrent_state": "uploading",
            "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
        }]

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.source = "fitgirl"
        fake_row.platform = "pc"
        fake_row.genres = "Action"
        fake_row.game_title = "Elden Ring"
        fake_row.post_process_state = None
        db.find_by_tag.return_value = fake_row

        with (
            patch("gamarr.post_processor.os.path.isdir", return_value=True),
        ):
            run_post_processing(config, qbt, db)

        assert fake_row.post_process_state is None  # unchanged — dest existed

    def test_delete_phase_paused_state(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.remove_completed = True

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [{
            "torrent_tag": "gamarr-test",
            "torrent_hash": "abc",
            "torrent_name": "Elden Ring",
            "torrent_save_path": "/dl/Elden Ring",
            "torrent_state": "pausedUP",  # seeding goal met
            "torrent_file_list": [],
        }]

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.post_process_state = "copied"
        fake_row.post_process_copied_at = "2025-01-01T00:00:00"
        db.find_by_tag.return_value = fake_row

        run_post_processing(config, qbt, db)
        qbt.delete_torrent.assert_called_once_with("abc", delete_data=True)
        assert fake_row.post_process_state == "deleted"

    def test_delete_phase_stays_if_still_seeding(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.remove_completed = True

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [{
            "torrent_tag": "gamarr-test",
            "torrent_hash": "abc",
            "torrent_name": "Elden Ring",
            "torrent_save_path": "/dl/Elden Ring",
            "torrent_state": "uploading",  # still seeding
            "torrent_file_list": [],
        }]

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.post_process_state = "copied"
        fake_row.post_process_copied_at = "2099-01-01T00:00:00"  # far future — won't time out
        db.find_by_tag.return_value = fake_row

        with patch("gamarr.post_processor._copied_age_hours", return_value=1):
            run_post_processing(config, qbt, db)

        qbt.delete_torrent.assert_not_called()  # still seeding, not old enough
        assert fake_row.post_process_state == "copied"  # unchanged

    def test_already_deleted_skipped(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [{
            "torrent_tag": "gamarr-done",
            "torrent_hash": "abc",
            "torrent_name": "Done Game",
            "torrent_save_path": "/dl",
            "torrent_state": "pausedUP",
            "torrent_file_list": [],
        }]

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.post_process_state = "deleted"
        db.find_by_tag.return_value = fake_row

        run_post_processing(config, qbt, db)
        qbt.delete_torrent.assert_not_called()  # already deleted
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_post_processor.py -v --tb=short 2>&1 | tail -25
```
Expected: FAIL — `ModuleNotFoundError: No module named 'gamarr.post_processor'`

- [ ] **Step 3: Write the post_processor module**

Create `src/gamarr/post_processor.py`:

```python
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
from typing import TYPE_CHECKING

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
    if min_kb and file_size_kb < min_kb:
        return True
    return False


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
) -> None:
    """Handle a single completed torrent: copy or delete based on state."""
    tag = torrent["torrent_tag"]
    torrent_hash = torrent["torrent_hash"]

    row: HistoryRow | None = db.find_by_tag(tag)
    if row is None:
        logger.warning("No history record for tag '{}'; skipping.", tag)
        return

    state = row.post_process_state

    # Already deleted — nothing to do.
    if state == "deleted":
        logger.debug("Torrent '{}' already deleted; skipping.", tag)
        return

    # Copy phase
    if state is None and config.post_process.copy_completed:
        _run_copy_phase(torrent, config, row)
        return

    # Delete phase: copied, waiting for seeding to finish
    if state == "copied" and config.post_process.remove_completed:
        _run_delete_phase(torrent, config, qbt, row)


def _run_copy_phase(
    torrent: dict,
    config: Config,
    row: HistoryRow,
) -> None:
    """Copy completed torrent files to the library."""
    pp = config.post_process

    dst_dir = _build_destination_path(
        template=pp.library_path,
        source=row.source,
        platform=row.platform,
        genres=row.genres,
        game_title=row.game_title or "Unknown",
    )
    if not dst_dir:
        logger.debug("Empty library_path; skipping copy for '{}'.", row.game_title)
        return

    # Skip if destination already exists
    if os.path.isdir(dst_dir):
        logger.info("Destination '{}' already exists; skipping '{}'.", dst_dir, row.game_title)
        return

    # Build file copy list
    src_files = _build_copy_list(torrent, pp)
    if not src_files:
        logger.debug("No files to copy for '{}'.", row.game_title)
        return

    if not make_directory(dst_dir):
        logger.error("Cannot create destination directory '{}'; skipping.", dst_dir)
        return

    all_ok = True
    for src_path in src_files:
        fname = os.path.basename(src_path)
        dst_path = os.path.join(dst_dir, fname)
        if not copy_with_verify(src_path, dst_path):
            logger.error("Copy/verify failed for '{}'; aborting.", src_path)
            all_ok = False
            break

    if all_ok:
        row.post_process_state = "copied"
        row.post_process_copied_at = datetime.datetime.now(tz=UTC).isoformat()
        logger.info("Copied '{}' to '{}'.", row.game_title, dst_dir)
    else:
        logger.warning("Copy failed for '{}'; will retry on next cycle.", row.game_title)


def _run_delete_phase(
    torrent: dict,
    config: Config,
    qbt: QBittorrentClient,
    row: HistoryRow,
) -> None:
    """Delete source torrent if seeding goal is met or timeout exceeded."""
    torrent_state = torrent.get("torrent_state", "")
    pp = config.post_process

    should_delete = torrent_state in ("pausedUP", "stoppedUP")
    if not should_delete:
        age = _copied_age_hours(row.post_process_copied_at)
        if pp.max_seed_wait_hours > 0 and age >= pp.max_seed_wait_hours:
            logger.info("Seed wait timeout ({} >= {}h) for '{}'; deleting.", age, pp.max_seed_wait_hours, row.game_title)
            should_delete = True

    if should_delete:
        qbt.delete_torrent(torrent["torrent_hash"], delete_data=True)
        row.post_process_state = "deleted"
        logger.info("Deleted torrent '{}' after post-processing.", row.game_title)
    else:
        logger.debug(
            "Torrent '{}' still seeding (state={}); waiting for seeding to finish.",
            row.game_title,
            torrent_state,
        )


def _build_copy_list(torrent: dict, pp: object) -> list[str]:
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
        rel_path = f.get("file_name") or ""
        if not rel_path:
            continue
        abs_path = os.path.join(save_path, rel_path)

        try:
            file_size = int(f.get("file_size") or 0)
        except (ValueError, TypeError):
            file_size = 0
        file_size_kb = file_size >> 10
        folder_part = os.path.dirname(rel_path)

        if _file_excluded(rel_path, folder_part, file_size_kb, file_regexes, folder_regexes, min_kb):
            continue

        result.append(abs_path)
    return result


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

    completed = qbt.list_completed()
    if not completed:
        logger.debug("No completed torrents to post-process.")
        return

    for torrent in completed:
        try:
            _process_one(torrent, config, qbt, db)
        except Exception:  # noqa: BLE001
            logger.exception("Post-processing failed for torrent '{}'.", torrent.get("torrent_tag", "unknown"))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_post_processor.py -v --tb=short 2>&1 | tail -30
```
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/post_processor.py tests/unit/test_post_processor.py
git commit -m "feat: add post_processor.py with two-phase copy-delete lifecycle"
```

---

### Task 7: Scheduler — Register Post-Processing Job

**Files:**
- Modify: `src/gamarr/scheduler.py`
- Read: `src/gamarr/scheduler.py` (understand existing `_run_daemon` pattern)

- [ ] **Step 1: Add _run_guarded helper and register post-processing job**

In `src/gamarr/scheduler.py`, add the `_run_guarded` helper and modify `_run_daemon`:

Add `_run_guarded` (after imports, before `_write_pid`):

```python
def _run_guarded(label: str, fn: Any, *args: Any) -> None:
    """Call fn(*args), logging any exception at ERROR level so one bad cycle cannot crash the scheduler."""
    try:
        fn(*args)
    except Exception:
        logger.exception("{} task failed.", label)
```

In `_run_daemon`, after the existing acquisition job registration, add:

```python
    from gamarr.post_processor import run_post_processing

    pp_cfg = config.post_process
    scheduler.add_job(
        lambda: _run_guarded("Post-processing", run_post_processing, config, qbt, db),
        trigger=IntervalTrigger(minutes=pp_cfg.schedule_time_mins),
        id="post_processing",
        name="Post-processing (copy to library)",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) if pp_cfg.run_on_start else datetime.now(UTC) + timedelta(minutes=pp_cfg.schedule_time_mins),
    )
```

- [ ] **Step 2: Wire into run_once**

In `run_once`, add after the existing acquisition call:

```python
    from gamarr.post_processor import run_post_processing
    run_post_processing(config, qbt, db)
```

- [ ] **Step 3: Run existing tests to confirm no regression**

```bash
cd /data/gamarr && uv run pytest -x -q --tb=short 2>&1 | tail -10
```
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/scheduler.py
git commit -m "feat: register post-processing job in scheduler alongside acquisition"
```

---

### Task 8: Config YAML Sample and Final Verification

**Files:**
- Modify: `configs/gamarr.yml`

- [ ] **Step 1: Add post_process section to the sample config**

Add at the end of `configs/gamarr.yml`:

```yaml
post_process:
  post_process_enabled: true
  schedule_time_mins: 5
  run_on_start: true
  library_path: ""
  copy_completed: true
  remove_completed: true
  max_seed_wait_hours: 168
  exclude_file_min_kb: 0
  exclude_file_regex_list:
    - sample
    - featurettes
  exclude_folder_regex_list:
    - subs
    - sample
    - screenshots
```

- [ ] **Step 2: Run full test suite**

```bash
cd /data/gamarr && uv run pytest -x -q --tb=short 2>&1 | tail -10
```
Expected: All tests pass.

- [ ] **Step 3: Run ruff format and check**

```bash
cd /data/gamarr && uv run ruff format src/gamarr/post_processor.py src/gamarr/file_utils.py src/gamarr/scheduler.py tests/unit/test_post_processor.py tests/unit/test_file_utils.py 2>&1
cd /data/gamarr && uv run ruff check src/gamarr/ tests/ --select=E,F 2>&1 | tail -10
```
Expected: No errors.

- [ ] **Step 4: Run mypy type check on new files**

```bash
cd /data/gamarr && uv run mypy src/gamarr/post_processor.py src/gamarr/file_utils.py 2>&1 | tail -10
```
Expected: No type errors.

- [ ] **Step 5: Commit**

```bash
git add configs/gamarr.yml
git commit -m "feat: add post_process config section to sample YAML"
```

---

### Verification Checklist

After all tasks complete, run the full verification:

```bash
cd /data/gamarr
uv run ruff format --check src/gamarr/ tests/
uv run ruff check src/gamarr/ tests/
uv run mypy src/gamarr/
uv run pytest -v --tb=short
```

All gates must pass before the feature is considered complete.
