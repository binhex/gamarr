# Library Scanning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add library scanning to gamarr — check if a game already exists on disk before looking it up on Metacritic, and skip it if found.

**Architecture:** New `LibraryScanner` class in `src/gamarr/library.py` normalizes library folder/file names and builds a lookup index. Called from the pipeline right after RSS fetch, before Metacritic lookup. Matches are recorded as `"Already owned"` in the history DB.

**Tech Stack:** Python 3.12+, os.walk, re for name normalization.

---

### Task 1: Add LibraryConfig to config model

**Files:**
- Modify: `src/gamarr/config.py`
- Modify: `configs/gamarr.yml`
- Verify: existing tests still pass

- [ ] **Step 1: Add LibraryConfig model and update root Config**

Add to `src/gamarr/config.py` (before the `Config` class):

```python
class LibraryConfig(BaseModel):
    """Game library scanning settings."""

    enabled: bool = True
    paths: list[str] = Field(default_factory=list)
```

Then add `library: LibraryConfig = Field(default_factory=LibraryConfig)` to the `Config` class (after `database`):

```python
class Config(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    metacritic: MetacriticConfig = Field(default_factory=MetacriticConfig)
    torrent_client: TorrentClientConfig = Field(default_factory=TorrentClientConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    library: LibraryConfig = Field(default_factory=LibraryConfig)
```

- [ ] **Step 2: Write a failing test for the config model**

Add to `tests/unit/test_config.py` (in `TestConfigModels`):

```python
def test_library_config_defaults(self) -> None:
    cfg = LibraryConfig()
    assert cfg.enabled is True
    assert cfg.paths == []

def test_library_in_root_config(self) -> None:
    cfg = Config()
    assert cfg.library.enabled is True
    assert cfg.library.paths == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_config.py::TestConfigModels::test_library_config_defaults -v --no-cov`
Expected: ImportError for `LibraryConfig`

- [ ] **Step 4: Implement the model**

Add `LibraryConfig` to `config.py` as described in Step 1.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_config.py -v --no-cov`
Expected: All config tests PASS

- [ ] **Step 6: Update default config YAML**

Add to `configs/gamarr.yml` (before `database:`):

```yaml
library:
  enabled: true
  paths: []
```

- [ ] **Step 7: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 8: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add LibraryConfig to config model

- Add LibraryConfig Pydantic model with enabled + paths fields
- Add library field to root Config
- Update default configs/gamarr.yml with library section
- Add test coverage for LibraryConfig defaults"
```

---

### Task 2: Create library.py module (LibraryScanner + normalization)

**Files:**
- Create: `src/gamarr/library.py`
- Create: `tests/unit/test_library.py`

- [ ] **Step 1: Write failing tests for name normalization**

Create `tests/unit/test_library.py`:

```python
"""Tests for gamarr library scanning."""

from __future__ import annotations

from pathlib import Path

import pytest

from gamarr.library import LibraryMatch, LibraryScanner, _normalise_name


class TestNormaliseName:
    """Game name normalization for cross-comparison."""

    def test_normalise_simple(self) -> None:
        assert _normalise_name("Elden Ring") == "elden ring"

    def test_normalise_with_underscores(self) -> None:
        assert _normalise_name("elden_ring_[dodi]") == "elden ring"

    def test_normalise_file_extension(self) -> None:
        assert _normalise_name("cyberpunk-2077.iso") == "cyberpunk 2077"

    def test_normalise_zip_file(self) -> None:
        assert _normalise_name("hades_ii.zip") == "hades ii"

    def test_normalise_strips_year_in_parens(self) -> None:
        assert _normalise_name("Game Name (2024)") == "game name"

    def test_normalise_mixed_punctuation(self) -> None:
        assert _normalise_name("FINAL FANTASY VII REBIRTH") == "final fantasy vii rebirth"

    def test_normalise_dotted_name(self) -> None:
        assert _normalise_name("baldurs.gate.3.iso") == "baldurs gate 3"

    def test_normalise_strips_version_suffix(self) -> None:
        assert _normalise_name("game-name_v1.0.rar") == "game name"

    def test_normalise_preserves_numeric_name(self) -> None:
        assert _normalise_name("Hades II") == "hades ii"

    def test_normalise_empty_string(self) -> None:
        assert _normalise_name("") == ""


class TestLibraryMatch:
    """LibraryMatch dataclass construction."""

    def test_library_match_creation(self) -> None:
        match = LibraryMatch(found=True, matched_name="Elden Ring", matched_path="/games/Elden Ring")
        assert match.found is True
        assert match.matched_name == "Elden Ring"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_library.py -v --no-cov`
