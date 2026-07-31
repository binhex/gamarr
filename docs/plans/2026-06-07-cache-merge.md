# Cache Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge `gamarr-cache.db` (MetacriticCache, raw sqlite3) into `gamarr.db` (Database, SQLAlchemy) so there is one SQLite database.

**Architecture:** Two new SQLAlchemy models (`GameDetailCache`, `BrowsePageCache`) in `database.py` replace the raw sqlite3 tables. `MetacriticCache` is refactored to delegate to `Database` methods instead of owning its own connection. `MetacriticClient` takes a `MetacriticCache` object instead of a `cache_path` string. `mc_cache_path` is removed from `run_acquisition` and the scheduler; the cache shares the pipeline's `Database` instance.

**Tech Stack:** Python, SQLAlchemy 2.0, sqlite3

---

### Task 1: Add cache ORM models to database.py

**Files:**
- Modify: `src/gamarr/database.py`
- Test: `tests/unit/test_database.py`

- [ ] **Step 1: Write failing test for ORM model creation**

Add to `tests/unit/test_database.py`:

```python
def test_cache_orm_tables_created(self, tmp_path: Path) -> None:
    """GameDetailCache and BrowsePageCache tables should be created automatically."""
    from gamarr.database import Database

    db = Database(str(tmp_path / "test.db"))
    with db._session() as session:
        from sqlalchemy import text
        tables = [row[0] for row in session.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))]
    assert "game_detail_cache" in tables
    assert "browse_page_cache" in tables
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_database.py::test_cache_orm_tables_created -v --no-cov`
Expected: FAIL — "game_detail_cache not in tables" (tables don't exist yet)

- [ ] **Step 3: Add ORM models to database.py**

After the `SourceTitle` class block, add:

```python
class GameDetailCache(Base):
    __tablename__ = "game_detail_cache"
    slug: Mapped[str] = mapped_column(String, primary_key=True)
    metascore: Mapped[float | None] = mapped_column(Float, nullable=True)
    metascore_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_at: Mapped[str] = mapped_column(String, nullable=False)


class BrowsePageCache(Base):
    __tablename__ = "browse_page_cache"
    platform: Mapped[str] = mapped_column(String, primary_key=True)
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    games_json: Mapped[str] = mapped_column(Text, nullable=False)
    cached_at: Mapped[str] = mapped_column(String, nullable=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_database.py::test_cache_orm_tables_created -v --no-cov`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/database.py tests/unit/test_database.py
git commit -m "feat: add GameDetailCache and BrowsePageCache ORM models"
```

---

### Task 2: Add Database cache access methods

**Files:**
- Modify: `src/gamarr/database.py`
- Test: `tests/unit/test_database.py`

- [ ] **Step 1: Write failing test for get/set game detail cache**

```python
def test_get_game_detail_cache(self, tmp_path: Path) -> None:
    from gamarr.database import Database

    db = Database(str(tmp_path / "test.db"))
    # Fresh cache should miss
    assert db.get_game_detail_cache("elden-ring", ttl_days=7) is None

    # Set cache entry
    db.set_game_detail_cache("elden-ring", metascore=96.0, metascore_reviews=120,
                             user_score=8.5, user_reviews=5000)

    # Should hit
    result = db.get_game_detail_cache("elden-ring", ttl_days=7)
    assert result is not None
    assert result["metascore"] == 96.0
    assert result["user_score"] == 8.5
    db.close()

def test_get_game_detail_cache_expired(self, tmp_path: Path) -> None:
    import datetime
    from gamarr.database import Database

    db = Database(str(tmp_path / "test.db"))
    db.set_game_detail_cache("old-game", metascore=50.0, metascore_reviews=5,
                             user_score=5.0, user_reviews=10)
    # Override cached_at to be old
    past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=14)).isoformat()
    with db._session() as session:
        from gamarr.database import GameDetailCache
        row = session.get(GameDetailCache, "old-game")
        row.cached_at = past
        session.commit()

    # Should miss (14 days old > 7 day TTL)
    assert db.get_game_detail_cache("old-game", ttl_days=7) is None
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_database.py::test_get_game_detail_cache tests/unit/test_database.py::test_get_game_detail_cache_expired -v --no-cov`
Expected: FAIL — "Database has no attribute 'get_game_detail_cache'"

- [ ] **Step 3: Write failing test for browse page cache**

```python
def test_get_browse_page_cache(self, tmp_path: Path) -> None:
    from gamarr.database import Database

    db = Database(str(tmp_path / "test.db"))
    assert db.get_browse_page_cache("pc", 1, ttl_hours=4) is None

    games = [{"title": "Game", "slug": "game"}]
    db.set_browse_page_cache("pc", 1, games)

    result = db.get_browse_page_cache("pc", 1, ttl_hours=4)
    assert result is not None
    assert result[0]["slug"] == "game"
    db.close()
