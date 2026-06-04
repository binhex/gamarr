"""SQLite history database using SQLAlchemy for gamarr."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import Column, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


class HistoryRow(Base):
    """ORM mapping for the ``history`` table."""

    __tablename__ = "history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False, index=True)
    source_title = Column(String, nullable=False)
    source_url = Column(String, nullable=True)
    game_title = Column(String, nullable=True)
    platform = Column(String, nullable=False)
    metascore = Column(Float, nullable=True)
    user_score = Column(Float, nullable=True)
    result = Column(String, nullable=False)
    result_details = Column(Text, nullable=True)
    magnet_url = Column(String, nullable=True)
    torrent_tag = Column(String, nullable=True)
    processed_at = Column(String, nullable=False)


class Database:
    """SQLite history database for tracking processed titles."""

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        if path.suffix:
            self._db_path = str(path)
        else:
            path.mkdir(parents=True, exist_ok=True)
            self._db_path = str(path / "gamarr.db")

        self._engine = create_engine(f"sqlite:///{self._db_path}", echo=False)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)
        logger.debug("Database opened at '{}'", self._db_path)

    def close(self) -> None:
        self._engine.dispose()

    def _session(self) -> Session:
        return self._session_factory()

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
            return {"total": total, "passed": passed, "failed": failed}