Expected: ImportError for `gamarr.library`

- [ ] **Step 3: Create library.py with normalization and LibraryScanner**

Create `src/gamarr/library.py`:

```python
"""Library scanning for gamarr.

Scans configured library paths for existing game titles, normalizes
folder and file names, and provides fast lookups to avoid re-downloading
games already on disk.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

# Extensions to strip from filenames before normalizing
_GAME_EXTENSIONS = (
    ".iso", ".zip", ".rar", ".7z", ".exe", ".bin", ".cue",
    ".nsp", ".xci", ".nro", ".nca",
    ".tar.gz", ".tar", ".gz",
)

# Suffixes to strip during normalization: edition markers
_EDITION_SUFFIX_PATTERN = re.compile(
    r"\s*(?:"
    r"\(v?\d[\d.]*[^)]*\)"       # (v1.0 + DLCs)
    r"|\[Repack\]"                 # [Repack]
    r"|\(?\d{4}\)?"               # (2024) — release year
    r"|-\s*(?:Digital\s+)?Deluxe\s+Edition"
    r"|-\s*Complete\s+Edition"
    r"|-\s*Game\s+of\s+the\s+Year\s+Edition"
    r"|-\s*Gold\s+Edition"
    r"|-\s*Ultimate\s+Edition"
    r"|-\s*Premium\s+Edition"
    r"|-\s*GOTY"
    r"|-\s*Standard\s+Edition"
    r")",
    re.IGNORECASE,
)


@dataclass
class LibraryMatch:
    """Result of a library scan for a single game title."""

    found: bool
    matched_name: str
    matched_path: str


def _strip_extension(name: str) -> str:
    """Strip known game file extensions from *name*."""
    lower = name.lower()
    for ext in _GAME_EXTENSIONS:
        if lower.endswith(ext):
            return name[: -len(ext)]
        if lower.endswith(ext + ".part") or lower.endswith(ext + ".001"):
            return name[: -(len(ext) + 5)]
    return name


def _normalise_name(name: str) -> str:
    """Normalize a game name for cross-comparison.

    1. Strip file extensions
    2. Replace underscores, dots, hyphens with spaces
    3. Lowercase
    4. Strip punctuation (keep spaces and alphanumeric)
    5. Strip common suffixes (years, edition markers)
    6. Collapse whitespace, strip leading/trailing spaces
    """
    if not name:
        return ""
    name = _strip_extension(name)
    name = re.sub(r"[._-]", " ", name)
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = _EDITION_SUFFIX_PATTERN.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _partial_match(entry_norm: str, index_key: str) -> bool:
    """Return True if *entry_norm* and *index_key* partially match.

    One must be a substring of the other with a minimum length of 5
    characters and at least 60% of the shorter string matching.
    """
    if len(entry_norm) < 5 or len(index_key) < 5:
        return False
    if entry_norm in index_key or index_key in entry_norm:
        shorter = min(len(entry_norm), len(index_key))
        longer = max(len(entry_norm), len(index_key))
        if shorter / longer >= 0.6:
            return True
    return False


class LibraryScanner:
    """Scans configured library paths for existing game titles.

    Walks all paths once at construction, normalizes folder and file
    names, and builds an index for fast lookups.

    Args:
        library_paths: List of root directories to scan.
        enabled: When False, ``check_game()`` always returns ``None``.
    """

    def __init__(
        self,
        library_paths: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._paths = library_paths or []
        self._index: dict[str, list[str]] = {}  # norm_name → [full_paths]
        if enabled and self._paths:
            self._build_index()

    # ------------------------------------------------------------------
    # Index building
    # ------------------------------------------------------------------

    def _build_index(self) -> None:
        """Walk all library paths and index normalized directory/file names."""
        for lib_path in self._paths:
            if not os.path.isdir(lib_path):
                logger.warning("Library path '{}' does not exist or is not a directory.", lib_path)
                continue
            for root, dirs, files in os.walk(lib_path):
                for dir_name in dirs:
                    norm = _normalise_name(dir_name)
                    full = os.path.join(root, dir_name)
                    self._index.setdefault(norm, []).append(full)
                for file_name in files:
                    norm = _normalise_name(file_name)
                    if norm:
                        full = os.path.join(root, file_name)
                        self._index.setdefault(norm, []).append(full)
        logger.debug("Library index built: {} entries from {} path(s)", len(self._index), len(self._paths))

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def check_game(self, title: str) -> LibraryMatch | None:
        """Return a :class:`LibraryMatch` if *title* is found in the library.

        Performs exact normalized match first, then partial substring matching.

        Args:
            title: Cleaned game title (e.g. ``"Elden Ring"``).

        Returns:
            A :class:`LibraryMatch` with the first match, or ``None``.
        """
        if not self._enabled or not self._index:
            return None

        norm = _normalise_name(title)
        if not norm:
            return None

        # 1. Exact normalized match
        if norm in self._index:
            paths = self._index[norm]
            return LibraryMatch(found=True, matched_name=norm, matched_path=paths[0])

        # 2. Partial substring match
        for index_key, paths in self._index.items():
            if _partial_match(norm, index_key):
                return LibraryMatch(found=True, matched_name=index_key, matched_path=paths[0])

        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_library.py -v --no-cov`
