# Remove search_mode, Unify Browse Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate `search_mode` config, consolidate `pending_games_backlog`/`pending_games_latest` into a single `pending_games` table, unify the browse loop with `max_pages` budget and infinite auto-reset.

**Architecture:** The `search_mode` branch in `run_acquisition()` collapses into one loop that uses the backlog mode's year-loop, `max_pages` budget, `max_cycle_pages` per-cycle cap, and progress tracking. When the budget exhausts, progress resets to page 1. All `_by_mode` dispatch helpers are removed; callers go directly to unified `db.*` methods targeting `PendingGame`. DB migration runs once on startup: rows from mode-specific tables are inserted into `pending_games`, then the old tables are dropped.

**Tech Stack:** Python 3.12, SQLAlchemy ORM, Pydantic config, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/gamarr/config.py` | Modify | Remove `search_mode` field + data migration |
| `src/gamarr/database.py` | Modify | Add table migration, consolidate 10 method pairs → 10 single methods |
| `src/gamarr/pipeline.py` | Modify | Unified browse loop, remove 5 `_by_mode` helpers, update all callers |
| `src/gamarr/scheduler.py` | Modify | Remove `search_mode` from `build_kwargs()` |
| `tests/unit/test_config.py` | Modify | Remove `search_mode` test references |
| `tests/unit/test_database.py` | Modify | Update for consolidated method names + table |
| `tests/unit/test_pipeline.py` | Modify | Update for unified loop, remove `search_mode` references, update `_by_mode` callers |

---

### Task 1: Config — Remove `search_mode` and add migration

**Files:**
- Modify: `src/gamarr/config.py:114`

**What:** Remove the `search_mode` field from `MetacriticPlatformConfig`, bump `_CONFIG_VERSION`, add a migration step.

- [ ] **Step 1: Remove `search_mode` from the config model**

In `src/gamarr/config.py`, remove line 114:

```diff
-    search_mode: Literal["backlog", "latest"] = "latest"
```

- [ ] **Step 2: Bump `_CONFIG_VERSION` and add migration**

In `src/gamarr/config.py`, bump the version at line 16:

```diff
- _CONFIG_VERSION = "1.0.0"
+ _CONFIG_VERSION = "1.58.0"
```

In the `_migrations` list (around line 812), add a migration that removes `search_mode` from loaded configs:

```python
# Remove search_mode from metacritic platform configs (it's been eliminated)
def _migrate_remove_search_mode(cfg: dict[str, Any]) -> None:
    platform_overrides = cfg.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_config in platform_overrides.values():
        platform_config.pop("search_mode", None)
```

Append `_migrate_remove_search_mode` to the `_migrations` list.

- [ ] **Step 3: Run config tests to verify migration works**

```bash
uv run pytest tests/unit/test_config.py -v
```

- [ ] **Step 4: Add/update a config migration test**

In `tests/unit/test_config.py`, add a test that verifies `search_mode` is stripped from loaded configs:

```python
def test_migration_removes_search_mode():
    raw = {
        "general": {"config_version": "1.0.0"},
        "review_sites": {
            "metacritic": {
                "platform_overrides": {
                    "pc": {"search_mode": "backlog", "min_metascore": 75},
                }
            }
        },
    }
    config = load_config_from_dict(raw)  # or whatever the config loading helper is
    pc_cfg = config.review_sites.metacritic.platform_overrides["pc"]
    assert not hasattr(pc_cfg, "search_mode")
```

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "feat: remove search_mode config key with migration"
```

---

### Task 2: Database — Table migration on startup

**Files:**
- Modify: `src/gamarr/database.py::Database.__init__`

**What:** On `Database.__init__`, if `pending_games_backlog` or `pending_games_latest` tables exist, migrate their rows into `pending_games` and drop the old tables.

- [ ] **Step 1: Add table migration in `Database.__init__`**

In `src/gamarr/database.py`, add a migration block after table creation (after `Base.metadata.create_all`). Insert it after the existing column-migration blocks:

```python
# Migrate pending_games_backlog / pending_games_latest -> pending_games
inspector = inspect(engine)
existing_tables = inspector.get_table_names()
if "pending_games_backlog" in existing_tables or "pending_games_latest" in existing_tables:
    logger.info("Migrating pending_games_backlog / pending_games_latest -> pending_games")
    with session_factory() as session:
        for source_table in ("pending_games_backlog", "pending_games_latest"):
            if source_table not in existing_tables:
                continue
            # Build INSERT from raw SQL since the ORM classes map to different tables
            rows = session.execute(
                text(
                    f"SELECT slug, game_title, platform, metascore, metascore_reviews, "
                    f"user_score, user_reviews, genres, release_date, discovered_at, "
                    f"expires_at, last_checked_at, score_checks_passed "
                    f"FROM {source_table}"
                )
            ).fetchall()
            for row in rows:
                session.execute(
                    text(
                        "INSERT OR IGNORE INTO pending_games "
                        "(slug, game_title, platform, metascore, metascore_reviews, "
                        "user_score, user_reviews, genres, release_date, discovered_at, "
                        "expires_at, last_checked_at, score_checks_passed) "
                        "VALUES (:slug, :game_title, :platform, :metascore, :metascore_reviews, "
                        ":user_score, :user_reviews, :genres, :release_date, :discovered_at, "
                        ":expires_at, :last_checked_at, :score_checks_passed)"
                    ),
                    {
                        "slug": row[0], "game_title": row[1], "platform": row[2],
                        "metascore": row[3], "metascore_reviews": row[4],
                        "user_score": row[5], "user_reviews": row[6],
                        "genres": row[7], "release_date": row[8],
                        "discovered_at": row[9], "expires_at": row[10],
                        "last_checked_at": row[11], "score_checks_passed": row[12],
                    },
                )
            session.execute(text(f"DROP TABLE IF EXISTS {source_table}"))
            session.commit()
            logger.info("Migrated and dropped table: {}", source_table)
```

- [ ] **Step 2: Run database tests to verify migration**

```bash
uv run pytest tests/unit/test_database.py -v -k "init or pending" --timeout 30
```

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/database.py
git commit -m "feat: add pending_games table migration from backlog/latest tables"
```

---

### Task 3: Database — Consolidate method pairs

**Files:**
- Modify: `src/gamarr/database.py` (lines 455-890)

**What:** Merge all 10 `_backlog`/`_latest` method pairs into single methods targeting `PendingGame`.

- [ ] **Step 1: Consolidate `reset_backlog_progress` → `reset_progress`**

```python
def reset_progress(self, platform: str, sort_order: str) -> None:
    """Reset last-scanned-page for all years for *platform*."""
    with self._session() as session:
        session.query(BacklogProgress).filter(
            BacklogProgress.platform == platform
        ).delete()
        session.commit()
```

- [ ] **Step 2: Consolidate `record_backlog_pending` + `record_latest_pending` → `record_pending`**

Rename `record_backlog_pending` to `record_pending`, target `PendingGame`. Delete `record_latest_pending`.

```python
def record_pending(
    self,
    *,
    slug: str,
    game_title: str,
    platform: str,
    metascore: float | None = None,
    metascore_reviews: int | None = None,
    user_score: float | None = None,
    user_reviews: int | None = None,
    genres: list[str] | None = None,
    release_date: str | None = None,
    expires_at: str | None = None,
) -> None:
    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    indefinite_default = (
        datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=_INDEFINITE_DAYS)
    ).isoformat()
    with self._session() as session:
        existing = session.get(PendingGame, slug)
        if existing is not None:
            return
        row = PendingGame(
            slug=slug,
            game_title=game_title,
            platform=platform,
            metascore=metascore,
            metascore_reviews=metascore_reviews,
            user_score=user_score,
            user_reviews=user_reviews,
            genres=", ".join(genres) if genres else None,
            release_date=release_date,
            discovered_at=now,
            expires_at=expires_at or indefinite_default,
        )
        session.add(row)
        session.commit()
```

- [ ] **Step 3: Consolidate `get_backlog_pending` + `get_latest_pending` → `get_pending`**

```python
def get_pending(self, *, platform: str | None = None) -> list[PendingGame]:
    with self._session() as session:
        query = session.query(PendingGame)
        if platform is not None:
            query = query.filter(PendingGame.platform == platform)
        return list(query.all())
```

- [ ] **Step 4: Consolidate `remove_backlog_pending` + `remove_latest_pending` → `remove_pending`**

```python
def remove_pending(self, slug: str) -> None:
    with self._session() as session:
        session.query(PendingGame).filter(PendingGame.slug == slug).delete()
        session.commit()
```

- [ ] **Step 5: Consolidate `touch_backlog_pending` + `touch_latest_pending` → `touch_pending`**

```python
def touch_pending(self, slug: str) -> None:
    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    with self._session() as session:
        row = session.get(PendingGame, slug)
        if row is None:
            return
        row.last_checked_at = now
        session.commit()
