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
    games_json: Mapped[str] = mapped_column(Text, nullable=False)
    cached_at: Mapped[str] = mapped_column(String, nullable=False)


class SitemapCache(Base):
    """Tracks when each source's sitemap was last fetched."""

    __tablename__ = "sitemap_cache"

    source: Mapped[str] = mapped_column(String, primary_key=True)
    cached_at: Mapped[str] = mapped_column(String, nullable=False)


class ScanState(Base):
    """Tracks the last browse cutoff date per platform for max_cycle_weeks."""

    __tablename__ = "scan_state"

    platform: Mapped[str] = mapped_column(String, primary_key=True)
    last_cutoff_date: Mapped[str | None] = mapped_column(String, nullable=True)


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
        days = 9999 if max_queue_days <= 0 else max_queue_days
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
        - ``1``: substring match, shorter string >= 5 chars
        - ``0``: no match

        The substring fallback handles FitGirl sitemap titles that have
        version/bonus info appended (e.g.
        ``"MOUSE: P.I. For Hire – v1.0.1.8044 + 2 Bonus DLCs"``).
        """
        if db_title == query:
            return 2
        if len(db_title) < 5 or len(query) < 5:
            return 0
        if db_title in query or query in db_title:
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

    def get_browse_page_cache(self, platform: str, page_number: int, ttl_hours: int) -> list[dict[str, Any]] | None:
        """Return cached browse page games list or None if expired/missing."""
        if ttl_hours <= 0:
            return None
        cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=ttl_hours)).isoformat()
        with self._session() as session:
            row = session.get(BrowsePageCache, (platform, page_number))
            if row is None or row.cached_at <= cutoff:
                return None
            return cast("list[dict[str, Any]]", json.loads(row.games_json))

    def set_browse_page_cache(self, platform: str, page_number: int, games: list[dict[str, Any]]) -> None:
        """Insert or replace a browse page cache entry."""
        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        with self._session() as session:
            row = session.get(BrowsePageCache, (platform, page_number))
            if row is None:
                session.add(
                    BrowsePageCache(
                        platform=platform,
                        page_number=page_number,
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
