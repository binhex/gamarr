# gamarr — Library Scanning Design

**Date:** 2026-06-05
**Status:** Draft
**Version:** 1.0.0

## 1. Overview

gamarr currently has no awareness of what games already exist on disk. If a game is
already in the user's library, gamarr will still look it up on Metacritic and send it
to qBittorrent — wasting API calls and potentially re-downloading owned content.

This feature adds a **library scanning** step that checks whether a game title from
the current source (FitGirl RSS) already exists in a configured library directory.
If found, the entry is skipped and recorded as "Already owned" in the history database.

## 2. Architecture

```
pipeline.py
    │
    │  1. Fetch FitGirl RSS → GameEntry list
    │  2. For each entry:
    │     │
    │     ├── library.py: LibraryScanner.check_game(entry.title)
    │     │     ├─ Normalize entry title (strip punctuation, lowercase, collapse)
    │     │     ├─ Compare against pre-built index of library names
    │     │     ├─ [MATCH] → record "Already owned" in DB, skip entry
    │     │     └─ [NO MATCH] → continue to Metacritic lookup
    │     │
    │     ├── (existing) Metacritic score lookup
    │     └── (existing) qBittorrent delivery
    │
    config.py: + LibraryConfig with paths list
```

### 2.1 Pipeline Flow Change

The existing `run_acquisition()` function gains a library check step between RSS
fetching and Metacritic lookup:

```
Fetch RSS → Library check → MC lookup → Score eval → qBittorrent
              │                  │
              ▼                  ▼
        "Already owned"    (unchanged)
        skip + record
```

### 2.2 When the Check Runs

The library check runs **once per acquisition cycle**, right after entries are fetched
from the RSS feed. The `LibraryScanner` builds its index once at construction time
and reuses it for all entries in the same cycle.

## 3. LibraryScanner (`src/gamarr/library.py`)

### 3.1 Class Interface

```python
@dataclass
class LibraryMatch:
    """Result of a library scan for a single game title."""

    found: bool
    matched_name: str       # The folder or filename that matched
    matched_path: str       # Full path to the matched item


class LibraryScanner:
    """Scans configured library paths for existing game titles.

    Walks all paths once at construction, normalizes all folder and file
    names, and builds an index for fast lookups.

    Args:
        library_paths: List of root directories to scan.
        enabled: When False, ``check_game()`` always returns ``None``.
    """

    def __init__(self, library_paths: list[str], enabled: bool = True) -> None:
        ...

    def check_game(self, title: str) -> LibraryMatch | None:
        """Return the first matching :class:`LibraryMatch`, or ``None``."""
        ...
```

### 3.2 Normalization (`_normalise_name`)

Both library names and entry titles go through the same normalization pipeline
to produce comparable strings:

```python
def _normalise_name(name: str) -> str:
    """Normalize a game name for cross-comparison.

    1. Strip file extensions (for filenames — .iso, .zip, .rar, .7z,
       .exe, .bin, .cue, .nsp, .xci, .tar.gz, etc.)
    2. Replace underscores, dots, and hyphens with spaces
    3. Lowercase
    4. Strip punctuation (except spaces)
    5. Collapse consecutive whitespace
    6. Strip common suffixes: release years (1958-2035),
       edition markers (deluxe, ultimate, goty)
    7. Strip leading/trailing whitespace
    """
```

Examples:

| Input | Normalized |
|---|---|
| `Elden_Ring_[DODI]` | `elden ring` |
| `Elden Ring (v1.12 + DLCs) [Repack]` | `elden ring` |
| `Hades II` | `hades ii` |
| `cyberpunk-2077.rar` | `cyberpunk 2077` |
| `FINAL FANTASY VII REBIRTH` | `final fantasy vii rebirth` |
| `baldurs.gate.3.iso` | `baldurs gate 3` |
| `game-name_v1.0.rar` | `game name` |

### 3.3 Index Building

