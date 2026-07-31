# Backlog Progress Tracking & Cycles-Remaining Display — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track per-year page progress in the database so each scheduler cycle resumes scanning from where the last cycle left off, and display a "cycles remaining" counter at the end of each cycle.

**Architecture:** Add a `backlog_progress` ORM table to `database.py` with per-year page tracking. Replace the hardcoded `start_page=1` in `pipeline.py` with the persisted value. Compute and display backlog progress (pages scanned / total, percentage, cycles remaining) in Phase 5.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 ORM, Loguru

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Modify | `src/gamarr/database.py` | New `BacklogProgress` table + get/set/clear/reset API |
| Modify | `src/gamarr/pipeline.py` | Replace `start_page=1`, fix year range, add cycles-remaining log |
| Modify | `tests/unit/test_database.py` | Tests for backlog_progress CRUD |
| Modify | `tests/unit/test_pipeline.py` | Tests for start_page advancement and cycles-remaining display |

---

### Task 1: Database — BacklogProgress ORM table + methods

**Files:**
- Modify: `src/gamarr/database.py:108-121`

- [ ] **Step 1: Add BacklogProgress ORM class**

Insert after `ScanState` (line 121):

```python
class BacklogProgress(Base):
    """Tracks per-year backlog scan progress for each platform.

    ``year = 0`` is the sentinel for ``sort_order = "metascore"`` (no year
    dimension — all games sorted by score).

    ``year = N`` (e.g. 2026) is for ``sort_order = "new"`` (year-specific
    browse pages).
    """

    __tablename__ = "backlog_progress"

    platform: Mapped[str] = mapped_column(String, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_scanned_page: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 2: Add Database methods**

Inside the `Database` class, add after `set_last_sort_order` (~line 328):

```python
def get_last_scanned_page(self, platform: str, year: int) -> int:
    """Return the last scanned page for *platform* and *year*, or 0."""
    with self._session() as session:
        row = session.get(BacklogProgress, (platform, year))
    return row.last_scanned_page if row else 0

def set_last_scanned_page(self, platform: str, year: int, page: int) -> None:
    """Store or update the last scanned page for *platform* and *year*."""
    with self._session() as session:
        row = session.get(BacklogProgress, (platform, year))
        if row is None:
            session.add(BacklogProgress(platform=platform, year=year, last_scanned_page=page))
        else:
            row.last_scanned_page = page
        session.commit()

def reset_backlog_progress(self, platform: str, sort_order: str) -> None:
    """Reset backlog progress for *platform*.

    For ``sort_order == "new"``, resets all year rows.  For
    ``sort_order == "metascore"``, resets only ``year=0``.
    Called when the sort_order changes (alongside the existing
    metacritic cache clear) or when the user explicitly resets.
    """
    with self._session() as session:
        if sort_order == "metascore":
            row = session.get(BacklogProgress, (platform, 0))
            if row is not None:
                session.delete(row)
        else:
            session.query(BacklogProgress).filter(
                BacklogProgress.platform == platform
            ).delete()
        session.commit()

def sum_scanned_pages(self, platform: str, from_year: int, to_year: int) -> int:
    """Return total pages scanned across years [*from_year*, *to_year*] inclusive.

    Used to compute cycles remaining.  For sort_order == "metascore"
    callers should pass ``from_year=0, to_year=0``, which sums just
    the year=0 sentinel row.
    """
    with self._session() as session:
        result = (
            session.query(func.coalesce(func.sum(BacklogProgress.last_scanned_page), 0))
            .filter(
                BacklogProgress.platform == platform,
                BacklogProgress.year >= from_year,
                BacklogProgress.year <= to_year,
            )
            .scalar()
        )
    return int(result)
```

Add the import at the top of `database.py` alongside the existing `func` import (if not already there):

```python
from sqlalchemy import func
```

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/database.py
git commit -m "feat: add BacklogProgress table and CRUD methods"
```

---

### Task 2: Pipeline — Per-year page advancement

**Files:**
- Modify: `src/gamarr/pipeline.py:278-290`

- [ ] **Step 1: Add import for math module**

Add `import math` at the top of `pipeline.py` alongside the other standard library imports.

- [ ] **Step 2: Replace the year-scanning block (lines 278-290)**

The current block:

```python
cutoff_year = scan_year
current_year = datetime.datetime.now(tz=datetime.UTC).year
for scan_year in range(cutoff_year, current_year + 1):
    year_games = mc.scan_recent_games(
        platform,
        cache_pages_hours=cfg.cache_pages_hours,
        cutoff_date=cutoff_date,
        cancel_event=cancel_event,
        start_page=1,
        show_progress=True,
        year=scan_year,
        max_pages=cfg.max_cycle_pages if cfg.max_cycle_pages else (cfg.max_pages if cfg.max_pages else 0),
    )
    browse_games.extend(year_games)
```

Replace with:

```python
cutoff_year: int
current_year: int
if cfg.sort_order == "new":
    years_back = max(0, math.ceil(cfg.max_pages / 52))
    cutoff_year = datetime.datetime.now(tz=datetime.UTC).year - years_back
    current_year = datetime.datetime.now(tz=datetime.UTC).year
else:
    # sort_order == "metascore": no year dimension
    cutoff_year = 0
    current_year = 0

for scan_year in range(cutoff_year, current_year + 1):
    start_page = db.get_last_scanned_page(platform, scan_year) + 1
    year_games = mc.scan_recent_games(
        platform,
        cache_pages_hours=cfg.cache_pages_hours,
        cutoff_date=cutoff_date,
        cancel_event=cancel_event,
        start_page=start_page,
        show_progress=True,
        year=scan_year if cfg.sort_order == "new" else None,
        max_pages=cfg.max_cycle_pages if cfg.max_cycle_pages else (cfg.max_pages if cfg.max_pages else 0),
    )
    browse_games.extend(year_games)
    last_page = mc._recent_games_last_page or 0
    db.set_last_scanned_page(platform, scan_year, last_page)
```

- [ ] **Step 3: Add sort-order-driven reset alongside existing cache clear (around line 264)**

After the existing sort_order change detection block (the `db.clear_cache("metacritic")` line), add:

```python
# Also reset backlog progress so the new sort order starts from
# page 1 — the page structure is different for "new" vs "metascore".
db.reset_backlog_progress(platform, cfg.sort_order)
```

This goes right after `db.clear_cache("metacritic")` in the sort_order-changed block.

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: per-year page advancement for backlog scanning"
```

---

### Task 3: Database — Unit tests for BacklogProgress

**Files:**
- Modify: `tests/unit/test_database.py`

- [ ] **Step 1: Write the tests**

Add a new test class at the end of `test_database.py`, before the final test class:

```python
class TestBacklogProgress:
    """Tests for the backlog_progress table and CRUD methods."""

    def test_default_returns_zero(self, tmp_path: Path) -> None:
        """When no row exists, get_last_scanned_page returns 0."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        assert db.get_last_scanned_page("pc", 2026) == 0
        db.close()

    def test_set_and_get(self, tmp_path: Path) -> None:
        """Set and retrieve a last_scanned_page value."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 42)
        assert db.get_last_scanned_page("pc", 2026) == 42
        db.close()

    def test_overwrite(self, tmp_path: Path) -> None:
        """Setting the same key updates the existing row."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        db.set_last_scanned_page("pc", 2026, 20)
        assert db.get_last_scanned_page("pc", 2026) == 20
        db.close()

    def test_per_year_isolation(self, tmp_path: Path) -> None:
        """Different years have independent page counters."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        db.set_last_scanned_page("pc", 2025, 50)
        assert db.get_last_scanned_page("pc", 2026) == 10
        assert db.get_last_scanned_page("pc", 2025) == 50
        db.close()

    def test_per_platform_isolation(self, tmp_path: Path) -> None:
        """Different platforms have independent page counters."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        db.set_last_scanned_page("ps4", 2026, 5)
        assert db.get_last_scanned_page("pc", 2026) == 10
        assert db.get_last_scanned_page("ps4", 2026) == 5
        db.close()

    def test_sum_scanned_pages_single_year(self, tmp_path: Path) -> None:
        """Sum across a single year range."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        assert db.sum_scanned_pages("pc", 2026, 2026) == 10
        db.close()

    def test_sum_scanned_pages_multi_year(self, tmp_path: Path) -> None:
        """Sum across a multi-year range."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        db.set_last_scanned_page("pc", 2025, 50)
        db.set_last_scanned_page("pc", 2024, 0)
        assert db.sum_scanned_pages("pc", 2024, 2026) == 60
        db.close()

    def test_sum_scanned_pages_empty_returns_zero(self, tmp_path: Path) -> None:
        """Sum returns 0 when no rows exist in range."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        assert db.sum_scanned_pages("pc", 2020, 2025) == 0
        db.close()

    def test_reset_backlog_progress_new_sort(self, tmp_path: Path) -> None:
        """Reset deletes all rows for sort_order='new'."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        db.set_last_scanned_page("pc", 2025, 50)
        db.reset_backlog_progress("pc", "new")
        assert db.get_last_scanned_page("pc", 2026) == 0
        assert db.get_last_scanned_page("pc", 2025) == 0
        db.close()

    def test_reset_backlog_progress_metascore_sentinel(self, tmp_path: Path) -> None:
        """Reset for sort_order='metascore' only deletes year=0."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 0, 25)   # metascore sentinel
        db.set_last_scanned_page("pc", 2026, 10) # new-mode year
        db.reset_backlog_progress("pc", "metascore")
        assert db.get_last_scanned_page("pc", 0) == 0
        assert db.get_last_scanned_page("pc", 2026) == 10  # untouched
        db.close()
```

- [ ] **Step 2: Run tests — verify they fail (RED)**

```bash
uv run pytest tests/unit/test_database.py::TestBacklogProgress -v --tb=long
```
Expected: All 10 tests fail with `ImportError` (class/methods not defined yet — but they ARE defined in Task 1, so verify they pass).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_database.py
git commit -m "test: add BacklogProgress unit tests"
```

