"""SQLite history database using SQLAlchemy for gamarr."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

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


class SourceTitle(Base):
    """ORM mapping for the ``source_titles`` table."""

    __tablename__ = "source_titles"

    source: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, primary_key=True)


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
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)
        self._migrate()
        logger.debug("Database opened at '{}'", self._db_path)

    def _migrate(self) -> None:
        """Add columns added in newer versions of gamarr."""
        try:
            inspector = sa_inspect(self._engine)
            columns = [c["name"] for c in inspector.get_columns("pending_games")]
            if "score_checks_passed" not in columns:
                with self._session() as session:
                    session.execute(text("ALTER TABLE pending_games ADD COLUMN score_checks_passed INTEGER"))
                    session.commit()
                logger.debug("Added score_checks_passed column to pending_games")
        except Exception:
            pass  # Migration best-effort

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
            row.score_checks_passed = True
            now = datetime.datetime.now(tz=datetime.UTC).isoformat()
            row.last_checked_at = now
            session.commit()

    def is_pending(self, slug: str) -> bool:
        with self._session() as session:
            return session.get(PendingGame, slug) is not None

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

    def rebuild_source_titles(self, source: str, titles: list[dict[str, str]]) -> None:
        with self._session() as session:
            session.query(SourceTitle).filter(SourceTitle.source == source).delete()
            for entry in titles:
                session.add(
                    SourceTitle(
                        source=source,
                        title=entry["title"],
                        url=entry["url"],
                    )
                )
            session.commit()

    def get_all_source_titles(self, source: str) -> list[dict[str, str]]:
        """Return all source title/url pairs for *source*."""
        with self._session() as session:
            rows = session.query(SourceTitle).filter(SourceTitle.source == source).order_by(SourceTitle.url).all()
        return [{"title": str(row.title), "url": str(row.url)} for row in rows]

    def match_source_title(self, source: str, normalized_title: str) -> list[dict[str, str]]:
        from gamarr.utils import normalise_for_compare

        with self._session() as session:
            rows = session.query(SourceTitle).filter(SourceTitle.source == source).all()
        results = []
        for row in rows:
            if normalise_for_compare(str(row.title)) == normalized_title:
                results.append({"title": str(row.title), "url": str(row.url)})
        return results

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
