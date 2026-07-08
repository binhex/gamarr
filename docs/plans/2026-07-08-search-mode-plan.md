# Search Mode — Backlog/Latest Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the conflated backlog/latest scanning paths into two explicit modes controlled by a `search_mode` config field with separate pending-game database tables.

**Architecture:** Add `search_mode` to `MetacriticPlatformConfig`, create `pending_games_backlog` and `pending_games_latest` ORM tables, branch the pipeline browse phase on mode, and thread mode through to all downstream database calls. Backlog mode preserves current behavior (year-loop, progress tracking). Latest mode is a new simplified path (pages 1..max_cycle_pages, no progress tracking). Mode state is independently preserved when switching.

**Tech Stack:** Python 3.12+, Pydantic v2, SQLAlchemy 2.x, SQLite, pytest, uv

---

### Task 1: Add `search_mode` to config model

**Files:**
- Modify: `src/gamarr/config.py` (add field to `MetacriticPlatformConfig`)
- Test: `tests/unit/test_config.py` (add validation tests)

- [ ] **Step 1: Add the field**

In `src/gamarr/config.py`, inside `MetacriticPlatformConfig`, add after `sort_order`:

```python
search_mode: Literal["backlog", "latest"] = "latest"
```

- [ ] **Step 2: Add test for default value**

In `tests/unit/test_config.py`, add:

```python
def test_metacritic_platform_config_search_mode_default() -> None:
    """search_mode defaults to 'latest'."""
    from gamarr.config import MetacriticPlatformConfig

    cfg = MetacriticPlatformConfig()
    assert cfg.search_mode == "latest"


def test_metacritic_platform_config_search_mode_valid() -> None:
    """search_mode accepts 'backlog' and 'latest'."""
    from gamarr.config import MetacriticPlatformConfig

    backlog_cfg = MetacriticPlatformConfig(search_mode="backlog")
    assert backlog_cfg.search_mode == "backlog"

    latest_cfg = MetacriticPlatformConfig(search_mode="latest")
    assert latest_cfg.search_mode == "latest"


def test_metacritic_platform_config_search_mode_invalid_raises() -> None:
    """search_mode rejects values outside Literal."""
    import pytest
    from gamarr.config import MetacriticPlatformConfig

    with pytest.raises(Exception):  # ValidationError via Pydantic
        MetacriticPlatformConfig(search_mode="illegal")  # type: ignore[arg-type]
```

- [ ] **Step 3: Run just the new tests**

```bash
uv run pytest tests/unit/test_config.py::test_metacritic_platform_config_search_mode_default tests/unit/test_config.py::test_metacritic_platform_config_search_mode_valid tests/unit/test_config.py::test_metacritic_platform_config_search_mode_invalid_raises -v
```

Expected: 3 PASS