```

- [ ] **Step 4: Add cache methods to Database class**

After the `set_sitemap_cache` method, add:

```python
    def get_game_detail_cache(self, slug: str, ttl_days: int) -> dict[str, Any] | None:
        """Return cached game detail dict or None if expired/missing."""
        cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=ttl_days)).isoformat()
        with self._session() as session:
            row = session.get(GameDetailCache, slug)
            if row is None or row.cached_at <= cutoff:
                return None
            return {
                "metascore": row.metascore,
                "metascore_reviews": row.metascore_reviews,
                "user_score": row.user_score,
                "user_reviews": row.user_reviews,
            }

    def set_game_detail_cache(
        self,
        slug: str,
        metascore: float | None = None,
        metascore_reviews: int | None = None,
        user_score: float | None = None,
        user_reviews: int | None = None,
    ) -> None:
        """Insert or update a game detail cache entry."""
        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        with self._session() as session:
            row = session.get(GameDetailCache, slug)
            if row is None:
                session.add(GameDetailCache(
                    slug=slug, metascore=metascore, metascore_reviews=metascore_reviews,
                    user_score=user_score, user_reviews=user_reviews, cached_at=now,
                ))
            else:
                row.metascore = metascore
                row.metascore_reviews = metascore_reviews
                row.user_score = user_score
                row.user_reviews = user_reviews
                row.cached_at = now
            session.commit()

    def get_browse_page_cache(self, platform: str, page_number: int, ttl_hours: int) -> list[dict[str, Any]] | None:
        """Return cached browse page games list or None if expired/missing."""
        cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=ttl_hours)).isoformat()
        with self._session() as session:
            row = session.get(BrowsePageCache, (platform, page_number))
            if row is None or row.cached_at <= cutoff:
                return None
            return json.loads(row.games_json)

    def set_browse_page_cache(self, platform: str, page_number: int, games: list[dict[str, Any]]) -> None:
        """Insert or replace a browse page cache entry."""
        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        with self._session() as session:
            row = session.get(BrowsePageCache, (platform, page_number))
            if row is None:
                session.add(BrowsePageCache(
                    platform=platform, page_number=page_number,
                    games_json=json.dumps(games), cached_at=now,
                ))
            else:
                row.games_json = json.dumps(games)
                row.cached_at = now
            session.commit()
```

Also ensure `json` is imported at the top of `database.py` (it already is).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_database.py::test_get_game_detail_cache tests/unit/test_database.py::test_get_game_detail_cache_expired tests/unit/test_database.py::test_get_browse_page_cache -v --no-cov`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest --no-cov -q`
Expected: 280+ passed

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/database.py tests/unit/test_database.py
git commit -m "feat: add cache access methods to Database class"
```

---

### Task 3: Refactor MetacriticCache to delegate to Database

**Files:**
- Modify: `src/gamarr/metacritic_cache.py`
- Test: `tests/unit/test_metacritic.py`

- [ ] **Step 1: Update MetacriticCache.__init__ to accept Database**

Replace entire `metacritic_cache.py` content:

```python
"""SQLite cache for Metacritic browse pages and game details.

Delegates to Database (gamarr.db) via SQLAlchemy models.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from gamarr.database import Database


class MetacriticCache:
    """Cache for Metacritic scraped data, backed by the main gamarr DB.

    Args:
        db: A :class:`Database` instance connected to ``gamarr.db``.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def close(self) -> None:
        """No-op: the Database engine is managed by the pipeline."""
        pass

    def get_game_detail(self, slug: str, ttl_days: int = 7) -> dict[str, Any] | None:
        """Retrieve cached game detail for *slug* if within *ttl_days*."""
        return self._db.get_game_detail_cache(slug, ttl_days)

    def set_game_detail(
        self,
        slug: str,
        metascore: float | None,
        metascore_reviews: int | None,
        user_score: float | None,
        user_reviews: int | None,
    ) -> None:
        """Insert or update a cached game detail entry."""
        self._db.set_game_detail_cache(slug, metascore, metascore_reviews, user_score, user_reviews)

    def _set_cached_at(self, slug: str, cached_at: str) -> None:
        """Override the cached_at timestamp for testing."""
        with self._db._session() as session:
            from gamarr.database import GameDetailCache
            row = session.get(GameDetailCache, slug)
            if row is not None:
                row.cached_at = cached_at
                session.commit()

    def get_browse_page(self, platform: str, page_number: int, ttl_hours: int = 4) -> list[dict[str, Any]] | None:
        """Retrieve cached browse page data if within *ttl_hours*."""
        return self._db.get_browse_page_cache(platform, page_number, ttl_hours)

    def set_browse_page(self, platform: str, page_number: int, games: list[dict[str, Any]]) -> None:
        """Insert or replace a cached browse page entry."""
        self._db.set_browse_page_cache(platform, page_number, games)
```

- [ ] **Step 2: Update tests to pass Database to MetacriticCache**

In `tests/unit/test_metacritic.py`, replace ALL patterns of:

```python
cache = MetacriticCache(str(tmp_path / "cache.db"))
```

with:

```python
db = Database(str(tmp_path / "test.db"))
cache = MetacriticCache(db)
```

And update the relevant cleanup (`db.close()` instead of `cache.close()`).

Also import `Database` at the top of the test file if not already:

```python
from gamarr.database import Database
```

Check the test file currently imports `MetacriticCache` — keep that, just update construction.

- [ ] **Step 3: Update `_set_cached_at` test helper reference**

In tests using `cache._set_cached_at(...)`, no change needed — the method still exists with same signature.

- [ ] **Step 4: Run metacritic tests**

Run: `pytest tests/unit/test_metacritic.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest --no-cov -q`
Expected: 280+ passed

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/metacritic_cache.py tests/unit/test_metacritic.py
git commit -m "refactor: MetacriticCache delegates to Database instead of raw sqlite3"
```

---

### Task 4: Update MetacriticClient constructor

**Files:**
- Modify: `src/gamarr/metacritic.py`

- [ ] **Step 1: Change constructor to accept MetacriticCache object**

In `src/gamarr/metacritic.py`, change:

```python
class MetacriticClient:
    def __init__(
        self,
        cache_path: str = "db/gamarr-cache.db",
        user_agent: str = _USER_AGENT,
    ) -> None:
        self.user_agent = user_agent
        self._cache = MetacriticCache(cache_path)
```

to:

```python
class MetacriticClient:
    def __init__(
        self,
        cache: MetacriticCache,
        user_agent: str = _USER_AGENT,
    ) -> None:
        self.user_agent = user_agent
        self._cache = cache
```

- [ ] **Step 2: Run full test suite**

Run: `pytest --no-cov -q`
Expected: 280+ passed (some metacritic tests need updating — see Task 5)

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/metacritic.py
git commit -m "refactor: MetacriticClient takes MetacriticCache object instead of cache_path"
```

---

### Task 5: Update pipeline.py — remove mc_cache_path

**Files:**
- Modify: `src/gamarr/pipeline.py`
- Test: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Remove `mc_cache_path` param from `run_acquisition`**

In `src/gamarr/pipeline.py`:

- Remove `mc_cache_path: str = ":memory:",` from function signature
- Update `MetacriticClient` creation from:
  ```python
  mc = MetacriticClient(cache_path=mc_cache_path)
  ```
  to:
  ```python
  mc = MetacriticClient(cache=MetacriticCache(db))
  ```
- Add `from gamarr.metacritic_cache import MetacriticCache` to the module-level imports (pipeline.py already imports `MetacriticClient`, add `MetacriticCache` alongside it)

- [ ] **Step 2: Remove `mc_cache_path` from internal docstring**

Remove the `mc_cache_path` entry from the `run_acquisition` docstring.

