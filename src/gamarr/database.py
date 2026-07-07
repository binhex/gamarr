"""SQLite history database using SQLAlchemy for gamarr."""

from __future__ import annotations

import contextlib
import datetime
import json
from pathlib import Path
from typing import Any, cast

from loguru import logger
from sqlalchemy import Boolean, Float, Integer, String, Text, create_engine, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

# Days to use for indefinite pending expiry (when max_queue_days is 0 or negative).
_INDEFINITE_DAYS: int = 9999


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


class HistoryRow(Base):
    """ORM mapping for the ``history`` table."""

    __tablename__ = "history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_title: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    game_title: Mapped[str | None] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    metascore: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[str] = mapped_column(String, nullable=False)
    result_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    magnet_url: Mapped[str | None] = mapped_column(String, nullable=True)
    torrent_tag: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_at: Mapped[str] = mapped_column(String, nullable=False)


class PendingGame(Base):
    """ORM mapping for the ``pending_games`` table."""

    __tablename__ = "pending_games"

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
    verify_attempts: Mapped[int] = mapped_column(Integer, default=0)


class SourceTitle(Base):
    """ORM mapping for the ``source_titles`` table."""

    __tablename__ = "source_titles"

    source: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, primary_key=True)
    magnet: Mapped[str | None] = mapped_column(String, nullable=True)


class GameDetailCache(Base):
    """ORM mapping for the ``game_detail_cache`` table."""

    __tablename__ = "game_detail_cache"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    metascore: Mapped[float | None] = mapped_column(Float, nullable=True)
    metascore_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    genres: Mapped[str | None] = mapped_column(String, nullable=True)
    must_play: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    release_date: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    cached_at: Mapped[str] = mapped_column(String, nullable=False)


class BrowsePageCache(Base):
    """ORM mapping for the ``browse_page_cache`` table."""

    __tablename__ = "browse_page_cache"

    platform: Mapped[str] = mapped_column(String, primary_key=True)
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    games_json: Mapped[str] = mapped_column(Text, nullable=False)
    cached_at: Mapped[str] = mapped_column(String, nullable=False)


class SitemapCache(Base):
    """Tracks when each source's sitemap was last fetched."""

    __tablename__ = "sitemap_cache"

    source: Mapped[str] = mapped_column(String, primary_key=True)
    cached_at: Mapped[str] = mapped_column(String, nullable=False)


class ScanState(Base):
    """Tracks pipeline state per platform."""

    __tablename__ = "scan_state"

    platform: Mapped[str] = mapped_column(String, primary_key=True)
    last_cutoff_date: Mapped[str | None] = mapped_column(String, nullable=True)
    last_max_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sort_order: Mapped[str | None] = mapped_column(String, nullable=True)