- [ ] **Step 4: Run full config test suite**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: all PASS (existing tests unaffected)

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "feat: add search_mode field to MetacriticPlatformConfig"
```

---

### Task 2: Add `PendingGameBacklog` and `PendingGameLatest` ORM models

**Files:**
- Modify: `src/gamarr/database.py` (add two ORM classes)

- [ ] **Step 1: Add the ORM classes**

In `src/gamarr/database.py`, after the `PendingGame` class definition, add:

```python
class PendingGameBacklog(Base):
    """ORM mapping for the ``pending_games_backlog`` table."""

    __tablename__ = "pending_games_backlog"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    game_title: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    metascore: Mapped[float | None] = mapped_column(Float, nullable=True)
    metascore_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    genres: Mapped[str | None] = mapped_column(String, nullable=True)
    release_date: Mapped[str | None] = mapped_column(String, nullable=True)
    discovered_at: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    last_checked_at: Mapped[str | None] = mapped_column(String, nullable=True)
    score_checks_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class PendingGameLatest(Base):
    """ORM mapping for the ``pending_games_latest`` table."""

    __tablename__ = "pending_games_latest"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    game_title: Mapped[str] = mapped_column(String, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    metascore: Mapped[float | None] = mapped_column(Float, nullable=True)
    metascore_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    genres: Mapped[str | None] = mapped_column(String, nullable=True)
    release_date: Mapped[str | None] = mapped_column(String, nullable=True)
    discovered_at: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    last_checked_at: Mapped[str | None] = mapped_column(String, nullable=True)
    score_checks_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
```

- [ ] **Step 2: Verify tables are created on startup**

```bash
uv run python -c "
from gamarr.config import load_config, Config
from gamarr.database import Database
import tempfile, os
with tempfile.TemporaryDirectory() as tmp:
    db = Database(tmp)
    db.close()
    import sqlite3
    conn = sqlite3.connect(os.path.join(tmp, 'gamarr.db'))
    tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
    assert 'pending_games_backlog' in tables, f'backlog table missing: {tables}'
    assert 'pending_games_latest' in tables, f'latest table missing: {tables}'
    conn.close()
    print('OK: both tables created')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/database.py
git commit -m "feat: add PendingGameBacklog and PendingGameLatest ORM models"
```

---

### Task 3: Add migration logic for legacy `pending_games` data

**Files:**
- Modify: `src/gamarr/database.py` (add `_migrate_pending_mode_split`)

- [ ] **Step 1: Add the migration method**

In `src/gamarr/database.py`, inside the `Database` class, add after `_migrate_browse_cache`:

```python
def _migrate_pending_mode_split(self) -> None:
    """Copy legacy pending_games rows into pending_games_backlog.

    Existing data logically belongs to backlog mode.  The original
    table is preserved as a safety net and not dropped.
    """
    try:
        inspector = sa_inspect(self._engine)
        legacy_tables = inspector.get_table_names()
        if "pending_games" not in legacy_tables:
            return
        legacy_rows = inspector.get_columns("pending_games")
        if not legacy_rows:
            return

        with self._session() as session:
            count = session.query(PendingGame).count()
            if count == 0:
                return

            rows = session.query(PendingGame).all()
            for row in rows:
                backlog_row = PendingGameBacklog(
                    slug=row.slug,
                    game_title=row.game_title,
                    platform=row.platform,
                    metascore=row.metascore,
                    metascore_reviews=row.metascore_reviews,
                    user_score=row.user_score,
                    user_reviews=row.user_reviews,
                    genres=row.genres,
                    release_date=row.release_date,
                    discovered_at=row.discovered_at,
                    expires_at=row.expires_at,
                    last_checked_at=row.last_checked_at,
                    score_checks_passed=row.score_checks_passed,
                )
                session.add(backlog_row)
                session.delete(row)
            session.commit()
            logger.info(
                "Migrated {} pending games from legacy table to pending_games_backlog",
                count,
            )
    except Exception:
        logger.debug("Migration of pending_games skipped (table may not exist yet)")
```

- [ ] **Step 2: Wire the migration into `_migrate()`**

In the `_migrate` method, add `self._migrate_pending_mode_split()` to the call list:

```python
def _migrate(self) -> None:
    """Add columns added in newer versions of gamarr."""
    self._migrate_pending_games()
    self._migrate_game_detail_cache()
    self._migrate_source_titles()
    self._migrate_scan_state()
    self._migrate_browse_cache()
    self._migrate_pending_mode_split()
```

- [ ] **Step 3: Test the migration**

```bash
uv run python -c "
from gamarr.database import Database, PendingGame
import tempfile, os, datetime, json

with tempfile.TemporaryDirectory() as tmp:
    db_path = os.path.join(tmp, 'gamarr.db')
    
    # Create a legacy pending_games table manually
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute('''CREATE TABLE pending_games (
        slug TEXT PRIMARY KEY,
        game_title TEXT NOT NULL,
        platform TEXT NOT NULL,
        metascore REAL,
        metascore_reviews INTEGER,
        user_score REAL,
        user_reviews INTEGER,
        genres TEXT,
        release_date TEXT,
        discovered_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        last_checked_at TEXT,
        score_checks_passed INTEGER
    )''')
    now = datetime.datetime.now(datetime.UTC).isoformat()
    conn.execute(
        'INSERT INTO pending_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('test-slug', 'Test Game', 'pc', 85.0, 20, 8.5, 50, '[\"Action\"]', '2024-01-01', now, now, None, 1)
    )
    conn.commit()
    conn.close()
    
    # Open Database — migration should copy the row
    db = Database(db_path)
    
    # Check the row is now in backlog table
    import sqlite3
    conn = sqlite3.connect(db_path)
    backlog_rows = conn.execute('SELECT slug, game_title FROM pending_games_backlog').fetchall()
    legacy_rows = conn.execute('SELECT slug FROM pending_games').fetchall()
    conn.close()
    
    assert len(backlog_rows) == 1, f'Expected 1 backlog row, got {len(backlog_rows)}'
    assert backlog_rows[0][0] == 'test-slug'
    assert len(legacy_rows) == 0, f'Legacy table should be empty, got {len(legacy_rows)}'
    
    db.close()
    print('OK: migration successful')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/database.py
git commit -m "feat: add legacy pending_games migration to backlog table"
```

---

### Task 4: Add mode-specific CRUD methods to `Database`

**Files:**
- Modify: `src/gamarr/database.py` (add backlog/latest-specific methods)
- Modify: `tests/unit/test_database.py` (add tests)

- [ ] **Step 1: Add `record_backlog_pending` and `record_latest_pending`**

After `record_pending`, add:

```python
def record_backlog_pending(
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
    with self._session() as session:
        existing = session.get(PendingGameBacklog, slug)
        if existing is not None:
            return
        row = PendingGameBacklog(
            slug=slug,
            game_title=game_title,
            platform=platform,
            metascore=metascore,
            metascore_reviews=metascore_reviews,
            user_score=user_score,
            user_reviews=user_reviews,
            genres=json.dumps(genres) if genres else None,
            release_date=release_date,
            discovered_at=now,
            expires_at=expires_at or now,
            last_checked_at=None,
        )
        session.add(row)
        session.commit()


def record_latest_pending(
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
    with self._session() as session:
        existing = session.get(PendingGameLatest, slug)
        if existing is not None:
            return
        row = PendingGameLatest(
            slug=slug,
            game_title=game_title,
            platform=platform,
            metascore=metascore,
            metascore_reviews=metascore_reviews,
            user_score=user_score,
            user_reviews=user_reviews,
            genres=json.dumps(genres) if genres else None,
            release_date=release_date,
            discovered_at=now,
            expires_at=expires_at or now,
            last_checked_at=None,
        )
        session.add(row)
        session.commit()
```

- [ ] **Step 2: Add `get_backlog_pending` and `get_latest_pending`**

After the new `record_*` methods, add:

```python
def get_backlog_pending(self, *, platform: str | None = None) -> list[PendingGameBacklog]:
    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    with self._session() as session:
        query = session.query(PendingGameBacklog).filter(PendingGameBacklog.expires_at > now)
        if platform is not None:
            query = query.filter(PendingGameBacklog.platform == platform)
        return list(query.all())


def get_latest_pending(self, *, platform: str | None = None) -> list[PendingGameLatest]:
    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    with self._session() as session:
        query = session.query(PendingGameLatest).filter(PendingGameLatest.expires_at > now)
        if platform is not None:
            query = query.filter(PendingGameLatest.platform == platform)
        return list(query.all())
```

- [ ] **Step 3: Add remaining mode-specific methods**

Add after `get_latest_pending`:

```python
def remove_backlog_pending(self, slug: str) -> None:
    with self._session() as session:
        row = session.get(PendingGameBacklog, slug)
        if row is not None:
            session.delete(row)
            session.commit()


def remove_latest_pending(self, slug: str) -> None:
    with self._session() as session:
        row = session.get(PendingGameLatest, slug)
        if row is not None:
            session.delete(row)
            session.commit()


def touch_backlog_pending(self, slug: str) -> None:
    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    with self._session() as session:
        row = session.get(PendingGameBacklog, slug)
        if row is not None:
            row.last_checked_at = now
            session.commit()


def touch_latest_pending(self, slug: str) -> None:
    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    with self._session() as session:
        row = session.get(PendingGameLatest, slug)
        if row is not None:
            row.last_checked_at = now
            session.commit()


def update_backlog_pending_scores(
    self,
    *,
    slug: str,
    metascore: float | None = None,
    metascore_reviews: int | None = None,
    user_score: float | None = None,
    user_reviews: int | None = None,
) -> None:
    with self._session() as session:
        row = session.get(PendingGameBacklog, slug)
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
        row.last_checked_at = datetime.datetime.now(tz=datetime.UTC).isoformat()
        session.commit()


def update_latest_pending_scores(
    self,
    *,
    slug: str,
    metascore: float | None = None,
    metascore_reviews: int | None = None,
    user_score: float | None = None,
    user_reviews: int | None = None,
) -> None:
    with self._session() as session:
        row = session.get(PendingGameLatest, slug)
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
        row.last_checked_at = datetime.datetime.now(tz=datetime.UTC).isoformat()
        session.commit()


def update_backlog_pending_expiry(self, slug: str, max_queue_days: int) -> None:
    days = _INDEFINITE_DAYS if max_queue_days <= 0 else max_queue_days
    expires_at = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=days)).isoformat()
    with self._session() as session:
        row = session.get(PendingGameBacklog, slug)
        if row is not None:
            row.expires_at = expires_at
            session.commit()


def update_latest_pending_expiry(self, slug: str, max_queue_days: int) -> None:
    days = _INDEFINITE_DAYS if max_queue_days <= 0 else max_queue_days
    expires_at = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=days)).isoformat()
    with self._session() as session:
        row = session.get(PendingGameLatest, slug)
        if row is not None:
            row.expires_at = expires_at
            session.commit()