```

- [ ] **Step 6: Consolidate `update_backlog_pending_scores` + `update_latest_pending_scores` → `update_pending_scores`**

```python
def update_pending_scores(
    self,
    *,
    slug: str,
    metascore: float | None = None,
    metascore_reviews: int | None = None,
    user_score: float | None = None,
    user_reviews: int | None = None,
) -> None:
    with self._session() as session:
        row = session.get(PendingGame, slug)
        if row is None:
            return
        if metascore is not None:
            row.metascore = metascore
        if metascore_reviews is not None:
            row.metascore_reviews = metascore_reviews
        if user_score is not None:
            row.user_score = user_score
        if user_reviews is not None:
            row.user_reviews = user_reviews
        if any(x is not None for x in (metascore, metascore_reviews, user_score, user_reviews)):
            row.score_checks_passed = True
        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        row.last_checked_at = now
        session.commit()
```

- [ ] **Step 7: Consolidate `update_backlog_pending_expiry` + `update_latest_pending_expiry` → `update_pending_expiry`**

```python
def update_pending_expiry(self, slug: str, max_queue_days: int) -> None:
    days = _INDEFINITE_DAYS if max_queue_days <= 0 else max_queue_days
    expires_at = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=days)).isoformat()
    with self._session() as session:
        row = session.get(PendingGame, slug)
        if row is None:
            return
        row.expires_at = expires_at
        session.commit()
```

- [ ] **Step 8: Consolidate `get_expired_backlog_pending` + `get_expired_latest_pending` → `get_expired_pending`**

```python
def get_expired_pending(self) -> list[PendingGame]:
    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    with self._session() as session:
        return list(
            session.query(PendingGame).filter(PendingGame.expires_at <= now).all()
        )
```

- [ ] **Step 9: Consolidate `is_backlog_pending` + `is_latest_pending` → `is_pending`**

```python
def is_pending(self, slug: str) -> bool:
    with self._session() as session:
        return session.get(PendingGame, slug) is not None
```

- [ ] **Step 10: Consolidate `has_verified_backlog_pending` + `has_verified_latest_pending` → `has_verified_pending`**

```python
def has_verified_pending(self, *, platform: str | None = None) -> bool:
    with self._session() as session:
        query = session.query(PendingGame).filter(
            PendingGame.score_checks_passed == True,  # noqa: E712
            PendingGame.expires_at > datetime.datetime.now(tz=datetime.UTC).isoformat(),
        )
        if platform is not None:
            query = query.filter(PendingGame.platform == platform)
        return query.first() is not None
```

- [ ] **Step 11: Consolidate `get_known_backlog_slugs` + `get_known_latest_slugs` → `get_known_slugs`**

```python
def get_known_slugs(self, *, source: str, platform: str) -> set[str]:
    known: set[str] = set()
    with self._session() as session:
        rows = (
            session.query(HistoryRow.source_url)
            .filter(HistoryRow.source == source, HistoryRow.source_url.isnot(None))
            .all()
        )
        for (source_url,) in rows:
            slug: str = str(source_url)
            if slug.startswith("mc:"):
                known.add(slug[3:])
            else:
                known.add(slug)
        pending_rows = session.query(PendingGame.slug).all()
        for (slug,) in pending_rows:
            known.add(str(slug))
    return known
