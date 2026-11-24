# Post-Processing — Move Completed Downloads to Library

**Date:** 2026-07-14
**Status:** Draft

## Problem

gamarr downloads game torrents to qBittorrent but never moves completed downloads
to a library.  The user must manually find, verify, and organise downloaded games.
There is no post-processing pipeline — once a torrent is added to qBittorrent,
gamarr never touches it again.

## Design

A separate APScheduler thread polls qBittorrent for completed downloads, copies
them (with SHA-256 verification) to a configurable library path using a template
format, then cleans up the source torrent after seeding goals are met.  The
implementation mirrors movarr's post-processing pattern throughout.

### Config

New top-level `post_process` section — no changes to existing config keys:

```yaml
post_process:
  post_process_enabled: true
  schedule_time_mins: 5
  run_on_start: true
  library_path: "/data/library/{site}/{platform}/{genre}/{title}"
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

**Pydantic model** (`config.py` — new `PostProcessConfig`):

| Field | Type | Default | Description |
|---|---|---|---|
| `post_process_enabled` | `bool` | `True` | Master toggle |
| `schedule_time_mins` | `int` (>0) | `5` | Polling interval |
| `run_on_start` | `bool` | `True` | Run immediately on daemon start |
| `library_path` | `str` | `""` | Destination path template. Empty = no-op |
| `copy_completed` | `bool` | `True` | Copy files to library |
| `remove_completed` | `bool` | `True` | Delete torrent + data after seeding goals met |
| `max_seed_wait_hours` | `int` (>=0) | `168` | Fallback: delete after this many hours even if still seeding. 0 = never force-delete |
| `exclude_file_min_kb` | `int` | `0` | Skip files smaller than this (0 = no minimum) |
| `exclude_file_regex_list` | `list[str]` | `[]` | Case-insensitive regex patterns to exclude files |
| `exclude_folder_regex_list` | `list[str]` | `[]` | Case-insensitive regex patterns to exclude folders |

**Template variables** in `library_path`:

| Variable | Source | Example |
|---|---|---|
| `{site}` | `HistoryRow.source` | `"fitgirl"`, `"freegog"` |
| `{platform}` | `HistoryRow.platform` | `"pc"` |
| `{genre}` | First genre from `HistoryRow.genres` (comma-separated) | `"Action"` |
| `{title}` | `HistoryRow.game_title` (Metacritic canonical name) | `"Elden Ring"` |

All template values are filesystem-sanitized: characters `/\<>:"|?*` and `..` are
stripped from each component.  Empty/unavailable values (e.g. no genre) default
to `"Unknown"`.

**Config migration:** The `post_process` section uses Pydantic defaults for
existing configs that lack it.  `library_path` defaults to `""` — an empty path
means post-processing runs but copies nothing (safe default).  No config migration
function needed.

---

### Database

Three additive changes to the `history` table — no existing columns modified:

**New columns:**

```sql
ALTER TABLE history ADD COLUMN genres VARCHAR;
ALTER TABLE history ADD COLUMN post_process_state VARCHAR;
ALTER TABLE history ADD COLUMN post_process_copied_at VARCHAR;
```

| Column | Type | Values | Description |
|---|---|---|---|
| `genres` | `VARCHAR` (nullable) | Comma-separated genre string | Populated at delivery time from the pending game record. Used for `{genre}` template. |
| `post_process_state` | `VARCHAR` (nullable) | `NULL`, `"copied"`, `"deleted"` | Tracks the two-phase lifecycle. `NULL` = not yet processed. |
| `post_process_copied_at` | `VARCHAR` (nullable) | ISO-8601 timestamp | Set when state transitions to `"copied"`. Used for `max_seed_wait_hours` fallback. |

**Migration:** `Database.__init__()` auto-detects missing columns via
`inspector.get_columns("history")` and adds them with `ALTER TABLE ... ADD COLUMN`.

**New query method:** `find_by_tag(tag: str) → HistoryRow | None` — looks up
a history row by `torrent_tag`.