def get_expired_backlog_pending(self) -> list[PendingGameBacklog]:
    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    with self._session() as session:
        return list(session.query(PendingGameBacklog).filter(PendingGameBacklog.expires_at <= now).all())


def get_expired_latest_pending(self) -> list[PendingGameLatest]:
    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    with self._session() as session:
        return list(session.query(PendingGameLatest).filter(PendingGameLatest.expires_at <= now).all())


def is_backlog_pending(self, slug: str) -> bool:
    with self._session() as session:
        return session.get(PendingGameBacklog, slug) is not None


def is_latest_pending(self, slug: str) -> bool:
    with self._session() as session:
        return session.get(PendingGameLatest, slug) is not None


def has_verified_backlog_pending(self, *, platform: str | None = None) -> bool:
    with self._session() as session:
        query = session.query(PendingGameBacklog).filter(
            PendingGameBacklog.score_checks_passed == True,  # noqa: E712
            PendingGameBacklog.expires_at > datetime.datetime.now(tz=datetime.UTC).isoformat(),
        )
        if platform is not None:
            query = query.filter(PendingGameBacklog.platform == platform)
        return query.first() is not None


def has_verified_latest_pending(self, *, platform: str | None = None) -> bool:
    with self._session() as session:
        query = session.query(PendingGameLatest).filter(
            PendingGameLatest.score_checks_passed == True,  # noqa: E712
            PendingGameLatest.expires_at > datetime.datetime.now(tz=datetime.UTC).isoformat(),
        )
        if platform is not None:
            query = query.filter(PendingGameLatest.platform == platform)
        return query.first() is not None


