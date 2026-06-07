# Merge gamarr-cache.db into gamarr.db

**Date:** 2026-06-07
**Status:** Approved design

## Problem

gamarr maintains two separate SQLite databases:
- `gamarr.db` — history, pending queue, source titles, sitemap cache (SQLAlchemy)
- `gamarr-cache.db` — Metacritic browse page and game detail cache (raw sqlite3 via `MetacriticCache`)

Two files means two connection pools, two sets of PRAGMA settings, and two code paths for database operations. Merging them simplifies the architecture to a single database with one connection pattern.

## Architecture

### Before

```
gamarr.db       ←──  Database (SQLAlchemy, 5 tables)
                         │
gamarr-cache.db ←──  MetacriticCache (raw sqlite3, 2 tables)  ←──  MetacriticClient
```

### After

```
gamarr.db       ←──  Database (SQLAlchemy, 7 tables)  ←──  MetacriticCache (delegates to Database)
                         │                                     │
                         └── MetacriticClient ←─────────────────┘
```

`MetacriticCache` no longer owns a raw `sqlite3.Connection`. It holds a reference to the shared `Database` instance and delegates all cache operations to it.

## New SQLAlchemy models

Added to `src/gamarr/database.py`:

### GameDetailCache

```python
class GameDetailCache(Base):
    __tablename__ = "game_detail_cache"
    slug: Mapped[str] = mapped_column(String, primary_key=True)
    metascore: Mapped[float | None] = mapped_column(Float, nullable=True)
    metascore_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_at: Mapped[str] = mapped_column(String, nullable=False)
```

### BrowsePageCache

```python
class BrowsePageCache(Base):
    __tablename__ = "browse_page_cache"
    platform: Mapped[str] = mapped_column(String, primary_key=True)
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    games_json: Mapped[str] = mapped_column(Text, nullable=False)
    cached_at: Mapped[str] = mapped_column(String, nullable=False)
```

Both tables are auto-created by `Base.metadata.create_all()`. No migration needed since the old `gamarr-cache.db` is ephemeral and can be abandoned.

## New Database methods

Four methods added to the `Database` class:

- `get_game_detail_cache(slug, ttl_days)` — returns a dict with `metascore`, `metascore_reviews`, `user_score`, `user_reviews` if a non-expired row exists, otherwise `None`
- `set_game_detail_cache(slug, metascore, metascore_reviews, user_score, user_reviews)` — inserts or replaces a game detail cache row with the current timestamp
- `get_browse_page_cache(platform, page_number, ttl_hours)` — returns a `list[dict]` of game entries if a non-expired row exists, otherwise `None`
- `set_browse_page_cache(platform, page_number, games)` — inserts or replaces a browse page cache row with the current timestamp

TTL comparison logic (checking `cached_at > now - timedelta`) is identical to the current implementation.

## MetacriticCache refactor

Constructor changes from:

```python
class MetacriticCache:
    def __init__(self, cache_path: str) -> None:
        self._conn = sqlite3.connect(cache_path, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        # creates tables via executescript
```

to:

```python
class MetacriticCache:
    def __init__(self, db: Database) -> None:
        self._db = db
```

All existing methods (`get_game_detail`, `set_game_detail`, `_set_cached_at`, `get_browse_page`, `set_browse_page`) are rewritten to delegate to `self._db.get_game_detail_cache(...)` etc.

The `close()` method no longer disposes the connection — the `Database` engine lifecycle is managed by `run_acquisition`.

The MetacriticCache PRAGMA settings (WAL mode, busy_timeout) are no longer set. The main database uses the default DELETE journal mode from SQLAlchemy, which is sufficient for single-process access.

## MetacriticClient changes

Constructor changes from:

```python
class MetacriticClient:
    def __init__(self, cache_path: str = ":memory:", user_agent: str = _USER_AGENT) -> None:
        self._cache = MetacriticCache(cache_path)
```

to:

```python
class MetacriticClient:
    def __init__(self, cache: MetacriticCache, user_agent: str = _USER_AGENT) -> None:
        self._cache = cache
```

## Pipeline changes (`run_acquisition`)

The `mc_cache_path` parameter is removed:

```python
def run_acquisition(
    *,
    fitgirl_rss_url: str,
    platform: str = "pc",
    db_path: str = ":memory:",
    # mc_cache_path removed
    ...
```

`MetacriticClient` creation changes from:

```python
mc = MetacriticClient(cache_path=mc_cache_path)
```

to:

```python
mc = MetacriticClient(cache=MetacriticCache(db))
```

## Scheduler changes

The `mc_cache_path` key is removed from `_build_kwargs`. The `_resolve_cache_path` helper function is removed since it's no longer needed.

## Tests

All tests that create a `MetacriticCache` with a string `cache_path` are updated to pass a `Database` instance instead:

```python
# Before:
cache = MetacriticCache(":memory:")
# After:
db = Database(":memory:")
cache = MetacriticCache(db)
```

Tests calling `run_acquisition` with `mc_cache_path` remove that kwarg. No new tests required — existing cache behaviour is preserved.

## Migration

No data migration is needed. The old `gamarr-cache.db` file is ephemeral cache — entries expire naturally. The file can be deleted manually by the user or left in place. The new cache tables are created automatically in `gamarr.db` by `Base.metadata.create_all()`.

## Files changed

| File | Change |
|---|---|
| `src/gamarr/database.py` | Add `GameDetailCache` and `BrowsePageCache` models + 4 cache methods |
| `src/gamarr/metacritic_cache.py` | Rewrite to delegate to `Database` instead of raw sqlite3 |
| `src/gamarr/metacritic.py` | `MetacriticClient` takes `MetacriticCache` object instead of cache_path |
| `src/gamarr/pipeline.py` | Remove `mc_cache_path` param, pass shared `db` to `MetacriticCache` |
| `src/gamarr/scheduler.py` | Remove `mc_cache_path` kwargs, remove `_resolve_cache_path` |
| `tests/unit/test_metacritic.py` | Update MetacriticCache construction in all tests |
| `tests/unit/test_pipeline.py` | Remove `mc_cache_path` from run_acquisition calls |
| `tests/unit/test_scheduler.py` | Remove cache path assertions |
