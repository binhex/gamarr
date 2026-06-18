# `--clear-cache` CLI Flag — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--clear-cache` flag to the `gamarr` CLI that deletes cached data for fitgirl, dodi, metacritic, or all sources.

**Architecture:** A new `Database.clear_cache(source)` method dispatching to private helpers that execute `DELETE` SQL against the three cache tables. A new `--clear-cache` click option on the existing flat CLI command.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, SQLite, Click, pytest.

**Spec:** `docs/specs/2026-06-18-clear-cache-design.md`

---

### Task 1: Database cache clearing methods

**Files:**
- Modify: `src/gamarr/database.py` (add after `set_sitemap_cache`)
- Test: `tests/unit/test_database.py`

- [ ] **Step 1: Write the failing tests**

Append these tests to `tests/unit/test_database.py` (before the closing of the last class or as standalone functions at the end):

```python
def test_clear_cache_fitgirl(temp_db: Database) -> None:
    """clear_cache('fitgirl') deletes only the fitgirl sitemap cache row."""
    temp_db.set_sitemap_cache("fitgirl")
    temp_db.set_sitemap_cache("dodi")
    temp_db.clear_cache("fitgirl")
    assert not temp_db.get_sitemap_cache("fitgirl", 9999)  # TTL=9999 → never expires
    assert temp_db.get_sitemap_cache("dodi", 9999)  # dodi row untouched


def test_clear_cache_dodi(temp_db: Database) -> None:
    """clear_cache('dodi') deletes only the dodi sitemap cache row."""
    temp_db.set_sitemap_cache("fitgirl")
    temp_db.set_sitemap_cache("dodi")
    temp_db.clear_cache("dodi")
    assert not temp_db.get_sitemap_cache("dodi", 9999)
    assert temp_db.get_sitemap_cache("fitgirl", 9999)


def test_clear_cache_metacritic(temp_db: Database) -> None:
    """clear_cache('metacritic') clears browse + detail caches, leaves sitemap alone."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    # Insert a browse cache row
    with temp_db._session() as session:
        session.execute(
            text(
                "INSERT INTO browse_page_cache (platform, page_number, games_json, cached_at) "
                "VALUES (:p, :pn, :j, :ca)"
            ),
            {"p": "pc", "pn": 1, "j": "[]", "ca": datetime.now(timezone.utc).isoformat()},
        )
        # Insert a detail cache row
        session.execute(
            text(
                "INSERT INTO game_detail_cache (slug, metascore, cached_at) "
                "VALUES (:s, :m, :ca)"
            ),
            {"s": "test-game", "m": 85, "ca": datetime.now(timezone.utc).isoformat()},
        )
        session.commit()

    temp_db.set_sitemap_cache("fitgirl")
    temp_db.clear_cache("metacritic")

    # Browse + detail caches cleared
    row = temp_db._session().execute(
        text("SELECT COUNT(*) FROM browse_page_cache")
    ).scalar()
    assert row == 0, f"browse_page_cache has {row} rows"
    row = temp_db._session().execute(
        text("SELECT COUNT(*) FROM game_detail_cache")
    ).scalar()
    assert row == 0, f"game_detail_cache has {row} rows"
    # Sitemap cache untouched
    assert temp_db.get_sitemap_cache("fitgirl", 9999)


def test_clear_cache_unknown_source_logs_warning(temp_db: Database, caplog: pytest.LogCaptureFixture) -> None:
    """clear_cache with an unrecognised source logs a warning."""
    temp_db.clear_cache("nonexistent")
    assert any("Unknown cache source" in msg for msg in caplog.messages)
```

Note: `temp_db` is the existing fixture from `test_database.py` — check the conftest or file for the exact fixture name. If no such fixture exists, use `tmp_path`:

```python
def test_clear_cache_fitgirl(tmp_path: Path) -> None:
    from gamarr.database import Database
    db = Database(str(tmp_path / "test.db"))
    db.set_sitemap_cache("fitgirl")
    db.set_sitemap_cache("dodi")
    db.clear_cache("fitgirl")
    assert not db.get_sitemap_cache("fitgirl", 9999)
    assert db.get_sitemap_cache("dodi", 9999)
    db.close()
```