---

### Task 4: Pipeline — Cycles-remaining display in Phase 5

**Files:**
- Modify: `src/gamarr/pipeline.py:455-465`

- [ ] **Step 1: Add cycles-remaining computation function**

Add as a module-level helper function near `_run_discovery_phases` (above line 227):

```python
def _log_backlog_progress(
    platform: str,
    db: Database,
    max_pages: int,
    max_cycle_pages: int,
    cutoff_year: int,
    current_year: int,
) -> None:
    """Log backlog scan progress and estimated cycles remaining."""
    if max_pages <= 0:
        return
    total_scanned = db.sum_scanned_pages(platform, cutoff_year, current_year)
    pct = min(100, round(total_scanned / max_pages * 100))
    remaining = max(0, max_pages - total_scanned)

    if remaining == 0 or total_scanned >= max_pages:
        logger.info("Backlog: fully scanned — monitoring for new releases on page 1")
    elif max_cycle_pages and max_cycle_pages > 0:
        cycles = math.ceil(remaining / max_cycle_pages)
        logger.info(
            "Backlog progress: {} of {} pages ({}%, ~{} cycles remaining)",
            total_scanned,
            max_pages,
            pct,
            cycles,
        )
    else:
        logger.info(
            "Backlog progress: {} of {} pages ({}%, unlimited per cycle)",
            total_scanned,
            max_pages,
            pct,
        )
```

- [ ] **Step 2: Call the function in Phase 5**

In `_run_discovery_phases`, right after the `_process_aged_games` call (line 472), add:

```python
# Log backlog progress if max_pages is configured
if cfg.enabled:
    _log_backlog_progress(
        platform,
        db,
        cfg.max_pages if cfg.max_pages else 0,
        cfg.max_cycle_pages if cfg.max_cycle_pages else 0,
        cutoff_year,
        current_year,
    )
```

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: add cycles-remaining display in Phase 5"
```

---

### Task 5: Pipeline — Integration tests for page advancement

**Files:**
- Modify: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write test for start_page advancement**

Add a test to verify that sequential cycles use advancing start_pages. Add inside the existing `TestScanWindowAdvancing` class (or create a new `TestBacklogAdvancing` class):

```python
class TestBacklogAdvancing:
    """Backlog page advancement across scheduler cycles."""

    def test_backlog_advances_start_page_across_cycles(self, tmp_path: Path) -> None:
        """Subsequent cycles use db-persisted start_page, not 1."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import run_acquisition

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        # Pre-populate: last scanned page for 2026 is 4
        db.set_last_scanned_page("pc", 2026, 4)
        db.close()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                platform="pc",
                db_path=db_path,
                qbt_host="localhost",
                qbt_port=8080,
                max_pages=280,
                max_cycle_pages=4,
                sort_order="new",
            )

            # Verify scan_recent_games was called with start_page=5
            # (persisted page 4 + 1)
            _, kwargs = mock_mc.scan_recent_games.call_args
            assert kwargs["start_page"] == 5, (
                f"Expected start_page=5, got {kwargs['start_page']}"
            )
```

- [ ] **Step 2: Write test for cycles-remaining display**

```python
    def test_backlog_progress_logged_in_phase_5(self, tmp_path: Path) -> None:
        """A 'Backlog progress' line is logged at the end of a cycle."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import run_acquisition

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.set_last_scanned_page("pc", 2026, 12)
        db.close()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            # Capture log output
            from io import StringIO
            import sys

            from loguru import logger

            log_buffer = StringIO()
            handler_id = logger.add(log_buffer, level="INFO", format="{message}")

            run_acquisition(
                platform="pc",
                db_path=db_path,
                qbt_host="localhost",
                qbt_port=8080,
                max_pages=280,
                max_cycle_pages=4,
                sort_order="new",
                enabled=True,
            )

            logger.remove(handler_id)
            log_output = log_buffer.getvalue()

            assert "Backlog progress" in log_output, (
                f"Expected 'Backlog progress' in log output, got: {log_output[:500]}"
            )
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
uv run pytest tests/unit/test_pipeline.py::TestBacklogAdvancing -v --tb=long
```
Expected: PASS (tests should pass once the code from Tasks 1, 2, and 4 is implemented).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add backlog advancement and progress-logging integration tests"
```

---

### Task 6: Full test suite verification

- [ ] **Step 1: Run full suite**

```bash
uv run pytest -v
```
Expected: All tests pass. Fix any regressions.

- [ ] **Step 2: Run linter**

```bash
uv run ruff check . && uv run ruff format --check .
```
Expected: Clean.

- [ ] **Step 3: Run type checker**

```bash
uv run mypy .
```
Expected: No issues.

- [ ] **Step 4: Commit if any fixups needed, otherwise done**

```bash
git add -A
git commit -m "chore: post-implementation cleanup"
```