**Write path:** `record_history()` (called at delivery time in the pipeline)
accepts a new optional `genres: str | None` parameter.

---

### QBittorrentClient

Two new methods on the existing client:

**`list_completed() → list[dict]`** — Queries all torrents in the configured
category (`games-gamarr`), filters to those with a `gamarr-` prefixed tag AND
`amount_left == 0` (100% downloaded).  For each, fetches the file list and save
path.  Returns:

```python
[{
    "torrent_tag": "gamarr-a1b2c3d4",
    "torrent_hash": "abc123...",
    "torrent_name": "Elden Ring [FitGirl Repack]",
    "torrent_save_path": "/downloads/Elden Ring [FitGirl Repack]",
    "torrent_state": "uploading",        # raw qBittorrent state string
    "torrent_file_list": [
        {"file_name": "setup.exe", "file_size": 123456},
        ...
    ],
}]
```

Completion check mirrors movarr exactly: `amount_left == 0` only.  No status
filter — actively seeding torrents (state `uploading`/`stalledUP`) must be
discovered for the copy phase.

**`delete_torrent(hash: str, delete_data: bool) → None`** — Wraps
`torrents_delete(delete_files=delete_data, torrent_hashes=hash)`.  Called with
`delete_data=True` to remove both the torrent entry and its downloaded files.

---

### file_utils.py

New module mirroring movarr's `file_utils.py`.  Two public functions:

- **`copy_with_verify(src: str | Path, dst: str | Path) → bool`** — SHA-256 copy
  with pre- and post-verification.  If `dst` already exists and checksums match,
  the copy is skipped.  If checksums differ, `dst` is deleted and re-copied.
  After copy, checksums are compared again.  Progress is logged at 25/50/75%
  milestones for large files.  Returns `True` only when `dst` exists with the
  correct checksum.

- **`make_directory(path: str | Path) → bool`** — Create `path` and all parents.
  Returns `True` on success.

Internal: `_sha256(path)` streams the file in 64 KiB chunks.

---

### post_processor.py

New module — the core post-processing orchestrator.  One public entry point:

**`run_post_processing(config: Config, qbt: QBittorrentClient, db: Database) → None`**

Flow:

1. **Guard:** If `post_process_enabled` is `False`, return immediately.
2. **Health check:** If `qbt.is_connected()` is `False`, log warning and return.
3. **Poll:** Call `qbt.list_completed()`.
4. **For each torrent:**

   a. Look up `db.find_by_tag(tag)`.  Skip if no matching history row.

   b. **Copy phase** (`post_process_state` is `NULL` and `copy_completed` is `True`):
      - If `copy_completed` is `False`, skip copy and transition directly to checking delete conditions.
      - (rest same as before)
      - Build destination path from `library_path` template.
      - If destination folder already exists, skip (log info).
      - Build file copy list from `torrent_file_list`, applying exclusion rules
        (min KB threshold, file regex, folder regex).
      - Create destination directory via `make_directory()`.
      - Copy each file via `copy_with_verify()`.  On any failure, abort this
        torrent (do not mark as copied).
      - On success, set `post_process_state = "copied"` and `post_process_copied_at` to the current ISO-8601 timestamp.

   c. **Delete phase** (`post_process_state` is `"copied"`):
      - Check if the torrent's qBittorrent state is `pausedUP` or `stoppedUP`
        (seeding goal met → qBittorrent has paused/stopped it).
      - **OR** if `copied_age > max_seed_wait_hours` (fallback timeout).
      - If either condition met, call `qbt.delete_torrent(hash, delete_data=True)`,
        then set `post_process_state = "deleted"`.

   d. **Already deleted** (`post_process_state` is `"deleted"`): skip.

**Path building** — resolves the `library_path` template:

```python
# Example: library_path = "/data/library/{site}/{platform}/{genre}/{title}"
# DB record: source="fitgirl", platform="pc", genres="Action, RPG", game_title="Elden Ring"

resolved = "/data/library/fitgirl/pc/Action/Elden Ring"
```

