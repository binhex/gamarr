"""Tests for gamarr database module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gamarr.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class TestDatabase:
    """Database CRUD operations."""

    def test_create_db_creates_tables(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.close()

    def test_is_processed_returns_false_for_new_entry(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        assert db.is_processed("fitgirl", "http://example.com/game") is False
        db.close()

    def test_is_processed_returns_true_after_insert(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        db.record_processed(
            source="fitgirl",
            source_title="Test Game [Repack]",
            source_url="http://example.com/game",
            game_title="Test Game",
            platform="pc",
            metascore=80.0,
            user_score=8.0,
            result="Passed",
            result_details="All checks passed",
            magnet_url="magnet:?xt=urn:btih:abc",
        )
        assert db.is_processed("fitgirl", "http://example.com/game") is True
        db.close()

    def test_record_failed_entry(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        db.record_processed(
            source="fitgirl",
            source_title="Bad Game [Repack]",
            source_url="http://example.com/bad",
            game_title="Bad Game",
            platform="pc",
            metascore=30.0,
            user_score=2.0,
            result="Failed",
            result_details="Score below threshold",
        )
        assert db.is_processed("fitgirl", "http://example.com/bad") is True
        db.close()

    def test_get_stats_returns_counts(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        stats = db.get_stats()
        assert "total" in stats
        assert "passed" in stats
        assert "failed" in stats
        assert stats["total"] == 0
        assert stats["passed"] == 0
        db.close()

    def test_get_stats_counts_correctly(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        db.record_processed(source="fitgirl", source_title="A", source_url="http://a", result="Passed")
        db.record_processed(source="fitgirl", source_title="B", source_url="http://b", result="Failed", metascore=50.0)
        stats = db.get_stats()
        assert stats["total"] == 2
        assert stats["passed"] == 1
        assert stats["failed"] == 1
        db.close()