(Apply the same `tmp_path` pattern to all four tests above if `temp_db` doesn't exist.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_database.py -k "clear_cache" -v
```

Expected: 4 failed — `Database` has no `clear_cache` method.

- [ ] **Step 3: Write minimal implementation**

Add to `src/gamarr/database.py`, after `set_sitemap_cache` (around line 485):

```python
def clear_cache(self, source: str) -> None:
    """Clear cached data for a given source.

    Args:
        source: One of ``"fitgirl"``, ``"dodi"``, or ``"metacritic"``.
    """
    if source == "fitgirl":
        self._delete_sitemap_cache("fitgirl")
    elif source == "dodi":
        self._delete_sitemap_cache("dodi")
    elif source == "metacritic":
        self._delete_browse_cache()
        self._delete_detail_cache()
    else:
        logger.warning("Unknown cache source '{}' — skipping", source)

def _delete_sitemap_cache(self, source: str) -> None:
    with self._session() as session:
        session.execute(text("DELETE FROM sitemap_cache WHERE source = :source"), {"source": source})
        session.commit()

def _delete_browse_cache(self) -> None:
    with self._session() as session:
        session.execute(text("DELETE FROM browse_page_cache"))
        session.commit()

def _delete_detail_cache(self) -> None:
    with self._session() as session:
        session.execute(text("DELETE FROM game_detail_cache"))
        session.commit()
```

Also add the import at the top if `text` isn't already imported:
```python
from sqlalchemy import text
```

Check the existing imports — `from sqlalchemy import text` should already be present (used in `_migrate` methods). If not, add it.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_database.py -k "clear_cache" -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/database.py tests/unit/test_database.py
git commit -m "feat: add Database.clear_cache() for fitgirl/dodi/metacritic caches"
```

---

### Task 2: CLI `--clear-cache` flag

**Files:**
- Modify: `src/gamarr/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_cli.py` (at the end):

```python
def test_clear_cache_flag_parses_single_source() -> None:
    """--clear-cache fitgirl parses correctly and calls clear_cache."""
    from click.testing import CliRunner
    from unittest.mock import patch

    from gamarr.cli import cli

    runner = CliRunner()
    with patch("gamarr.cli.Database") as mock_db:
        result = runner.invoke(cli, ["--config-path", "/tmp/.gamarr.yml", "--clear-cache", "fitgirl"])
    assert result.exit_code == 0
    mock_db.return_value.clear_cache.assert_called_once_with("fitgirl")


def test_clear_cache_flag_parses_multiple_sources() -> None:
    """--clear-cache fitgirl,dodi calls clear_cache for each."""
    from click.testing import CliRunner
    from unittest.mock import patch

    from gamarr.cli import cli

    runner = CliRunner()
    with patch("gamarr.cli.Database") as mock_db:
        result = runner.invoke(cli, ["--config-path", "/tmp/.gamarr.yml", "--clear-cache", "fitgirl,dodi"])
    assert result.exit_code == 0
    assert mock_db.return_value.clear_cache.call_count == 2
    mock_db.return_value.clear_cache.assert_any_call("fitgirl")
    mock_db.return_value.clear_cache.assert_any_call("dodi")


def test_clear_cache_flag_parses_all() -> None:
    """--clear-cache all calls clear_cache for all three sources."""
    from click.testing import CliRunner
    from unittest.mock import patch

    from gamarr.cli import cli

    runner = CliRunner()
    with patch("gamarr.cli.Database") as mock_db:
        result = runner.invoke(cli, ["--config-path", "/tmp/.gamarr.yml", "--clear-cache", "all"])
    assert result.exit_code == 0
    assert mock_db.return_value.clear_cache.call_count == 3
    mock_db.return_value.clear_cache.assert_any_call("fitgirl")
    mock_db.return_value.clear_cache.assert_any_call("dodi")
    mock_db.return_value.clear_cache.assert_any_call("metacritic")


def test_clear_cache_unknown_source_logs_warning() -> None:
    """Unknown cache source is silently skipped."""
    from click.testing import CliRunner
    from unittest.mock import patch

    from gamarr.cli import cli

    runner = CliRunner()
    with patch("gamarr.cli.Database") as mock_db, patch("gamarr.cli.logger") as mock_logger:
        result = runner.invoke(cli, ["--config-path", "/tmp/.gamarr.yml", "--clear-cache", "bogus"])
    assert result.exit_code == 0
    mock_db.return_value.clear_cache.assert_not_called()
    mock_logger.warning.assert_any_call("Unknown cache source '{}' — skipping", "bogus")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_cli.py -k "clear_cache" -v
```

Expected: 4 failed — no `--clear-cache` option on CLI yet.

- [ ] **Step 3: Write minimal implementation**

In `src/gamarr/cli.py`:

a) Add the new option to the `@click.option` decorators (around line 158, before `@click.version_option`):