- [ ] **Step 3: Update test that passes `mc_cache_path`**

In `tests/unit/test_pipeline.py`, find the test using `mc_cache_path` (line ~1977):

```python
# Before:
results = run_acquisition(
    ...
    mc_cache_path=str(tmp_path / "mc-cache.db"),
    ...
)

# After: remove that kwarg
results = run_acquisition(
    ...
    ...
)
```

- [ ] **Step 4: Run full test suite**

Run: `pytest --no-cov -q`
Expected: 280+ passed

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "refactor: remove mc_cache_path from run_acquisition, use shared Database"
```

---

### Task 6: Update scheduler.py — remove mc_cache_path

**Files:**
- Modify: `src/gamarr/scheduler.py`
- Test: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Remove `_resolve_cache_path` function and `mc_cache_path` kwarg**

In `src/gamarr/scheduler.py`:

- Remove the entire `_resolve_cache_path` function (lines 35-39)
- Remove `import os` if it becomes unused (it's probably used elsewhere — check)
- Remove `"mc_cache_path": _resolve_cache_path(config.general.db_path),` from `_build_kwargs`

- [ ] **Step 2: Update scheduler tests that use `_resolve_cache_path`**

In `tests/unit/test_scheduler.py`, remove or update tests referencing `_resolve_cache_path`:

```python
# Remove these test methods:
def test_cache_path_with_memory(self) -> None:
    ...

def test_cache_path_with_dir(self) -> None:
    ...
```

- [ ] **Step 3: Run full test suite**

Run: `pytest --no-cov -q`
Expected: 278+ passed (down from 280 — 2 tests removed)

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/scheduler.py tests/unit/test_scheduler.py
git commit -m "refactor: remove mc_cache_path and _resolve_cache_path from scheduler"
```

---

### Task 7: Update MetacriticClient test constructions

**Files:**
- Modify: `tests/unit/test_metacritic.py`

- [ ] **Step 1: Update all `MetacriticClient(cache_path=":memory:")` calls**

In `tests/unit/test_metacritic.py`, replace ALL instances of:

```python
client = MetacriticClient(cache_path=":memory:")
```

with:

```python
db = Database(":memory:")
client = MetacriticClient(cache=MetacriticCache(db))
```

There are approximately 18 occurrences. Use `sed` for bulk replacement, ensuring `Database` and `MetacriticCache` are imported at the top of the file.

- [ ] **Step 2: Run metacritic tests**

Run: `pytest tests/unit/test_metacritic.py -v --no-cov`
Expected: PASS

- [ ] **Step 3: Run full test suite and ruff**

Run: `uv run pytest --no-cov -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: All passed, all clean

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_metacritic.py
git commit -m "test: update MetacriticClient construction to use shared Database"
```

---

### Task 8: Delete old cache file and final verification

**Files:**
- Delete: `db/gamarr-cache.db` (if exists)

- [ ] **Step 1: Delete old cache file**

```bash
rm -f db/gamarr-cache.db db/gamarr-cache.db-wal db/gamarr-cache.db-shm
```

- [ ] **Step 2: Final full verification**

```bash
uv run pytest --no-cov -q
uv run pytest --cov=src/gamarr --cov-fail-under=80
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/gamarr/
uv run pre-commit run --all-files
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: delete old gamarr-cache.db, finalise cache merge"
```

---

### Summary of all changes

| File | Change |
|---|---|
| `src/gamarr/database.py` | +2 ORM models, +4 cache access methods |
| `src/gamarr/metacritic_cache.py` | Rewritten to delegate to Database |
| `src/gamarr/metacritic.py` | MetacriticClient takes `cache` object |
| `src/gamarr/pipeline.py` | Remove `mc_cache_path`, pass shared db |
| `src/gamarr/scheduler.py` | Remove `_resolve_cache_path`, `mc_cache_path` |
| `tests/unit/test_database.py` | +3 cache tests |
| `tests/unit/test_metacritic.py` | Update all MetacriticCache/MetacriticClient constructions |
| `tests/unit/test_pipeline.py` | Remove `mc_cache_path` from one call |
| `tests/unit/test_scheduler.py` | Remove `_resolve_cache_path` tests |
| `db/gamarr-cache.db` | Deleted |