def get_known_backlog_slugs(self, *, source: str, platform: str) -> set[str]:
    """Return slugs from history + backlog pending table."""
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
        pending_rows = session.query(PendingGameBacklog.slug).all()
        for (slug,) in pending_rows:
            known.add(str(slug))
    return known


def get_known_latest_slugs(self, *, source: str, platform: str) -> set[str]:
    """Return slugs from history + latest pending table."""
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
        pending_rows = session.query(PendingGameLatest.slug).all()
        for (slug,) in pending_rows:
            known.add(str(slug))
    return known
```

- [ ] **Step 4: Write database unit tests**

In `tests/unit/test_database.py`, add:

```python
def test_record_and_get_backlog_pending(tmp_path: Path) -> None:
    """Backlog pending table CRUD works independently."""
    from gamarr.database import Database

    db = Database(tmp_path / "test.db")
    db.record_backlog_pending(
        slug="bg-test",
        game_title="Backlog Game",
        platform="pc",
        metascore=85.0,
    )
    rows = db.get_backlog_pending(platform="pc")
    assert len(rows) == 1
    assert rows[0].game_title == "Backlog Game"
    assert rows[0].metascore == 85.0
    db.close()


def test_record_and_get_latest_pending(tmp_path: Path) -> None:
    """Latest pending table CRUD works independently."""
    from gamarr.database import Database

    db = Database(tmp_path / "test.db")
    db.record_latest_pending(
        slug="lt-test",
        game_title="Latest Game",
        platform="pc",
        metascore=90.0,
    )
    rows = db.get_latest_pending(platform="pc")
    assert len(rows) == 1
    assert rows[0].game_title == "Latest Game"
    assert rows[0].metascore == 90.0
    db.close()


def test_backlog_and_latest_are_independent(tmp_path: Path) -> None:
    """Backlog and latest pending tables are isolated."""
    from gamarr.database import Database

    db = Database(tmp_path / "test.db")
    db.record_backlog_pending(slug="shared-slug", game_title="Backlog Only", platform="pc")
    db.record_latest_pending(slug="shared-slug", game_title="Latest Only", platform="pc")

    assert len(db.get_backlog_pending()) == 1
    assert len(db.get_latest_pending()) == 1
    assert db.get_backlog_pending()[0].game_title == "Backlog Only"
    assert db.get_latest_pending()[0].game_title == "Latest Only"
    db.close()


def test_remove_pending_mode_specific(tmp_path: Path) -> None:
    """remove_backlog_pending only affects backlog table."""
    from gamarr.database import Database

    db = Database(tmp_path / "test.db")
    db.record_backlog_pending(slug="x", game_title="X", platform="pc")
    db.record_latest_pending(slug="x", game_title="X", platform="pc")
    db.remove_backlog_pending("x")
    assert len(db.get_backlog_pending()) == 0
    assert len(db.get_latest_pending()) == 1  # unaffected
    db.close()


def test_update_pending_scores_mode_specific(tmp_path: Path) -> None:
    """update_backlog_pending_scores sets score_checks_passed."""
    from gamarr.database import Database

    db = Database(tmp_path / "test.db")
    db.record_backlog_pending(slug="bg", game_title="BG", platform="pc")
    db.update_backlog_pending_scores(slug="bg", metascore=92.0, metascore_reviews=15)
    rows = db.get_backlog_pending()
    assert rows[0].score_checks_passed is True
    assert rows[0].metascore == 92.0
    assert rows[0].metascore_reviews == 15
    db.close()


def test_has_verified_pending_mode_specific(tmp_path: Path) -> None:
    """has_verified_backlog_pending looks only at backlog table."""
    from gamarr.database import Database

    db = Database(tmp_path / "test.db")
    assert not db.has_verified_backlog_pending(platform="pc")
    db.record_backlog_pending(slug="v", game_title="V", platform="pc")
    db.update_backlog_pending_scores(slug="v", metascore=80.0)
    assert db.has_verified_backlog_pending(platform="pc")
    assert not db.has_verified_latest_pending(platform="pc")
    db.close()


def test_known_slugs_mixed_mode_shared_history(tmp_path: Path) -> None:
    """Known slugs include history (shared) + mode-specific pending."""
    from gamarr.database import Database

    db = Database(tmp_path / "test.db")
    db.record_processed(source="metacritic", source_url="mc:old-game", source_title="Old", result="Passed")
    db.record_backlog_pending(slug="bg-pending", game_title="BG", platform="pc")
    db.record_latest_pending(slug="lt-pending", game_title="LT", platform="pc")

    backlog_known = db.get_known_backlog_slugs(source="metacritic", platform="pc")
    latest_known = db.get_known_latest_slugs(source="metacritic", platform="pc")

    # Both see history ("old-game")
    assert "old-game" in backlog_known
    assert "old-game" in latest_known
    # Each sees only its own pending
    assert "bg-pending" in backlog_known
    assert "bg-pending" not in latest_known
    assert "lt-pending" in latest_known
    assert "lt-pending" not in backlog_known
    db.close()