class Database:
    """SQLite history database for tracking processed titles."""

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        # Support in-memory SQLite database
        if str(path) == ":memory:":
            self._db_path = ":memory:"
        elif path.suffix:
            self._db_path = str(path)
        else:
            path.mkdir(parents=True, exist_ok=True)
            self._db_path = str(path / "gamarr.db")

        self._engine = create_engine(f"sqlite:///{self._db_path}", echo=False)
        # WAL mode + busy_timeout for concurrent readers from thread pool
        with self._engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA busy_timeout=5000"))
            conn.commit()
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)
        self._migrate()
        logger.debug("Database opened at '{}'", self._db_path)

    def _migrate(self) -> None:
        """Add columns added in newer versions of gamarr."""
        self._migrate_pending_games()
        self._migrate_game_detail_cache()
        self._migrate_source_titles()
        self._migrate_scan_state()
        self._migrate_browse_cache()

    def _migrate_pending_games(self) -> None:
        """Add columns to pending_games that were added in newer versions."""
        try:
            inspector = sa_inspect(self._engine)
            columns = [c["name"] for c in inspector.get_columns("pending_games")]
            if "score_checks_passed" not in columns:
                with self._session() as session:
                    session.execute(text("ALTER TABLE pending_games ADD COLUMN score_checks_passed INTEGER"))
                    session.commit()
                logger.debug("Added score_checks_passed column to pending_games")
            if "verify_attempts" not in columns:
                with self._session() as session:
                    session.execute(text("ALTER TABLE pending_games ADD COLUMN verify_attempts INTEGER DEFAULT 0"))
                    session.commit()
                logger.debug("Added verify_attempts column to pending_games")
        except Exception:
            logger.debug("Migration of pending_games skipped (table may not exist yet)")

    def _migrate_game_detail_cache(self) -> None:
        """Add metadata columns to game_detail_cache that were added in newer versions."""
        try:
            inspector = sa_inspect(self._engine)
            detail_columns = [c["name"] for c in inspector.get_columns("game_detail_cache")]
            for col, col_type in (
                ("genres", "TEXT"),
                ("must_play", "INTEGER"),
                ("release_date", "TEXT"),
                ("description", "TEXT"),
            ):
                if col not in detail_columns:
                    with self._session() as session:
                        session.execute(text(f"ALTER TABLE game_detail_cache ADD COLUMN {col} {col_type}"))
                        session.commit()
                    logger.debug("Added '{}' column to game_detail_cache", col)
        except Exception:
            logger.debug("Migration of game_detail_cache skipped (table may not exist yet)")

    def _migrate_source_titles(self) -> None:
        """Add magnet column to source_titles if missing."""
        try:
            inspector = sa_inspect(self._engine)
            columns = [c["name"] for c in inspector.get_columns("source_titles")]
            if "magnet" not in columns:
                with self._session() as session:
                    session.execute(text("ALTER TABLE source_titles ADD COLUMN magnet VARCHAR"))
                    session.commit()
                logger.debug("Added magnet column to source_titles")
        except Exception:
            logger.debug("Migration of source_titles skipped (table may not exist yet)")

    def _migrate_scan_state(self) -> None:
        """Add missing columns to scan_state if not present."""
        try:
            inspector = sa_inspect(self._engine)
            columns = [c["name"] for c in inspector.get_columns("scan_state")]
            if "last_max_weeks" not in columns:
                with self._session() as session:
                    session.execute(text("ALTER TABLE scan_state ADD COLUMN last_max_weeks INTEGER"))
                    session.commit()
                logger.debug("Added last_max_weeks column to scan_state")
            if "last_sort_order" not in columns:
                with self._session() as session:
                    session.execute(text("ALTER TABLE scan_state ADD COLUMN last_sort_order VARCHAR"))
                    session.commit()
                logger.debug("Added last_sort_order column to scan_state")
        except Exception:
            logger.debug("Migration of scan_state skipped (table may not exist yet)")

    def _migrate_browse_cache(self) -> None:
        """Add year column to browse_page_cache and fix primary key."""
        try:
            inspector = sa_inspect(self._engine)
            pk = inspector.get_pk_constraint("browse_page_cache")
            pk_cols = pk.get("constrained_columns", [])

            if "year" not in pk_cols:
                # Old schema: year not in PK.  Drop and recreate the table so
                # the PRIMARY KEY is (platform, page_number, year).  Cached
                # data is lost but will be repopulated on the next fetch.
                BrowsePageCache.__table__.drop(self._engine)  # type: ignore[attr-defined]
                BrowsePageCache.__table__.create(self._engine)  # type: ignore[attr-defined]
                logger.debug("Recreated browse_page_cache with year in primary key")
        except Exception:
            logger.debug("Migration of browse_cache skipped (table may not exist yet)")

    def close(self) -> None:
        self._engine.dispose()

    def _session(self) -> Session:
        return self._session_factory()

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
        with self._session() as session:
            existing = session.get(PendingGame, slug)
            if existing is not None:
                return  # Already pending
            row = PendingGame(
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

    def get_pending(self, *, platform: str | None = None) -> list[PendingGame]:
        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        with self._session() as session:
            query = session.query(PendingGame).filter(PendingGame.expires_at > now)
            if platform is not None:
                query = query.filter(PendingGame.platform == platform)
            rows = query.all()
            return list(rows)

    def get_last_cutoff(self, platform: str) -> str | None:
        """Return the last stored cutoff date for *platform*, or None."""
        with self._session() as session:
            row = session.get(ScanState, platform)
        if row is None:
            return None
        return str(row.last_cutoff_date) if row.last_cutoff_date else None

    def set_last_cutoff(self, platform: str, cutoff_date: str) -> None:
        """Store or update the cutoff date for *platform*."""
        with self._session() as session:
            row = session.get(ScanState, platform)
            if row is None:
                session.add(ScanState(platform=platform, last_cutoff_date=cutoff_date))
            else:
                row.last_cutoff_date = cutoff_date
            session.commit()

    def get_last_sort_order(self, platform: str) -> str | None:
        """Return the last stored sort_order for *platform*, or None."""
        with self._session() as session:
            row = session.get(ScanState, platform)
        if row is None:
            return None
        return row.last_sort_order

    def set_last_sort_order(self, platform: str, sort_order: str) -> None:
        """Store or update the sort_order value for *platform*."""
        with self._session() as session:
            row = session.get(ScanState, platform)
            if row is None:
                session.add(ScanState(platform=platform, last_sort_order=sort_order))
            else:
                row.last_sort_order = sort_order
            session.commit()

    def get_expired_pending(self) -> list[PendingGame]:
        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        with self._session() as session:
            rows = session.query(PendingGame).filter(PendingGame.expires_at <= now).all()
            return list(rows)

    def touch_pending(self, slug: str) -> None:
        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        with self._session() as session:
            row = session.get(PendingGame, slug)
            if row is not None:
                row.last_checked_at = now
                session.commit()

    def increment_verify_attempts(self, slug: str) -> int:
        """Increment the verify_attempts counter and return the new value."""
        with self._session() as session:
            row = session.get(PendingGame, slug)
            if row is None:
                return 0
            row.verify_attempts = (row.verify_attempts or 0) + 1
            session.commit()
            return row.verify_attempts

    def reset_verify_attempts(self, slug: str) -> None:
        """Reset verify_attempts to 0 (e.g. after a successful score check)."""
        with self._session() as session:
            row = session.get(PendingGame, slug)
            if row is not None:
                row.verify_attempts = 0
                session.commit()

    def update_pending_expiry(self, slug: str, max_queue_days: int) -> None:
        """Recalculate expires_at to now + max_queue_days for a pending game.

        Used to extend the expiry window when a game transitions from
        the score-waiting phase to the FitGirl-matching phase.

        When *max_queue_days* is 0 or negative, the expiry is set to a far
        future date (~27 years), making the game pend indefinitely.
        """
        days = _INDEFINITE_DAYS if max_queue_days <= 0 else max_queue_days
        expires_at = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=days)).isoformat()
        with self._session() as session:
            row = session.get(PendingGame, slug)
            if row is not None:
                row.expires_at = expires_at
                session.commit()

    def remove_pending(self, slug: str) -> None:
        with self._session() as session:
            row = session.get(PendingGame, slug)
            if row is not None:
                session.delete(row)
                session.commit()

    def update_pending_scores(
        self,
        *,
        slug: str,
        metascore: float | None = None,
        metascore_reviews: int | None = None,
        user_score: float | None = None,
        user_reviews: int | None = None,
    ) -> None:
        """Update the Metacritic scores for a pending game with real detail-page values."""
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

    def is_pending(self, slug: str) -> bool:
        with self._session() as session:
            return session.get(PendingGame, slug) is not None

    def get_known_slugs(self, *, source: str, platform: str) -> set[str]:
        """Return the set of all slugs already processed or pending.

        Replaces N+1 ``is_processed`` + ``is_pending`` calls made per game
        during browse processing.  Instead of querying the DB for each of
        35K games individually, two batch queries collect all known slugs.

        Args:
            source: The source name (e.g. ``"metacritic"``).
            platform: Target platform to filter pending slugs.

        Returns:
            Set of slug strings that are already known.
        """

        known: set[str] = set()
        with self._session() as session:
            # Slugs from history table: source_url is "mc:{slug}"
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
            # Slugs from pending_games table (no platform filter — matches
            # old ``is_pending(slug)`` behavior which checked by PK only).
            pending_rows = session.query(PendingGame.slug).all()
            for (slug,) in pending_rows:
                known.add(str(slug))
        return known

    def has_verified_pending(self, *, platform: str | None = None) -> bool:
        """Return True if any score-checked games are waiting in the queue."""
        with self._session() as session:
            query = session.query(PendingGame).filter(
                PendingGame.score_checks_passed == True,  # noqa: E712
                PendingGame.expires_at > datetime.datetime.now(tz=datetime.UTC).isoformat(),
            )
            if platform is not None:
                query = query.filter(PendingGame.platform == platform)
            return query.first() is not None

    def store_source_title(self, *, source: str, title: str, url: str, magnet: str | None) -> None:
        """Insert a single source title entry.

        Used for incremental indexing where individual entries are added
        without replacing the entire source index (unlike rebuild_source_titles).

        Args:
            source: Source identifier (e.g. "freegog").
            title: Cleaned game title.
            url: Full game page URL.
            magnet: Magnet URI or None.
        """
        with self._session() as session:
            session.add(
                SourceTitle(
                    source=source,
                    title=title,
                    url=url,
                    magnet=magnet,
                )
            )
            session.commit()

    def upsert_source_title(self, *, source: str, title: str, url: str, magnet: str | None) -> None:
        """Insert or replace a source title entry atomically.

        Deletes any existing row with the same (source, url), then inserts
        the new row — all in a single transaction to avoid a crash window
        between two separate commits.

        Args:
            source: Source identifier (e.g. "freegog").
            title: Cleaned game title.
            url: Full game page URL.
            magnet: Magnet URI or None.
        """
        with self._session() as session:
            session.query(SourceTitle).filter(
                SourceTitle.source == source,
                SourceTitle.url == url,
            ).delete()
            session.add(
                SourceTitle(
                    source=source,
                    title=title,
                    url=url,
                    magnet=magnet,
                )
            )
            session.commit()

    def rebuild_source_titles(self, source: str, titles: list[dict[str, str | None]]) -> None:
        with self._session() as session:
            session.query(SourceTitle).filter(SourceTitle.source == source).delete()
            for entry in titles:
                session.add(
                    SourceTitle(
                        source=source,
                        title=entry["title"],
                        url=entry["url"],
                        magnet=entry.get("magnet"),
                    )
                )
            session.commit()

    def get_all_source_titles(self, source: str) -> list[dict[str, str | None]]:
        """Return all source title/url/magnet pairs for *source*."""
        with self._session() as session:
            rows = session.query(SourceTitle).filter(SourceTitle.source == source).order_by(SourceTitle.url).all()
        return [{"title": str(row.title), "url": str(row.url), "magnet": row.magnet} for row in rows]

    @staticmethod
    def _normalised_title_matches(db_title: str, query: str) -> int:
        """Return a match score if *db_title* matches *query*, or 0 if no match.

        Uses normalised title strings (via ``normalise_for_compare``).

        Returns a score where higher values indicate a better match:
        - ``2``: exact match (best possible)
        - ``1``: *query* is a substring of *db_title* and both are >= 5 chars.
          This direction handles FitGirl titles that have version/bonus info
          appended (e.g. ``"MOUSE: P.I. For Hire – v1.0.1.8044 + 2 Bonus
          DLCs"``). The reverse direction (db_title inside a longer query)
          is NOT a match, to prevent false positives when a Metacritic game
          title happens to contain a shorter FitGirl title as a prefix
          (e.g. ``"DAVE THE DIVER: In the Jungle"`` vs ``"Dave The Diver"``).
        - ``0``: no match
        """
        if db_title == query:
            return 2
        if len(db_title) < 5 or len(query) < 5:
            return 0
        if query in db_title:
            return 1
        return 0

    def match_source_title(self, source: str, normalized_title: str) -> list[dict[str, str | None]]:
        from gamarr.utils import normalise_for_compare

        with self._session() as session:
            rows = session.query(SourceTitle).filter(SourceTitle.source == source).all()
        matched: list[tuple[int, float, str, str, str | None]] = []
        for row in rows:
            row_normalised = normalise_for_compare(str(row.title))
            score = self._normalised_title_matches(row_normalised, normalized_title)
            if score:
                # Sort key: higher score first, then better length ratio (closer to 1.0)
                shorter = min(len(row_normalised), len(normalized_title))
                longer = max(len(row_normalised), len(normalized_title))
                ratio = shorter / longer if longer > 0 else 0.0
                matched.append((score, ratio, row.title, row.url, row.magnet))

        # Sort by score descending, then by ratio descending (better match first)
        matched.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [{"title": t, "url": u, "magnet": m} for (_, _, t, u, m) in matched]

    def get_sitemap_cache(self, source: str, ttl_hours: int) -> bool:
        """Return True if the sitemap for *source* was cached within *ttl_hours*."""
        if ttl_hours <= 0:
            return False
        cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=ttl_hours)).isoformat()
        with self._session() as session:
            row = session.get(SitemapCache, source)
            if row is None:
                return False
            return row.cached_at > cutoff

    def get_sitemap_cache_expiry(self, source: str, ttl_hours: int) -> str | None:
        """Return the cache expiry time as a formatted string, or None if not cached."""
        if ttl_hours <= 0:
            return None
        with self._session() as session:
            row = session.get(SitemapCache, source)
            if row is None:
                return None
            cached = datetime.datetime.fromisoformat(row.cached_at)
            expiry = cached + datetime.timedelta(hours=ttl_hours)
            return expiry.strftime("%Y-%m-%d %H:%M:%S")

    def set_sitemap_cache(self, source: str) -> None:
        """Update the sitemap cache timestamp for *source* to now."""
        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        with self._session() as session:
            row = session.get(SitemapCache, source)
            if row is None:
                session.add(SitemapCache(source=source, cached_at=now))
            else:
                row.cached_at = now
            session.commit()

    def clear_cache(self, source: str) -> None:
        """Clear cached data for a given source.

        Args:
            source: One of ``"fitgirl"``, ``"freegog"``, or ``"metacritic"``.
        """
        if source == "fitgirl":
            self._delete_sitemap_cache("fitgirl")
        elif source == "freegog":
            self._delete_sitemap_cache("freegog")
            self._delete_source_titles("freegog")
        elif source == "metacritic":
            self._delete_browse_cache()
            self._delete_detail_cache()
        else:
            logger.warning("Unknown cache source '{}' \u2014 skipping", source)

    def _delete_sitemap_cache(self, source: str) -> None:
        with self._session() as session:
            session.execute(text("DELETE FROM sitemap_cache WHERE source = :source"), {"source": source})
            session.commit()

    def _delete_source_titles(self, source: str) -> None:
        with self._session() as session:
            session.query(SourceTitle).filter(SourceTitle.source == source).delete()
            session.commit()

    def _delete_browse_cache(self) -> None:
        with self._session() as session:
            session.execute(text("DELETE FROM browse_page_cache"))
            session.commit()

    def _delete_detail_cache(self) -> None:
        with self._session() as session:
            session.execute(text("DELETE FROM game_detail_cache"))
            session.commit()

    def get_game_detail_cache(self, slug: str, ttl_days: int) -> dict[str, Any] | None:
        """Return cached game detail dict or None if expired/missing."""
        if ttl_days <= 0:
            return None
        cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=ttl_days)).isoformat()
        with self._session() as session:
            row = session.get(GameDetailCache, slug)
            if row is None or row.cached_at <= cutoff:
                return None
            genres: list[str] | None = None
            if row.genres is not None:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    genres = json.loads(row.genres)
            return {
                "metascore": row.metascore,
                "metascore_reviews": row.metascore_reviews,
                "user_score": row.user_score,
                "user_reviews": row.user_reviews,
                "genres": genres,
                "must_play": row.must_play,
                "release_date": row.release_date,
                "description": row.description,
            }

    def set_game_detail_cache(
        self,
        slug: str,
        metascore: float | None = None,
        metascore_reviews: int | None = None,
        user_score: float | None = None,
        user_reviews: int | None = None,
        genres: list[str] | None = None,
        must_play: bool | None = None,
        release_date: str | None = None,
        description: str | None = None,
    ) -> None:
        """Insert or update a game detail cache entry.

        Uses SQLite ``INSERT OR REPLACE`` (via SQLAlchemy ``text()``)
        to atomically insert-or-update at the database level,
        avoiding the TOCTOU race between ``session.get()`` and
        ``session.add()`` that occurs when concurrent threads
        (from ``_process_verify_batch``'s ``ThreadPoolExecutor``)
        both check the same slug before either commits.
        """

        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        genres_json: str | None = json.dumps(genres) if genres is not None else None
        with self._session() as session:
            session.execute(
                text("""
                    INSERT OR REPLACE INTO game_detail_cache
                    (slug, metascore, metascore_reviews, user_score, user_reviews,
                     genres, must_play, release_date, description, cached_at)
                    VALUES (:slug, :metascore, :metascore_reviews, :user_score,
                            :user_reviews, :genres, :must_play, :release_date,
                            :description, :cached_at)
                """),
                {
                    "slug": slug,
                    "metascore": metascore,
                    "metascore_reviews": metascore_reviews,
                    "user_score": user_score,
                    "user_reviews": user_reviews,
                    "genres": genres_json,
                    "must_play": must_play,
                    "release_date": release_date,
                    "description": description,
                    "cached_at": now,
                },
            )
            session.commit()

    def get_browse_page_cache(
        self,
        platform: str,
        page_number: int,
        ttl_hours: int,
        *,
        year: int = 0,
    ) -> list[dict[str, Any]] | None:
        """Return cached browse page games list or None if expired/missing.

        Args:
            platform: Target platform.
            page_number: Browse page number.
            ttl_hours: Cache TTL in hours.
            year: Year filter.  ``0`` matches rows stored without a year
                (e.g. legacy all-time entries or fallback lookups).
        """
        if ttl_hours <= 0:
            return None
        cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=ttl_hours)).isoformat()
        with self._session() as session:
            row = (
                session.query(BrowsePageCache).filter_by(platform=platform, page_number=page_number, year=year).first()
            )
            if row is None or row.cached_at <= cutoff:
                return None
            return cast("list[dict[str, Any]]", json.loads(row.games_json))

    def set_browse_page_cache(
        self,
        platform: str,
        page_number: int,
        games: list[dict[str, Any]],
        *,
        year: int = 0,
    ) -> None:
        """Insert or replace a browse page cache entry."""
        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        with self._session() as session:
            row = (
                session.query(BrowsePageCache).filter_by(platform=platform, page_number=page_number, year=year).first()
            )
            if row is None:
                session.add(
                    BrowsePageCache(
                        platform=platform,
                        page_number=page_number,
                        year=year,
                        games_json=json.dumps(games),
                        cached_at=now,
                    )
                )
            else:
                row.games_json = json.dumps(games)
                row.cached_at = now
            session.commit()

    def is_processed(self, source: str, source_url: str) -> bool:
        with self._session() as session:
            count = (
                session.query(HistoryRow)
                .filter(
                    HistoryRow.source == source,
                    HistoryRow.source_url == source_url,
                )
                .count()
            )
            return count > 0

    def record_processed(
        self,
        *,
        source: str,
        source_title: str,
        source_url: str | None = None,
        game_title: str | None = None,
        platform: str = "pc",
        metascore: float | None = None,
        user_score: float | None = None,
        result: str = "Passed",
        result_details: str = "",
        magnet_url: str | None = None,
        torrent_tag: str | None = None,
    ) -> None:
        with self._session() as session:
            row = HistoryRow(
                source=source,
                source_title=source_title,
                source_url=source_url if source_url is not None else source_title,
                game_title=game_title,
                platform=platform,
                metascore=metascore,
                user_score=user_score,
                result=result,
                result_details=result_details,
                magnet_url=magnet_url,
                torrent_tag=torrent_tag,
                processed_at=datetime.datetime.now(tz=datetime.UTC).isoformat(),
            )
            session.add(row)
            session.commit()

    def get_stats(self) -> dict[str, Any]:
        with self._session() as session:
            total = session.query(HistoryRow).count()
            passed = session.query(HistoryRow).filter(HistoryRow.result == "Passed").count()
            failed = session.query(HistoryRow).filter(HistoryRow.result == "Failed").count()
            already_owned = session.query(HistoryRow).filter(HistoryRow.result == "Already owned").count()
            error = session.query(HistoryRow).filter(HistoryRow.result == "Error").count()
            expired = session.query(HistoryRow).filter(HistoryRow.result == "Expired").count()
            return {
                "total": total,
                "passed": passed,
                "failed": failed,
                "already_owned": already_owned,
                "error": error,
                "expired": expired,
            }