Each component is sanitized via `_safe_path_component()` (strips
`/\<>:"|?*` and `..`). Missing values default to `"Unknown"`.

**File exclusion** — mirrors movarr's `_build_copy_list()`:
- Files whose size in KB is below `exclude_file_min_kb` are skipped.
- Files matching any pattern in `exclude_file_regex_list` (case-insensitive,
  matched against the full relative path) are skipped.
- Files whose parent folder matches any pattern in `exclude_folder_regex_list`
  are skipped.

**Skip-if-dest-exists:** Before any copy, check if the resolved destination
directory already exists on disk.  If yes, log info and skip the entire
torrent — the game is already in the library.

**Error handling:** Exceptions within `run_post_processing` are caught at the
scheduler level (via `_run_guarded`), so one failed run does not kill the
post-processing thread.

---

### Scheduler

`scheduler.py` registers a second recurring APScheduler job alongside acquisition:

```python
scheduler.add_job(
    lambda: _run_guarded("Post-processing", run_post_processing, config, qbt, db),
    trigger=IntervalTrigger(minutes=config.post_process.schedule_time_mins),
    id="post_processing",
    max_instances=1,
    coalesce=True,
    next_run_time=datetime.now(UTC) if run_on_start else ...,
)
```

The acquisition job is unchanged.  `run_once()` (single-pass mode) also calls
`run_post_processing()` after acquisition completes.

**`_run_guarded`** — catches all exceptions and logs them at ERROR level, so
a bad cycle cannot crash the scheduler.

---

### Files Changed / Added

| File | Action | Description |
|---|---|---|
| `src/gamarr/config.py` | Edit | Add `PostProcessConfig` model and `post_process` field on `Config` |
| `src/gamarr/database.py` | Edit | Add `genres` + `post_process_state` columns to `HistoryRow`; add `find_by_tag()` method; add `genres` param to `record_history()` |
| `src/gamarr/qbittorrent.py` | Edit | Add `list_completed()` and `delete_torrent()` methods |
| `src/gamarr/pipeline.py` | Edit | Pass `genres` to `record_history()` at delivery time |
| `src/gamarr/scheduler.py` | Edit | Register post-processing job; add `_run_guarded()` helper; wire into `run_once()` |
| `src/gamarr/file_utils.py` | **New** | `copy_with_verify()`, `make_directory()`, internal `_sha256()` |
| `src/gamarr/post_processor.py` | **New** | `run_post_processing()`, path building, file exclusion, two-phase lifecycle |
| `configs/gamarr.yml` | Edit | Add `post_process` section to sample config |
| `tests/unit/test_file_utils.py` | **New** | Tests for `copy_with_verify` and `make_directory` |
| `tests/unit/test_post_processor.py` | **New** | Tests for path building, exclusion logic, two-phase state machine |
| `tests/unit/test_qbittorrent.py` | Edit | Tests for `list_completed` and `delete_torrent` |
| `tests/unit/test_database.py` | Edit | Tests for `find_by_tag`, new columns, `record_history` with genres |

### Testing Strategy

- **`test_file_utils.py`:** Unit tests for `copy_with_verify` — mocks filesystem
  and hash operations.  Tests: fresh copy, skip on checksum match, re-copy on
  mismatch, cleanup on failure, directory creation.

- **`test_post_processor.py`:** Unit tests for `run_post_processing` — mocks
  qBittorrent, database, and filesystem.  Tests: disabled toggle, unreachable
  qBittorrent, copy phase success/failure, delete phase (paused state, timeout
  fallback), template path resolution, file exclusion rules, skip-if-dest-exists,
  missing history row.

- **`test_qbittorrent.py`:** Unit tests for `list_completed` — mocks
  `torrents_info`, `torrents_files`, `torrents_properties`.  Tests: empty
  category, non-gamarr tags, `amount_left != 0` skip, completed with files.

- **`test_database.py`:** Unit tests for `find_by_tag`, `record_history` with
  genres param, column migration.