```

- [ ] **Step 5: Run the new tests**

```bash
uv run pytest tests/unit/test_database.py -k "backlog_pending or latest_pending or mode_specific or known_slugs_mixed" -v
```

Expected: 7 PASS

- [ ] **Step 6: Run full database test suite**

```bash
uv run pytest tests/unit/test_database.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/database.py tests/unit/test_database.py
git commit -m "feat: add mode-specific CRUD methods for backlog/latest pending tables"
```

---

### Task 5: Add `search_mode` to `AcquisitionConfig` and `run_acquisition()`

**Files:**
- Modify: `src/gamarr/pipeline.py` (dataclass + function signature)

- [ ] **Step 1: Add `search_mode` to `AcquisitionConfig`**

In `src/gamarr/pipeline.py`, in the `AcquisitionConfig` dataclass, add after `sort_order`:

```python
search_mode: Literal["backlog", "latest"] = "latest"
```

Add the import at the top of the file if `Literal` isn't already imported (it already is — from `typing`).

- [ ] **Step 2: Add `search_mode` parameter to `run_acquisition()`**

In the `run_acquisition` function signature, add after `sort_order`:

```python
search_mode: Literal["backlog", "latest"] = "latest",
```

And in the `AcquisitionConfig` construction inside `run_acquisition`, add `search_mode=search_mode,` to the cfg assignment.

- [ ] **Step 3: Verify no import errors**

```bash
uv run python -c "from gamarr.pipeline import AcquisitionConfig, run_acquisition; cfg = AcquisitionConfig(min_metascore=75, min_metascore_reviews=10, min_user_score=7.5, min_user_reviews=10); assert cfg.search_mode == 'latest'; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: add search_mode to AcquisitionConfig and run_acquisition"
```

---

### Task 6: Branch browse phase on `search_mode`

**Files:**
- Modify: `src/gamarr/pipeline.py` (`_run_discovery_phases` — browse section)

- [ ] **Step 1: Identify the browse section boundaries**

In `_run_discovery_phases`, the browse section starts at `if cfg.enabled:` and ends at `# NEW: If browsing returned no games AND no cached data exists`.  The entire browse block (including sort_order detection, year-loop, and scrape-health check) is about 60 lines.  We'll wrap this block in a `search_mode` branch.

Find the existing code (approximately lines 190-250 of `_run_discovery_phases`):

```python
    browse_games: list[dict[str, Any]] = []
    new_pending: int = 0
    if cfg.enabled:
        cutoff_date: str | None = None
        previous_sort_order = db.get_last_sort_order(platform)
        ... (sort_order change detection) ...

        scan_year_anchor = datetime.datetime.now(tz=datetime.UTC).year
        mc.sort_order = cfg.sort_order

        if cfg.sort_order == "new":
            years_back = max(0, math.ceil((cfg.max_pages if cfg.max_pages else 500) / 52))
            cutoff_year = scan_year_anchor - years_back
            current_year = scan_year_anchor
        else:
            cutoff_year = 0
            current_year = 0

        # Check if backlog is fully exhausted — if so, reset to page 1
        total_backlog = db.sum_scanned_pages(platform, cutoff_year, current_year)
        max_pages = cfg.max_pages if cfg.max_pages else 0
        if max_pages > 0 and total_backlog >= max_pages:
            db.reset_backlog_progress(platform, cfg.sort_order)

        for scan_year in range(cutoff_year, current_year + 1):
            ... (year-loop with mc.scan_recent_games) ...

        db.set_last_sort_order(platform, cfg.sort_order)
        if browse_games:
            ... (pending queue processing) ...

    # NEW: If browsing returned no games AND no cached data exists...
    if cfg.enabled and not is_cancelled(cancel_event) and not browse_games and cfg.notify_on_scrape_failure:
        ... (scrape health check) ...
```

- [ ] **Step 2: Replace the entire browse block with mode-branched version**

Replace the block from `browse_games: list[dict[str, Any]] = []` through the scrape-health check with:

```python
    browse_games: list[dict[str, Any]] = []
    new_pending: int = 0
    scan_year_anchor = datetime.datetime.now(tz=datetime.UTC).year

    if cfg.enabled:
        cutoff_date: str | None = None

        if cfg.search_mode == "backlog":
            # ── Backlog mode: year-loop with progress tracking ──
            previous_sort_order = db.get_last_sort_order(platform)
            if previous_sort_order is not None and previous_sort_order != cfg.sort_order:
                logger.info(
                    "Sort order changed from '{}' to '{}'",
                    previous_sort_order,
                    cfg.sort_order,
                )
                db.clear_cache("metacritic")
                db.reset_backlog_progress(platform, cfg.sort_order)

            mc.sort_order = cfg.sort_order

            if cfg.sort_order == "new":
                years_back = max(0, math.ceil((cfg.max_pages if cfg.max_pages else 500) / 52))
                cutoff_year = scan_year_anchor - years_back
                current_year = scan_year_anchor
            else:
                cutoff_year = 0
                current_year = 0

            total_backlog = db.sum_scanned_pages(platform, cutoff_year, current_year)
            max_pages = cfg.max_pages if cfg.max_pages else 0

            if max_pages > 0 and total_backlog >= max_pages:
                logger.info(
                    "Backlog complete — {} of {} pages scanned. "
                    "Switch to search_mode: latest for ongoing monitoring.",
                    max_pages,
                    max_pages,
                )
                # Do NOT reset progress — stay in backlog mode boundary
            else:
                for scan_year in range(cutoff_year, current_year + 1):
                    if is_cancelled(cancel_event):
                        break
                    start_page = db.get_last_scanned_page(platform, scan_year) + 1
                    try:
                        year_games = mc.scan_recent_games(
                            platform,
                            cache_pages_hours=cfg.cache_pages_hours,
                            cutoff_date=cutoff_date,
                            cancel_event=cancel_event,
                            start_page=start_page,
                            show_progress=True,
                            year=scan_year if cfg.sort_order == "new" else None,
                            max_pages=cfg.max_cycle_pages
                            if cfg.max_cycle_pages
                            else (cfg.max_pages if cfg.max_pages else 0),
                        )
                        browse_games.extend(year_games)
                        last_page = mc._recent_games_last_page if isinstance(mc._recent_games_last_page, int) else 0
                        db.set_last_scanned_page(platform, scan_year, last_page)
                    except Exception:
                        logger.exception("Scan failed for year {} — will retry next cycle", scan_year)

                db.set_last_sort_order(platform, cfg.sort_order)

        else:
            # ── Latest mode: simple page-1..N scan, no progress tracking ──
            mc.sort_order = cfg.sort_order if cfg.sort_order else "new"
            year = scan_year_anchor if cfg.sort_order == "new" else None
            try:
                browse_games = mc.scan_recent_games(
                    platform,
                    cache_pages_hours=cfg.cache_pages_hours,
                    cutoff_date=None,
                    cancel_event=cancel_event,
                    start_page=1,
                    show_progress=True,
                    year=year,
                    max_pages=cfg.max_cycle_pages if cfg.max_cycle_pages else 0,
                )
            except Exception:
                logger.exception("Latest scan failed — will retry next cycle")

        # ── Shared: process browse results into pending queue ──
        if browse_games:
            thresholds = {
                "min_metascore": cfg.min_metascore,
                "min_metascore_reviews": cfg.min_metascore_reviews,
                "min_user_score": cfg.min_user_score,
                "min_user_reviews": cfg.min_user_reviews,
            }
            new_pending = _process_browse_games(
                browse_games,
                platform,
                db,
                thresholds,
                max_queue_days=cfg.max_queue_days,
                reject_title=cfg.reject_title,
                search_mode=cfg.search_mode,
            )
            pending_queue_len = (
                len(db.get_backlog_pending(platform=platform))
                if cfg.search_mode == "backlog"
                else len(db.get_latest_pending(platform=platform))
            )
            new_this_cycle = max(0, pending_queue_len - (pending_before if cfg.search_mode == "backlog" else 0))
            logger.info(
                "Pending queue: {} new + from previous cycles = {} total",
                new_this_cycle,
                pending_queue_len,
            )
            if new_pending:
                logger.info(
                    "{} of {} collected games passed title/age filters — added to pending queue",
                    new_pending,
                    len(browse_games),
                )

        # ── Shared: scrape-health check ──
        if not is_cancelled(cancel_event) and not browse_games and cfg.notify_on_scrape_failure:
            cached_exists = (
                mc._cache.get_browse_page(platform, 1, ttl_hours=cfg.cache_pages_hours, year=0) is not None
                or mc._cache.get_browse_page(
                    platform,
                    1,
                    ttl_hours=cfg.cache_pages_hours,
                    year=datetime.datetime.now(tz=datetime.UTC).year,
                )
                is not None
            )
            if not cached_exists:
                _diagnose_and_notify_scrape(
                    notifier,
                    _check_scrape_health(),
                    "Metacritic browse returned no games",
                )
```

- [ ] **Step 3: Set `pending_before` before the browse block**

Before the `if cfg.enabled:` line, add:

```python
        pending_before = len(db.get_pending(platform=platform))
```

Replace that with mode-specific queries — but the variable is only used for logging now, initialized just before its use.  Actually, looking at the code flow, `pending_before` is set earlier in the function.  Let me keep the existing `pending_before` initialization and use it correctly in the new code.  The key insight: `pending_before` needs to be set BEFORE the browse block runs.

In the existing code, find:

```python
        pending_before = len(db.get_pending(platform=platform))

        scan_year_anchor = datetime.datetime.now(tz=datetime.UTC).year
```

Replace with mode-specific pending count before browse:

```python
        pending_before = (
            len(db.get_backlog_pending(platform=platform))
            if cfg.search_mode == "backlog"
            else len(db.get_latest_pending(platform=platform))
        )

        scan_year_anchor = datetime.datetime.now(tz=datetime.UTC).year
```

- [ ] **Step 4: Verify no syntax errors**

```bash
uv run python -c "from gamarr.pipeline import run_acquisition; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: branch browse phase on search_mode (backlog vs latest)"
```

---

### Task 7: Update downstream functions for mode-aware dispatch

**Files:**
- Modify: `src/gamarr/pipeline.py` (post-browse phases)

- [ ] **Step 1: Update `_process_browse_games` to accept `search_mode`**

Add `search_mode` parameter and dispatch to correct pending table:

```python
def _process_browse_games(
    browse_games: list[dict[str, Any]],
    platform: str,
    db: Database,
    thresholds: dict[str, Any],
    *,
    max_queue_days: int = 30,
    reject_title: list[str] | None = None,
    search_mode: str = "latest",
) -> int:
```

Inside the function, change the `known_slugs` call:

```python
    known_slugs = (
        db.get_known_backlog_slugs(source="metacritic", platform=platform)
        if search_mode == "backlog"
        else db.get_known_latest_slugs(source="metacritic", platform=platform)
    )
```

And change the `record_pending` call:

```python
        (
            db.record_backlog_pending(
                slug=g_slug,
                ...
            )
            if search_mode == "backlog"
            else db.record_latest_pending(
                slug=g_slug,
                ...
            )
        )
```

- [ ] **Step 2: Update `_verify_pending_scores` to accept `search_mode`**

Add parameter and dispatch pending query:

```python
def _verify_pending_scores(
    db: Database,
    mc: MetacriticClient,
    platform: str,
    thresholds: dict[str, Any],
    *,
    cache_details_days: int = 7,
    max_verify: int = 50,
    reject_genre: list[str] | None = None,
    reject_title: list[str] | None = None,
    fitgirl_max_queue_days: int = 60,
    notifier: Any = None,
    cancel_event: threading.Event | None = None,
    search_mode: str = "latest",
) -> int:
    ...
    pending = (
        db.get_backlog_pending(platform=platform)
        if search_mode == "backlog"
        else db.get_latest_pending(platform=platform)
    )
```

In `_process_verify_result`, the calls to `db.touch_pending`, `db.remove_pending`, `db.update_pending_scores`, `db.update_pending_expiry` must also dispatch to mode-specific methods.  Pass `search_mode` through `_process_verify_result` and replace:

```python
    if search_mode == "backlog":
        db.remove_backlog_pending(str(game.slug))
    else:
        db.remove_latest_pending(str(game.slug))
```

Similarly for `touch_pending`, `update_pending_scores`, `update_pending_expiry`.

- [ ] **Step 3: Update `_match_pending_games` to accept `search_mode`**

Add parameter and dispatch pending query:

```python
def _match_pending_games(
    db: Database,
    *,
    qbt: Any = None,
    magnet_fetcher: Callable[[str], str | None] | None = None,
    notifier: Any = None,
    library: Any = None,
    mc: Any = None,
    thresholds: dict[str, Any] | None = None,
    reject_keywords: list[str] | None = None,
    source_name: str = "fitgirl",
    search_mode: str = "latest",
) -> list[dict[str, Any]]:
    ...
    pending = (
        db.get_backlog_pending()
        if search_mode == "backlog"
        else db.get_latest_pending()
    )
```

Inside `_process_single_pending_match`, pass `search_mode` to `_deliver_with_jit_verify` and to `db.touch_pending`/`db.remove_pending`.  In `_deliver_match`, use mode-specific `db.remove_pending`.

- [ ] **Step 4: Update `has_verified_pending` check in `_run_discovery_phases`**

In `_run_discovery_phases`, the check for verified pending before source matching:

```python
    if not is_cancelled(cancel_event) and db.has_verified_pending(platform=platform):
```

Change to:

```python
    has_verified = (
        db.has_verified_backlog_pending(platform=platform)
        if cfg.search_mode == "backlog"
        else db.has_verified_latest_pending(platform=platform)
    )
    if not is_cancelled(cancel_event) and has_verified:
```

- [ ] **Step 5: Update expired-games processing**

In `_match_pending_games`, the call `results.extend(_process_expired_games(db))` uses `db.get_expired_pending()`.  Change `_process_expired_games` to accept `search_mode` and use the mode-specific expired query:

```python
def _process_expired_games(db: Database, *, search_mode: str = "latest") -> list[dict[str, Any]]:
    expired = (
        db.get_expired_backlog_pending()
        if search_mode == "backlog"
        else db.get_expired_latest_pending()
    )
```

- [ ] **Step 6: Update `_log_backlog_progress` call to be backlog-only**

At the end of `_run_discovery_phases`, the `_log_backlog_progress` call:

```python
    if cfg.enabled:
        _log_backlog_progress(platform, db, ...)
```

Guard it with:

```python
    if cfg.enabled and cfg.search_mode == "backlog":
        _log_backlog_progress(platform, db, ...)
```

- [ ] **Step 7: Verify no import or syntax errors**

```bash
uv run python -c "from gamarr.pipeline import run_acquisition; print('OK')"
```

- [ ] **Step 8: Run existing tests to check for regressions**

```bash
uv run pytest tests/unit/test_pipeline.py -v
```

Expected: tests may fail due to expected signature changes — note which ones and we'll update tests in Task 11.

- [ ] **Step 9: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: thread search_mode through all pipeline phases"
```

---

### Task 8: Update `scheduler.py` to pass `search_mode`

**Files:**
- Modify: `src/gamarr/scheduler.py` (`_build_kwargs`)

- [ ] **Step 1: Add `search_mode` to the kwargs dict**

In `_build_kwargs`, add after the `"sort_order"` line:

```python
        "search_mode": mc_cfg.search_mode,