```

- [ ] **Step 12: Delete all old `_backlog_pending` and `_latest_pending` methods**

Remove all method bodies and definitions for: `record_backlog_pending`, `record_latest_pending`, `get_backlog_pending`, `get_latest_pending`, `remove_backlog_pending`, `remove_latest_pending`, `touch_backlog_pending`, `touch_latest_pending`, `update_backlog_pending_scores`, `update_latest_pending_scores`, `update_backlog_pending_expiry`, `update_latest_pending_expiry`, `get_expired_backlog_pending`, `get_expired_latest_pending`, `is_backlog_pending`, `is_latest_pending`, `has_verified_backlog_pending`, `has_verified_latest_pending`, `get_known_backlog_slugs`, `get_known_latest_slugs`.

- [ ] **Step 13: Delete ORM classes `PendingGameBacklog` and `PendingGameLatest`**

Remove the class definitions from `database.py`.

- [ ] **Step 14: Run database tests**

```bash
uv run pytest tests/unit/test_database.py -v --timeout 30
```

- [ ] **Step 15: Commit**

```bash
git add src/gamarr/database.py tests/unit/test_database.py
git commit -m "refactor: consolidate backlog/latest DB methods into unified pending_games methods"
```

---

### Task 4: Pipeline — Remove `_by_mode` helpers and update callers

**Files:**
- Modify: `src/gamarr/pipeline.py`

**What:** Delete `_get_known_slugs_by_mode`, `_remove_pending_by_mode`, `_touch_pending_by_mode`, `_record_pending_by_mode`, `_update_pending_scores_by_mode`. Update all callers to use unified `db.*` methods.

- [ ] **Step 1: Delete the five `_by_mode` helper functions**

Remove these function definitions from `pipeline.py`:
- `_get_known_slugs_by_mode` (lines ~1093-1098)
- `_remove_pending_by_mode` (lines ~1100-1106)
- `_touch_pending_by_mode` (lines ~1108-1114)
- `_record_pending_by_mode` (lines ~1116-1154)
- `_update_pending_scores_by_mode` (lines ~1156-1182)

- [ ] **Step 2: Update callers — `_process_browse_games`**

Replace `_get_known_slugs_by_mode` and `_record_pending_by_mode`:

```python
# Before:
known_slugs = _get_known_slugs_by_mode(db, search_mode, source="metacritic", platform=platform)
# After:
known_slugs = db.get_known_slugs(source="metacritic", platform=platform)
```

```python
# Before:
_record_pending_by_mode(db, search_mode, slug=g_slug, game_title=g_title, ...)
# After:
db.record_pending(slug=g_slug, game_title=g_title, ...)
```

Also remove `search_mode` parameter from `_process_browse_games` signature and docstring.

- [ ] **Step 3: Update callers — `_process_verify_result`**

Replace `_remove_pending_by_mode`, `_touch_pending_by_mode`, `_update_pending_scores_by_mode`:

```python
# Before:
_remove_pending_by_mode(db, str(game.slug), search_mode)
# After:
db.remove_pending(str(game.slug))

# Before:
_touch_pending_by_mode(db, str(game.slug), search_mode)
# After:
db.touch_pending(str(game.slug))

# Before:
_update_pending_scores_by_mode(db, str(game.slug), result, search_mode, fitgirl_max_queue_days)
# After:
db.update_pending_scores(slug=str(game.slug), ...)
db.update_pending_expiry(str(game.slug), fitgirl_max_queue_days)
```

Also remove `search_mode` parameter from `_process_verify_result` signature.

- [ ] **Step 4: Update callers — `_process_verify_batch`**

Remove `search_mode` from `_process_verify_batch` signature and from the `_process_verify_result` call within it.

- [ ] **Step 5: Update callers — `_verify_pending_scores`**

Remove `search_mode` parameter. Replace:

```python
# Before:
pending = (
    db.get_backlog_pending(platform=platform)
    if search_mode == "backlog"
    else db.get_latest_pending(platform=platform)
)
# After:
pending = db.get_pending(platform=platform)
```

Remove `search_mode` from `_process_verify_batch` call.

- [ ] **Step 6: Update callers — `_process_aged_games`**

Remove `search_mode` parameter. Replace `get_expired_backlog_pending`/`get_expired_latest_pending` with `db.get_expired_pending()`. Replace `is_backlog_pending`/`is_latest_pending` with `db.is_pending(slug)`.

- [ ] **Step 7: Update callers — `_jit_verify_and_update`**

Remove `search_mode` parameter. Use `db.record_pending()` instead of `db.record_backlog_pending()`/`db.record_latest_pending()`.

- [ ] **Step 8: Update callers — `_process_single_pending_match`**

Remove `search_mode` parameter. Use `db.remove_pending()`.

- [ ] **Step 9: Run pipeline tests to verify no breakage**

```bash
uv run pytest tests/unit/test_pipeline.py -v --timeout 60
```

- [ ] **Step 10: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "refactor: remove _by_mode helpers, use unified db methods"
```

---

### Task 5: Pipeline — Unified browse loop

**Files:**
- Modify: `src/gamarr/pipeline.py` (the `run_acquisition` inner function, lines ~285-610)

**What:** Replace the `if cfg.search_mode == "backlog"` / `else` branching with a single unified browse loop.

- [ ] **Step 1: Replace the search_mode branch with the unified loop**

In `run_acquisition()`, replace the entire `if cfg.enabled:` block's browse logic with:

```python
if cfg.enabled:
    # Detect sort_order change and reset progress if needed
    previous_sort_order = db.get_last_sort_order(platform)
    if previous_sort_order is not None and previous_sort_order != cfg.sort_order:
        logger.info(
            "Sort order changed from '{}' to '{}'",
            previous_sort_order,
            cfg.sort_order,
        )
        db.clear_cache("metacritic")
        db.reset_progress(platform, cfg.sort_order)

    mc.sort_order = cfg.sort_order

    # Calculate year range
    if cfg.sort_order == "new":
        years_back = max(0, math.ceil((cfg.max_pages if cfg.max_pages else 500) / 52))
        cutoff_year = scan_year_anchor - years_back
        current_year = scan_year_anchor
    else:
        # sort_order == "metascore": no year dimension, use year=0 sentinel
        cutoff_year = 0
        current_year = 0

    total_scanned = db.sum_scanned_pages(platform, cutoff_year, current_year)
    max_pages_cfg = cfg.max_pages if cfg.max_pages else 0

    # Budget check — auto-reset when exhausted
    if max_pages_cfg > 0 and total_scanned >= max_pages_cfg:
        logger.info(
            "Budget exhausted — {} of {} pages scanned, restarting from page 1",
            total_scanned,
            max_pages_cfg,
        )
        db.reset_progress(platform, cfg.sort_order)
        total_scanned = 0

    remaining_pages = max_pages_cfg - total_scanned if max_pages_cfg > 0 else 0

    # Year loop
    for scan_year in range(cutoff_year, current_year + 1):
        if is_cancelled(cancel_event):
            break
        if max_pages_cfg > 0 and remaining_pages <= 0:
            break
        start_page = db.get_last_scanned_page(platform, scan_year) + 1
        try:
            if max_pages_cfg > 0:
                if cfg.max_cycle_pages and cfg.max_cycle_pages > 0:
                    per_call_max = min(cfg.max_cycle_pages, remaining_pages)
                else:
                    per_call_max = remaining_pages
            else:
                per_call_max = cfg.max_cycle_pages if cfg.max_cycle_pages else 0

            year_games = mc.scan_recent_games(
                platform,
                cache_pages_hours=cfg.cache_pages_hours,
                cutoff_date=None,
                cancel_event=cancel_event,
                start_page=start_page,
                show_progress=True,
                year=scan_year if cfg.sort_order == "new" else None,
                max_pages=per_call_max,
            )
            browse_games.extend(year_games)
            last_page = mc._recent_games_last_page if isinstance(mc._recent_games_last_page, int) else 0
            db.set_last_scanned_page(platform, scan_year, last_page)

            if max_pages_cfg > 0 and last_page >= start_page:
                pages_scanned = last_page - start_page + 1
                remaining_pages -= pages_scanned
        except Exception:
            logger.exception("Scan failed for year {} — will retry next cycle", scan_year)

    db.set_last_sort_order(platform, cfg.sort_order)
```

Note: the `cutoff_date` variable declaration at the top of the enabled block can be removed (it was always `None`).

- [ ] **Step 2: Remove `search_mode` from `AcquisitionConfig`**

```diff
-    search_mode: Literal["backlog", "latest"] = "latest"
```

And from `run_acquisition()` parameters:

```diff
-    search_mode: Literal["backlog", "latest"] = "latest",
```

And from the `cfg = AcquisitionConfig(...)` construction:

```diff
-        search_mode=search_mode,
```

- [ ] **Step 3: Update end-of-cycle logging**

Replace the backlog progress log (lines ~604-614) with a simpler budget log:

```python
# Log scan progress if max_pages is configured
if cfg.enabled and cfg.max_pages and cfg.max_pages > 0:
    total_scanned = db.sum_scanned_pages(platform, cutoff_year, current_year)
    if total_scanned > 0:
        pct = min(100, round(total_scanned / cfg.max_pages * 100))
        logger.info(
            "Progress: {} of {} pages scanned ({}%)",
            total_scanned,
            cfg.max_pages,
            pct,
        )
```

Remove the `_log_backlog_progress` function (only caller was the backlog branch).

- [ ] **Step 4: Remove `search_mode` from `_process_browse_games` call**

```diff
-                    search_mode=cfg.search_mode,
```

Also remove `search_mode` from `_process_aged_games` call, `_match_pending_games` call if present, and `pending_queue_len` block:

```diff
-                pending_queue_len = (
-                    len(db.get_backlog_pending(platform=platform))
-                    if cfg.search_mode == "backlog"
-                    else len(db.get_latest_pending(platform=platform))
-                )
+                pending_queue_len = len(db.get_pending(platform=platform))
```

And the `pending_games` fetch:

```diff
-            pending_games = (
-                db.get_backlog_pending(platform=platform)
-                if cfg.search_mode == "backlog"
-                else db.get_latest_pending(platform=platform)
-            )
+            pending_games = db.get_pending(platform=platform)
```

And the verified check:

```diff
-            if not is_cancelled(cancel_event) and (
-                db.has_verified_backlog_pending(platform=platform)
-                if cfg.search_mode == "backlog"
-                else db.has_verified_latest_pending(platform=platform)
-            ):
+            if not is_cancelled(cancel_event) and db.has_verified_pending(platform=platform):
```

- [ ] **Step 5: Run pipeline tests**

```bash
uv run pytest tests/unit/test_pipeline.py -v --timeout 60
```

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: unified browse loop with max_pages budget and infinite auto-reset"
```

---

### Task 6: Scheduler — Remove `search_mode` passing

**Files:**
- Modify: `src/gamarr/scheduler.py:210`

- [ ] **Step 1: Remove `search_mode` from `build_kwargs()`**

```diff
-        "search_mode": mc_cfg.search_mode,
```

- [ ] **Step 2: Run scheduler tests**

```bash
uv run pytest tests/unit/test_scheduler.py -v --timeout 30
```

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/scheduler.py tests/unit/test_scheduler.py
git commit -m "refactor: remove search_mode from scheduler kwargs"
```

---

### Task 7: Tests — Full update pass

**Files:**
- Modify: `tests/unit/test_pipeline.py`, `tests/unit/test_database.py`, `tests/unit/test_config.py`

**What:** Fix all remaining test references to deleted methods, `search_mode`, and mode-specific tables.

- [ ] **Step 1: Update pipeline tests**

Search for remaining `search_mode` references and replace:

```bash
grep -n "search_mode\|_backlog_pending\|_latest_pending\|_by_mode\|get_backlog_pending\|get_latest_pending\|has_verified_backlog\|has_verified_latest\|is_backlog_pending\|is_latest_pending\|reset_backlog_progress" tests/unit/test_pipeline.py
```

For each match:
- `search_mode=` → remove the parameter from `run_acquisition()` calls and `AcquisitionConfig()` constructions
- `db.get_backlog_pending(...)` / `db.get_latest_pending(...)` → `db.get_pending(...)`
- `db.record_backlog_pending(...)` / `db.record_latest_pending(...)` → `db.record_pending(...)`
- `db.has_verified_backlog_pending(...)` / `db.has_verified_latest_pending(...)` → `db.has_verified_pending(...)`
- `db.is_backlog_pending(...)` / `db.is_latest_pending(...)` → `db.is_pending(...)`
- `db.reset_backlog_progress(...)` → `db.reset_progress(...)`
- `db.remove_backlog_pending(...)` / `db.remove_latest_pending(...)` → `db.remove_pending(...)`
- `_process_verify_result(..., search_mode=...)` → remove `search_mode=` kwarg
- `_process_browse_games(..., search_mode=...)` → remove `search_mode=` kwarg
- `_verify_pending_scores(..., search_mode=...)` → remove `search_mode=` kwarg

Also update tests that check for `PendingGameBacklog` / `PendingGameLatest` ORM references to use `PendingGame` instead.

- [ ] **Step 2: Update database tests**

Replace `PendingGameBacklog` / `PendingGameLatest` ORM references with `PendingGame`. Update method name references to consolidated names.

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest -v --timeout 120
```

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: update tests for unified pending table and removed search_mode"
```

---

### Task 8: README cleanup

**Files:**
- Modify: `README.md` (if needed)

- [ ] **Step 1: Check README for `search_mode` references**

```bash
grep -n "search_mode\|backlog.*latest\|latest.*backlog" README.md || echo "Clean"
```

If any references found, update them:
- Remove `search_mode` from example configs
- Replace "backlog mode" / "latest mode" descriptions with the unified approach
- Update the two-phase description if it references mode switching

- [ ] **Step 2: Commit (if changes needed)** otherwise skip

```bash
git add README.md
git commit -m "docs: update README for search_mode removal"
```

---

### Task 9: Final integration test

- [ ] **Step 1: Run full test suite with coverage**

```bash
uv run pytest --cov=src/gamarr --cov-report=term -v --timeout 120
```

Expected: All tests pass. Coverage >= 95%.

- [ ] **Step 2: Run ruff and mypy**

```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy .
```

Expected: Zero errors.

- [ ] **Step 3: Commit any final cleanups**

```bash
git add -A
git commit -m "chore: final cleanup after search_mode removal"
```