```python
@click.option(
    "--clear-cache",
    default=None,
    show_default=False,
    help="Clear cached data before running. Comma-separated: fitgirl, dodi, metacritic, or all.",
)
```

b) Add `clear_cache` parameter to the `cli` function signature (line ~162):

```python
def cli(
    config_path: str,
    log_level_console: str | None,
    ...,
    clear_cache: str | None = None,
) -> None:
```

c) After config is loaded and Database is created (find the `db = Database(...)` call near the end of `cli()`), insert the cache clearing logic:

```python
if clear_cache:
    from gamarr.database import Database

    db = Database(db_path=str(cfg.general.db_path))
    try:
        sources = [s.strip().casefold() for s in clear_cache.split(",")]
        for source in sources:
            if source == "all":
                for s in ("fitgirl", "dodi", "metacritic"):
                    db.clear_cache(s)
            elif source in ("fitgirl", "dodi", "metacritic"):
                db.clear_cache(source)
            else:
                logger.warning("Unknown cache source '{}' — skipping", source)
    finally:
        db.close()
```

Find the right insertion point by searching for where `db = Database(...)` is called in the existing CLI flow. It's likely inside a `try/finally` block that calls `db.close()`. The cache clearing should happen right after the Database is opened, before any scheduler/acquisition code runs.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_cli.py -k "clear_cache" -v
```

Expected: 4 passed.

- [ ] **Step 5: Run full test suite**

```bash
cd /data/gamarr && uv run pytest -q
```

Expected: all tests pass (coverage may drop slightly due to untested branches in CLI).

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/cli.py tests/unit/test_cli.py
git commit -m "feat: add --clear-cache CLI flag for fitgirl/dodi/metacritic"
```

---

### Task 3: End-to-end smoke test (optional but recommended)

**Files:**
- Create: `tests/integration/test_clear_cache.py`

- [ ] **Step 1: Write an integration test**

```python
"""End-to-end test for --clear-cache using a temp database."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest


def test_clear_cache_via_cli_sitemap(tmp_path: Path) -> None:
    """--clear-cache fitgirl removes the fitgirl sitemap cache row."""
    import shutil
    import subprocess
    import sys

    from gamarr.database import Database

    # Create a real gamarr config pointing at a temp DB
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    db_path = db_dir / "gamarr.db"

    # Pre-populate a sitemap cache entry
    db = Database(str(db_path))
    db.set_sitemap_cache("fitgirl")
    db.set_sitemap_cache("dodi")
    db.close()

    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"""\
general:
  config_version: 1.35.0
  db_path: {db_dir}
schedule:
  acquisition:
    enabled: false
"""
    )

    # Run gamarr with --clear-cache fitgirl
    result = subprocess.run(
        [sys.executable, "-m", "gamarr", "--config-path", str(config_path), "--clear-cache", "fitgirl"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    # Verify: dodi still cached, fitgirl cleared
    db2 = Database(str(db_path))
    assert not db2.get_sitemap_cache("fitgirl", 9999), f"fitgirl should be cleared\n{result.stdout}\n{result.stderr}"
    assert db2.get_sitemap_cache("dodi", 9999), f"dodi should remain\n{result.stdout}\n{result.stderr}"
    db2.close()
```

- [ ] **Step 2: Run it**

```bash
cd /data/gamarr && uv run pytest tests/integration/test_clear_cache.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_clear_cache.py
git commit -m "test: integration test for --clear-cache CLI flag"
```

---

## Spec Coverage Check

| Spec requirement | Task |
|---|---|
| `--clear-cache fitgirl` clears sitemap_cache for fitgirl | Task 1 (db method), Task 2 (CLI), Task 3 (e2e) |
| `--clear-cache dodi` clears sitemap_cache for dodi | Task 1, Task 2 |
| `--clear-cache metacritic` clears browse + detail caches | Task 1 |
| `--clear-cache all` clears all three sources | Task 2 |
| Unknown source logs warning | Task 1, Task 2 |
| Cache clearing before scheduler start | Task 2 (insertion point), Task 3 (e2e) |
| Error handling: missing table / SQL error | Task 1 (exception propagation), existing behaviour |