At construction time, `LibraryScanner` walks all configured paths using
``os.walk()`` (same pattern as movarr's ``walk_library``). For each
directory and file encountered:

1. **Directories**: The basename is normalized and added to the index.
2. **Files**: The filename (without extension) is normalized and added.
   Debug log: "Indexed library item: {normalized} -> {full_path}"

The index is a ``dict[str, list[str]]`` mapping normalized names to one
or more full paths (a game might appear in multiple library folders).
Duplicates are expected and harmless.

### 3.4 Matching Strategy

`check_game(title)` performs two matching passes:

1. **Exact normalized match**: Normalize the entry title and look it up in
   the index. If found, return the first match.

2. **Partial match**: Check if the normalized entry title appears as a
   substring of any index key, or vice versa. This catches cases like:
   - Entry title "Elden Ring" vs library folder "Elden Ring Deluxe Edition"
   - Entry title "Assassin's Creed Valhalla Complete Edition" vs library
     folder "Assassin's Creed Valhalla"

   To avoid false positives, partial matches require a minimum length of
   5 characters and at least 60% of the shorter string matching.

Returns the first `LibraryMatch` found, or `None` if no match exists.

## 4. Config Model

### 4.1 New Pydantic Model

Add to `src/gamarr/config.py`:

```python
class LibraryConfig(BaseModel):
    """Game library scanning settings."""

    enabled: bool = True
    paths: list[str] = Field(default_factory=list)
```

### 4.2 Root Config Update

```python
class Config(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    metacritic: MetacriticConfig = Field(default_factory=MetacriticConfig)
    torrent_client: TorrentClientConfig = Field(default_factory=TorrentClientConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    library: LibraryConfig = Field(default_factory=LibraryConfig)  # NEW
```

### 4.3 Default YAML

```yaml
library:
  enabled: true
  paths: []
```

An empty `paths` list means the library check is a no-op (no paths to scan).

## 5. Pipeline Integration

### 5.1 Changes to `run_acquisition()`

The function signature gains `library_paths` and `library_enabled` parameters:

```python
def run_acquisition(
    *,
    ...existing params...,
    library_paths: list[str] | None = None,
    library_enabled: bool = True,
) -> list[dict[str, Any]]:
```

Inside the function, between the source fetch and Metacritic lookup:

```python
# --- Library check ---
library = LibraryScanner(library_paths or [], enabled=library_enabled)

entries = source.fetch_new()
results: list[dict[str, Any]] = []

for entry in entries:
    match = library.check_game(entry.title)
    if match:
        db.record_processed(
            source=entry.source,
            source_title=entry.source_url,
            source_url=entry.source_url,
            game_title=entry.title,
            platform=entry.platform,
            result="Already owned",
            result_details=f"Found in library: {match.matched_path}",
        )
        logger.info("Already in library, skipping: '{}'", entry.title)
        results.append({
            "result": "Already owned",
            "game_title": entry.title,
            "result_details": f"Found in library: {match.matched_path}",
        })
        continue

    # --- Existing pipeline (Metacritic → score → qBittorrent) ---
    mc_result = mc.lookup_game(...)
    ...
```

### 5.2 Scheduler Changes

`_build_kwargs()` in `scheduler.py` extracts the new config fields:

```python
"library_paths": config.library.paths,
"library_enabled": config.library.enabled,
```

## 6. History Recording

Library matches are recorded using the existing `record_processed()` method
with `result="Already owned"`. This uses the same `HistoryRow` model; no
schema changes are needed.

The `get_stats()` method will be updated to include an "already_owned" count:

```python
def get_stats(self) -> dict[str, Any]:
    with self._session() as session:
        total = session.query(HistoryRow).count()
        passed = session.query(HistoryRow).filter(HistoryRow.result == "Passed").count()
        failed = session.query(HistoryRow).filter(HistoryRow.result == "Failed").count()
        already_owned = session.query(HistoryRow).filter(
            HistoryRow.result == "Already owned"
        ).count()
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "already_owned": already_owned,
        }
```

## 7. CLI

No new CLI flags for V1 — library paths are configured entirely via
`configs/gamarr.yml`. The `--library-path` flag can be added in a future
release if needed.

## 8. File Layout

```
src/gamarr/
├── library.py        # NEW — LibraryScanner + name normalization
├── pipeline.py       # MODIFIED — add library check step
├── config.py         # MODIFIED — add LibraryConfig
├── scheduler.py      # MODIFIED — extract library config in _build_kwargs
├── database.py       # MODIFIED — update get_stats() with already_owned
└── ...

tests/unit/
├── test_library.py   # NEW — LibraryScanner tests
├── test_pipeline.py  # MODIFIED — add library check tests
└── ...
```

## 9. Test Strategy

### 9.1 Unit Tests for `library.py`

| Test | Scenario |
|---|---|
| `test_normalise_simple` | "Elden Ring" → "elden ring" |
| `test_normalise_with_underscores` | "elden_ring_[dodi]" → "elden ring" |
| `test_normalise_file_extension` | "cyberpunk-2077.iso" → "cyberpunk 2077" |
| `test_normalise_strips_year` | "Game Name (2024)" → "game name" |
| `test_scanner_creates_index` | Scanner builds index from paths |
| `test_check_game_found` | Exact match returns LibraryMatch |
| `test_check_game_not_found` | No match returns None |
| `test_check_game_partial_match` | Partial match works |
| `test_check_game_disabled` | When enabled=False, returns None |
| `test_scanner_empty_paths` | No paths = no index = no matches |
| `test_mixed_structure` | Both directories and files match |

### 9.2 Integration Tests for `pipeline.py`

| Test | Scenario |
|---|---|
| `test_library_match_skips_mc` | Entry found in library skips MC lookup |
| `test_library_match_records_owned` | Match records "Already owned" result |
| `test_library_no_match_normal_flow` | No match → normal pipeline |
| `test_library_disabled_normal_flow` | Library disabled → normal pipeline |

## 10. YAGNI Decisions

| Not included | Rationale |
|---|---|
| CLI `--library-path` flag | Can be added later; config-only is sufficient for V1 |
| Fuzzy/Levenshtein matching | Normalized comparison + partial matching covers practical cases without the complexity |
| Watch/inotify for live library updates | Library changes between cycles are picked up on next scan |
| Multi-platform library paths | `paths` is a list — multiple platforms are just more entries |
