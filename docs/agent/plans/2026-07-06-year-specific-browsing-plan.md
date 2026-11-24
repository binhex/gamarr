# Year-Specific Metacritic Browsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the all-time Metacritic browse URL with year-specific URLs so gamarr can discover games from years before 2021, while keeping `max_weeks` and `max_cycle_weeks` as control knobs.

**Architecture:** Add `sort_order` config key. Change browse URL from `all-time/new/` to `/{year}/{sort_order}/`. Add `year` parameter to `scan_recent_games()`. Remove `browse_start_page` / `last_browse_page` (no longer needed). Migrate browse page cache to compound `(platform, year, page_number)` key.

**Tech Stack:** Python 3.12+, SQLAlchemy, pytest, Loguru

---

### Task 1: Add `sort_order` config key

**Files:**
- Modify: `src/gamarr/config.py:112` (add after `max_cycle_weeks`)
- Test: `tests/unit/test_config.py` — add test for sort_order

- [ ] **Step 1: Write failing tests for sort_order**

Insert in `tests/unit/test_config.py`:

```python
class TestSortOrder:
    """sort_order config key for year-specific Metacritic browsing."""

    def test_sort_order_defaults_to_newest(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert cfg.sort_order == "newest"

    def test_sort_order_accepts_metascore(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig(sort_order="metascore")
        assert cfg.sort_order == "metascore"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_config.py::TestSortOrder -v
```

Expected: 2 FAIL (pydantic ValidationError — `sort_order` is not a valid field)

- [ ] **Step 3: Add `sort_order` to `MetacriticPlatformConfig`**

Insert after `max_cycle_weeks` (line 112) in `src/gamarr/config.py`:

```python
    sort_order: str = "newest"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_config.py::TestSortOrder -v
```

Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "feat: add sort_order config key for year-specific browsing"
```

---

### Task 2: Change browse URL from all-time to year-specific

**Files:**
- Modify: `src/gamarr/metacritic.py:715-740` (`_fetch_browse_page`)
- Test: `tests/unit/test_metacritic.py` — update URL assertions

- [ ] **Step 1: Update tests to expect year-specific URLs**

Find and update existing URL assertions in test_metacritic.py. The current URL format is:

```
/browse/game/pc/all/all-time/new/?releaseYearMin=1958&releaseYearMax=2035&platform=pc&page=0
```

Change test assertions to expect:

```
/browse/game/pc/all/2026/newest/?releaseYearMin=1958&releaseYearMax=2035&platform=pc&page=0
```

For tests that mock `requests.get`, update the mock to respond to the new URL format.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_metacritic.py -x -q
```

Expected: FAIL — URL format mismatch

- [ ] **Step 3: Change URL construction and thread sort_order**

First, add a `sort_order` attribute to `MetacriticClient` in `src/gamarr/metacritic.py` (near `__init__`):

```python
def __init__(self, user_agent: str = _USER_AGENT):
    self.user_agent = user_agent
    self.sort_order = "newest"  # default, overridden before scan_recent_games
```

Change the URL construction in `_fetch_browse_page` (line 720-724):

```python
# BEFORE
url = (
    f"https://www.metacritic.com/browse/game/{platform}/all/all-time/new/"
    f"?releaseYearMin=1958&releaseYearMax=2035"
    f"&platform={platform}&page={page_number}"
)

# AFTER
year_str = str(year) if year is not None else "all-time"
url = (
    f"https://www.metacritic.com/browse/game/{platform}/all/{year_str}/{self.sort_order}/"
    f"?releaseYearMin=1958&releaseYearMax=2035"
    f"&platform={platform}&page={page_number}"
)
```

Also update the `_fetch_browse_page` signature to accept `year: int | None = None`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_metacritic.py -x -q
```

Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/metacritic.py tests/unit/test_metacritic.py
git commit -m "feat: change browse URL from all-time to year-specific format"
```

---

### Task 3: Add `year` parameter to `scan_recent_games` and change cache key