Expected: All tests PASS

- [ ] **Step 5: Add LibraryScanner construction and matching tests**

Append to `tests/unit/test_library.py`:

```python
class TestLibraryScanner:
    """LibraryScanner index building and game lookup."""

    def test_scanner_disabled(self) -> None:
        scanner = LibraryScanner(enabled=False)
        assert scanner.check_game("Elden Ring") is None

    def test_scanner_empty_paths(self) -> None:
        scanner = LibraryScanner([])
        assert scanner.check_game("Elden Ring") is None

    def test_scanner_no_index_when_disabled(self) -> None:
        scanner = LibraryScanner(["/nonexistent"], enabled=False)
        assert scanner._index == {}

    def test_check_game_exact_match(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Elden Ring"
        game_dir.mkdir()
        scanner = LibraryScanner([str(tmp_path)])
        match = scanner.check_game("Elden Ring")
        assert match is not None
        assert match.found is True
        assert match.matched_name == "elden ring"

    def test_check_game_not_found(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Some Game"
        game_dir.mkdir()
        scanner = LibraryScanner([str(tmp_path)])
        match = scanner.check_game("Elden Ring")
        assert match is None

    def test_check_game_partial_match(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Elden Ring Deluxe Edition"
        game_dir.mkdir()
        scanner = LibraryScanner([str(tmp_path)])
        match = scanner.check_game("Elden Ring")
        assert match is not None
        assert match.found is True

    def test_check_game_partial_reverse(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Elden Ring"
        game_dir.mkdir()
        scanner = LibraryScanner([str(tmp_path)])
        match = scanner.check_game("Elden Ring Deluxe Edition")
        assert match is not None
        assert match.found is True

    def test_mixed_structure_dirs_and_files(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Hades II"
        game_dir.mkdir()
        # Also create a file in the root that matches another game
        (tmp_path / "cyberpunk-2077.iso").write_text("")
        scanner = LibraryScanner([str(tmp_path)])
        assert scanner.check_game("Hades II") is not None
        assert scanner.check_game("Cyberpunk 2077") is not None
```

- [ ] **Step 6: Run all library tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_library.py -v --no-cov`
Expected: All tests PASS (11 normalise + 1 match + 8 scanner = 20 tests)

- [ ] **Step 7: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 8: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: add LibraryScanner module for game library detection

- LibraryScanner with os.walk-based index building
- _normalise_name for cross-comparison of game names
- _partial_match for substring matching with similarity guard
- LibraryMatch dataclass for scan results
- Full test coverage: normalization, matching, edge cases"
```

---

### Task 3: Update Database get_stats with already_owned count

**Files:**
- Modify: `src/gamarr/database.py`
- Modify: `tests/unit/test_database.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_database.py` in `TestDatabase`:

```python
def test_get_stats_counts_already_owned(self, tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    db.record_processed(source="fitgirl", source_title="A", result="Passed")
    db.record_processed(source="fitgirl", source_title="B", result="Already owned")
    db.record_processed(source="fitgirl", source_title="C", result="Already owned")
    stats = db.get_stats()
    assert stats["total"] == 3
    assert stats["passed"] == 1
    assert stats["already_owned"] == 2
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_database.py::TestDatabase::test_get_stats_counts_already_owned -v --no-cov`
Expected: FAIL — `KeyError: 'already_owned'`

