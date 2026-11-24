# Clear Cache CLI Flag — Design Spec

**Date:** 2026-06-18
**Status:** Approved
**Author:** pi

## Overview

Add a `--clear-cache` option to the `gamarr` CLI that accepts a comma-separated
list of cache source names (or `all`) and deletes the corresponding rows from
the SQLite database before the main run begins.

## Rationale

There is currently no way to reset the FitGirl/DODI sitemap caches or
Metacritic page/detail caches without manually running SQL queries against
`db/gamarr.db`. This is friction when:

- Testing config changes that affect scoring thresholds (need fresh Metacritic
  detail verification)
- Changing scrape targets or feed URLs (need fresh source sitemaps)
- Debugging stale data issues

## Interface

```bash
gamarr --config-path /path/to/config.yml --clear-cache fitgirl,dodi,metacritic
gamarr --config-path /path/to/config.yml --clear-cache all
```

- `--clear-cache` accepts a single string of comma-separated source names
- Special value `all` clears every cache (equivalent to all three sources)
- The flag is optional — omitting it means no cache clearing (current behaviour)
- Cache clearing happens **before** the scheduler/acquisition starts

## Cache Table Mapping

There are three tables in `db/gamarr.db` that store cached data:

| Source | Tables affected | SQL |
|---|---|---|
| `fitgirl` | `sitemap_cache` | `DELETE FROM sitemap_cache WHERE source='fitgirl'` |
| `dodi` | `sitemap_cache` | `DELETE FROM sitemap_cache WHERE source='dodi'` |
| `metacritic` | `browse_page_cache`, `game_detail_cache` | `DELETE FROM browse_page_cache; DELETE FROM game_detail_cache` |
| `all` | All three tables | All of the above |

## Implementation

### Files to change

| File | Change |
|---|---|
| `src/gamarr/cli.py` | Add `--clear-cache` click option. Parse value, call `db.clear_cache()`. |
| `src/gamarr/database.py` | Add `clear_cache(source: str) -> None` method. |

### CLI change (`cli.py`)

Add a `--clear-cache` option to the existing `@click.command()`:

```python
@click.option(
    "--clear-cache",
    default=None,
    show_default=False,
    help="Clear cached data before running. Comma-separated: fitgirl, dodi, metacritic, or all.",
)
```

After config is loaded but before the scheduler starts, parse the value and
dispatch to `db.clear_cache()`:

```python
if clear_cache:
    sources = [s.strip().casefold() for s in clear_cache.split(",")]
    for source in sources:
        if source == "all":
            for s in ("fitgirl", "dodi", "metacritic"):
                db.clear_cache(s)
        elif source in ("fitgirl", "dodi", "metacritic"):
            db.clear_cache(source)
        else:
            logger.warning("Unknown cache source '{}' — skipping", source)
```

### Database change (`database.py`)

Add a `clear_cache` method:

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

The private helper methods keep `clear_cache` focused on routing while each
helper handles a single table. This also makes testing easier — each table
clear can be tested independently.

### Flow

1. CLI parses `--clear-cache fitgirl,dodi` → list `["fitgirl", "dodi"]`
2. After loading config but before scheduler start, calls `db.clear_cache(s)` for each
3. Each call runs the appropriate `DELETE` via the existing ORM session
4. Unknown source names produce a warning and are silently skipped

## Error Handling

| Scenario | Behaviour |
|---|---|
| Unknown source name | Log warning, skip |
| Table doesn't exist (fresh install) | SQLite returns error → Database initialisation handles it |
| SQL error during delete | Log error at ERROR level, caller continues |
| No database opened | The run will fail at acquisition start — pre-existing behaviour |

## Testing

| Test | What it verifies |
|---|---|
| `test_clear_cache_fitgirl` | `DELETE` runs for sitemap_cache source='fitgirl' only |
| `test_clear_cache_dodi` | `DELETE` runs for sitemap_cache source='dodi' only |
| `test_clear_cache_metacritic` | Both browse_page_cache and game_detail_cache are cleared |
| `test_clear_cache_all` | All three cache tables are cleared |
| `test_clear_cache_unknown_source` | Warning logged, no SQL executed |
| `test_clear_cache_empty_db` | No crash when tables don't exist yet |

## Out of Scope

- Adding a `--list-caches` or `--show-cache-stats` flag — can be added later if needed
- Cache warming / pre-fetch after clearing — the next scheduler cycle does this naturally
- Per-entry cache expiry management — cache clearing is all-or-nothing per source