**Files:**
- Modify: `src/gamarr/metacritic.py:753-835` (`scan_recent_games`) + pass `year` through calls
- Test: `tests/unit/test_metacritic.py` — add test for year parameter and cache key

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_metacritic.py`:

```python
def test_scan_recent_games_accepts_year(self) -> None:
    """scan_recent_games should accept a year parameter and pass it to _fetch_browse_page."""
    from unittest.mock import MagicMock, patch

    from gamarr.metacritic import MetacriticClient

    client = MetacriticClient()
    with patch.object(client, "_fetch_browse_page", return_value=None) as mock_fetch:
        client.scan_recent_games("pc", year=2024, max_games=0)

    # Should have called _fetch_browse_page with year=2024
    assert mock_fetch.call_count >= 1
    assert mock_fetch.call_args[1].get("year") == 2024
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_metacritic.py::test_scan_recent_games_accepts_year -v
```

Expected: FAIL — `scan_recent_games()` got unexpected keyword argument `year`

- [ ] **Step 3: Add `year` parameter to `scan_recent_games`**

In `src/gamarr/metacritic.py:753`, add `year` to the signature:

```python
def scan_recent_games(
    self,
    platform: str,
    *,
    year: int | None = None,
    max_games: int = 0,
    ...
```

Pass `year` through to `_fetch_browse_page`:

```python
games = self._fetch_browse_page(platform, page_number, cache_pages_hours, year=year)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_metacritic.py::test_scan_recent_games_accepts_year -v
```

Expected: PASS

- [ ] **Step 5: Change the cache key**

Update `_fetch_browse_page` and cache methods to use `(platform, year, page_number)` as the compound key. Change `_cache.get_browse_page(platform, page_number, ...)` to `_cache.get_browse_page(platform, page_number, year=year, ...)`. Similarly for `set_browse_page`.

- [ ] **Step 6: Run all metacritic tests**

```bash
uv run pytest tests/unit/test_metacritic.py -v
```

Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/metacritic.py tests/unit/test_metacritic.py
git commit -m "feat: add year parameter to scan_recent_games + per-year cache key"
```

---

### Task 4: Remove `browse_start_page` from pipeline and database

**Files:**
- Modify: `src/gamarr/pipeline.py` — remove browse_start_page logic
- Modify: `src/gamarr/database.py` — remove last_browse_page column
- Test: `tests/unit/test_pipeline.py`, `tests/unit/test_database.py` — update/remove tests

- [ ] **Step 1: Remove `browse_start_page` from pipeline.py**

Remove the following from `src/gamarr/pipeline.py`:

1. Line ~256: Remove `browse_start_page = db.get_last_browse_page(platform) or 1` — replace with `browse_start_page = 1`
2. Line ~298: Remove the guard block's `browse_start_page = db.get_last_browse_page(platform) or 1`
3. Line ~325: Remove `browse_start_page = 1` from the clamped block (it's already reset in steady-state)
4. Line ~380: Remove `_last_page` tracking: `_last_page = getattr(mc, "_recent_games_last_page", None)` and `if isinstance(_last_page, int): db.set_last_browse_page(platform, _last_page)`
5. Add `year` parameter to the `scan_recent_games()` call AND set `sort_order` on the client:

```python
from datetime import datetime

# Set sort_order from config on the MC client before scanning
mc.sort_order = cfg.sort_order

if cutoff_date is not None:
    scan_year = datetime.strptime(cutoff_date, "%Y-%m-%d").year
else:
    scan_year = datetime.now(tz=datetime.UTC).year

browse_games = mc.scan_recent_games(
    platform,
    cache_pages_hours=cfg.cache_pages_hours,
    cutoff_date=cutoff_date,
    cancel_event=cancel_event,
    start_page=browse_start_page,
    year=scan_year,
    show_progress=(not clamped_by_max_weeks),
)
```

- [ ] **Step 2: Run pipeline tests to see which ones break**

```bash
uv run pytest tests/unit/test_pipeline.py -x -q 2>&1 | tail -10
```

Expected: Some tests fail because `start_page` assertions no longer match

- [ ] **Step 3: Update pipeline tests**

For each failing test:
- Remove `start_page` assertions (e.g., `test_backlog_scan_resumes_from_stored_page`)
- Remove `test_backlog_scan_uses_stored_page_when_max_weeks_increased`
- Update `TestScanWindowAdvancing` tests to not check `start_page`/`browse_start_page`

- [ ] **Step 4: Remove `last_browse_page` from database.py**

1. Remove `last_browse_page: Mapped[...]` from `ScanState` (line 120)
2. Remove `get_last_browse_page` and `set_last_browse_page` methods
3. Simplify `_migrate_scan_state` — remove the `last_browse_page` migration block, keep only `last_max_weeks`
4. Add migration to drop `last_browse_page` column if it exists:

```python
if "last_browse_page" in columns:
    with self._session() as session:
        session.execute(text("ALTER TABLE scan_state DROP COLUMN last_browse_page"))
        session.commit()
    logger.debug("Dropped last_browse_page column from scan_state")
```

- [ ] **Step 5: Update database tests**

Remove tests that reference `last_browse_page`. Update the `TestMaxWeeksDetection` test (it uses `set_last_max_weeks` which is unaffected).

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest -x -q
```

Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/pipeline.py src/gamarr/database.py tests/
git commit -m "refactor: remove browse_start_page, add year-based scanning from pipeline"
```

---

### Task 5: Migrate browse page cache to year-based key

**Files:**
- Modify: `src/gamarr/database.py` — add `year` column to `BrowseCache`, migration
- Test: `tests/unit/test_database.py` — test migration

- [ ] **Step 1: Add `year` column to `BrowseCache` model**

In `src/gamarr/database.py`, find the `BrowseCache` class and add:

```python
year: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 2: Add migration**

```python
if "year" not in [c["name"] for c in inspector.get_columns("browse_cache")]:
    with self._session() as session:
        session.execute(text("ALTER TABLE browse_cache ADD COLUMN year INTEGER"))
        session.commit()
    logger.debug("Added year column to browse_cache")
```

- [ ] **Step 3: Update cache query to include year**

Update `get_browse_page` and `set_browse_page` to accept and filter by `year`:

```python
def get_browse_page(self, platform: str, page_number: int, *, ttl_hours: int, year: int | None = None) -> list[dict] | None:
    ...
    query = session.query(BrowseCache).filter(
        BrowseCache.platform == platform,
        BrowseCache.page_number == page_number,
        BrowseCache.year == year,
    )
    ...

def set_browse_page(self, platform: str, page_number: int, games: list[dict], *, year: int | None = None) -> None:
    ...
    cache_row = BrowseCache(
        platform=platform,
        page_number=page_number,
        year=year,
        ...
    )
```

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest -x -q
```

Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/database.py tests/unit/test_database.py
git commit -m "feat: add year column to browse page cache for year-specific browsing"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest -q
```

Expected: All tests pass (count may differ due to removed tests)

- [ ] **Step 2: Run lint + type check**

```bash
uv run ruff check --fix . && uv run ruff format . && uv run mypy .
```

Expected: All clean

- [ ] **Step 3: Commit if any lint fixes were made**

```bash
git add -A && git commit -m "chore: fix lint/format issues from year-specific browsing changes"
```