- [ ] **Step 3: Update get_stats()**

In `src/gamarr/database.py`, update `get_stats()`:

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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_database.py -v --no-cov`
Expected: All database tests PASS

- [ ] **Step 5: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 6: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: update get_stats to count already_owned entries

- Add 'already_owned' key to get_stats return dict
- Query filters HistoryRow.result == 'Already owned'
- Test verifies counts for all three result types"
```

---

### Task 4: Integrate library check into pipeline and scheduler

**Files:**
- Modify: `src/gamarr/pipeline.py`
- Modify: `src/gamarr/scheduler.py`
- Modify: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write failing tests for pipeline library integration**

Add to `tests/unit/test_pipeline.py`:

```python
class TestPipelineLibraryCheck:
    """Library check integration in the acquisition pipeline."""

    def test_library_match_skips_mc_lookup(self) -> None:
        """When a game is found in the library, MC lookup should NOT be called."""
        import types
        from gamarr.models import GameEntry

        entry = GameEntry(
            title="Elden Ring", source_title="Elden Ring [Repack]",
            source="fitgirl", platform="pc",
            magnet_url="magnet:?xt=urn:btih:abc",
            source_url="http://example.com/elden-ring",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.pipeline.os.path.isdir") as mock_isdir,
            patch("gamarr.pipeline.os.walk") as mock_walk,
        ):
            mock_isdir.return_value = True
            mock_walk.return_value = [("/games", ["Elden Ring"], [])]

            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc", qbt_host="localhost", qbt_port=8080,
                library_paths=["/games"], library_enabled=True,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Already owned"
            # MC should NOT be called since library match was found
            mock_mc_cls.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py::TestPipelineLibraryCheck -v --no-cov --tb=short`
Expected: FAIL — `run_acquisition()` doesn't accept `library_paths`/`library_enabled` params yet

- [ ] **Step 3: Update pipeline.py**

Add the library check to `run_acquisition()`. Add `library_paths` and `library_enabled` parameters, and insert the library check after entries are fetched, before the Metacritic lookup loop:

```python
def run_acquisition(
    *,
    ...existing params...,
    library_paths: list[str] | None = None,
    library_enabled: bool = True,
) -> list[dict[str, Any]]:
```

In the function body, after `entries = source.fetch_new()` and before the processing loop, add:

```python
    from gamarr.library import LibraryScanner

    library = LibraryScanner(library_paths, enabled=library_enabled)
```

Then change the loop to check the library first:

```python
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

        result = _process_entry(entry, cfg, mc, qbt, db, notifier)
        results.append(result)
```

- [ ] **Step 4: Update scheduler.py**

In `_build_kwargs()`, add:

```python
"library_paths": config.library.paths,
"library_enabled": config.library.enabled,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py -v --no-cov`
Expected: All pipeline tests PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /data/gamarr && timeout 30 uv run pytest --no-cov -q`
Expected: All tests PASS (should be ~175-180 tests)

- [ ] **Step 7: Run lint + type check**

Run: `cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: All clean

- [ ] **Step 8: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat: integrate library scanning into acquisition pipeline

- Add library_paths/library_enabled params to run_acquisition()
- Check library before Metacritic lookup, skip if game found
- Extract library config in scheduler _build_kwargs()
- Test verifies library match prevents MC lookup"
```

---

## Spec Coverage Check

| Spec Section | Task Implementing It |
|---|---|
| 3. LibraryScanner class | Task 2 (library.py) |
| 3.1 LibraryMatch dataclass | Task 2 (library.py) |
| 3.2 Name normalization | Task 2 (library.py, _normalise_name) |
| 3.3 Index building | Task 2 (library.py, _build_index) |
| 3.4 Matching strategy | Task 2 (library.py, check_game + _partial_match) |
| 4. Config model | Task 1 (config.py, LibraryConfig) |
| 5. Pipeline integration | Task 4 (pipeline.py + scheduler.py) |
| 6. History recording | Task 3 (database.py get_stats) |
| 8. File layout | All tasks |
| 9. Test strategy | Tasks 1-4 (test files) |