```

- [ ] **Step 2: Verify**

```bash
uv run python -c "from gamarr.scheduler import _build_kwargs; from gamarr.config import Config; c = Config(); kw = _build_kwargs(c); assert 'search_mode' in kw; print(f'search_mode={kw[\"search_mode\"]}')"
```

Expected: `search_mode=latest`

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/scheduler.py
git commit -m "feat: pass search_mode from config through scheduler"
```

---

### Task 9: Update CLI (if it passes `sort_order` or nearby params)

**Files:**
- Modify: `src/gamarr/cli.py` (check if `run_acquisition` is called directly)

- [ ] **Step 1: Check if CLI calls `run_acquisition`**

```bash
grep -n "run_acquisition\|sort_order" src/gamarr/cli.py
```

If `run_acquisition` is invoked with explicit keyword arguments, add `search_mode`.  If it only calls `run()` from scheduler (which calls `_build_kwargs`), no change needed.

- [ ] **Step 2: Commit (if changed) or skip**

---

### Task 10: Update existing pipeline tests for new signatures

**Files:**
- Modify: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Find tests that call changed functions**

```bash
grep -n "run_acquisition\|_process_browse_games\|_verify_pending_scores\|_match_pending_games\|_process_expired_games" tests/unit/test_pipeline.py
```

- [ ] **Step 2: Add `search_mode` parameter to each call**

Wherever `run_acquisition` is called in tests, add `search_mode="backlog"` (to match existing test behavior which tests backlog traversal).

For helper function calls like `_process_browse_games(...)`, add `search_mode="latest"` (the default).

- [ ] **Step 3: Run tests to verify**

```bash
uv run pytest tests/unit/test_pipeline.py -v
```

Fix any failures. Keep iterating until all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add search_mode parameter to pipeline test calls"
```

---

### Task 11: Add pipeline mode-specific tests

**Files:**
- Modify: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Test backlog mode exhaustion log**

```python
def test_backlog_exhausted_logs_completion_and_skips_scan(mocker: Any, tmp_path: Path) -> None:
    """When backlog is fully scanned, log completion and skip browse."""
    from gamarr.pipeline import run_acquisition

    # Use a DB that reports full backlog coverage
    mock_db = mocker.patch("gamarr.database.Database", autospec=True)
    mock_db_instance = mock_db.return_value
    mock_db_instance.sum_scanned_pages.return_value = 20
    mock_db_instance.has_verified_backlog_pending.return_value = False
    mock_db_instance.get_backlog_pending.return_value = []

    mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient", autospec=True)

    result = run_acquisition(
        platform="pc",
        db_path=str(tmp_path / "test.db"),
        min_metascore=75, min_metascore_reviews=10, min_user_score=7.5, min_user_reviews=10,
        enabled=True,
        max_pages=20,
        max_cycle_pages=4,
        search_mode="backlog",
    )
    # scan_recent_games should NOT be called (backlog exhausted)
    mock_mc.return_value.scan_recent_games.assert_not_called()
```

- [ ] **Step 2: Test latest mode uses page 1 with no progress tracking**

```python
def test_latest_mode_scans_page1_no_progress_tracking(mocker: Any, tmp_path: Path) -> None:
    """Latest mode always starts from page 1 and does not track progress."""
    from gamarr.pipeline import run_acquisition

    mock_db = mocker.patch("gamarr.database.Database", autospec=True)
    mock_db_instance = mock_db.return_value
    mock_db_instance.get_latest_pending.return_value = []
    mock_db_instance.has_verified_latest_pending.return_value = False

    mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient", autospec=True)
    mock_mc_instance = mock_mc.return_value
    mock_mc_instance.scan_recent_games.return_value = []

    run_acquisition(
        platform="pc",
        db_path=str(tmp_path / "test.db"),
        min_metascore=75, min_metascore_reviews=10, min_user_score=7.5, min_user_reviews=10,
        enabled=True,
        max_pages=500,
        max_cycle_pages=4,
        search_mode="latest",
    )

    kwargs = mock_mc_instance.scan_recent_games.call_args
    assert kwargs[1]["start_page"] == 1
    assert kwargs[1]["max_pages"] == 4
```

- [ ] **Step 3: Run the new tests**

```bash
uv run pytest tests/unit/test_pipeline.py::test_backlog_exhausted_logs_completion_and_skips_scan tests/unit/test_pipeline.py::test_latest_mode_scans_page1_no_progress_tracking -v
```

Expected: 2 PASS

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add mode-specific pipeline tests (backlog exhaustion, latest mode)"
```

---

### Task 12: Final verification — full test suite

**Files:**
- (all modified files)

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest tests/ -v --timeout=60
```

Expected: all PASS.  Fix any failures.

- [ ] **Step 2: Run type checker**

```bash
uv run mypy src/gamarr/ --strict
```

Fix any type errors.

- [ ] **Step 3: Run linter**

```bash
uv run ruff check --fix src/gamarr/ && uv run ruff format src/gamarr/
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: final verification — full test suite, type check, lint"
```<｜end▁of▁thinking｜>The plan is getting quite long. Let me continue with the remaining tasks.

<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="bash">
<｜｜DSML｜｜parameter name="command" string="true">wc -l /data/gamarr/docs/plans/2026-07-08-search-mode-plan.md