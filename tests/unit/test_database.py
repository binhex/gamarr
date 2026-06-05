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


class TestPendingGame:
    """PendingGame CRUD operations."""

    def test_insert_and_retrieve(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            metascore_reviews=120,
            user_score=8.5,
            user_reviews=5000,
            genres=["Action", "RPG"],
            release_date="2022-02-25",
            expires_at="2026-07-05T00:00:00",
        )
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        assert pending[0].slug == "elden-ring"
        assert pending[0].game_title == "Elden Ring"
        db.close()

    def test_remove_pending(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        db.record_pending(
            slug="test-game",
            game_title="Test Game",
            platform="pc",
            expires_at="2026-07-05T00:00:00",
        )
        db.remove_pending("test-game")
        pending = db.get_pending(platform="pc")
        assert len(pending) == 0
        db.close()

    def test_is_pending_returns_true_for_existing(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        db.record_pending(
            slug="test-game",
            game_title="Test Game",
            platform="pc",
            expires_at="2026-07-05T00:00:00",
        )
        assert db.is_pending("test-game") is True
        assert db.is_pending("unknown-game") is False
        db.close()

    def test_record_pending_duplicate_slug(self, tmp_path: Path) -> None:
        """Inserting the same slug twice should be a no-op."""
        db = Database(str(tmp_path / "test.db"))
        db.record_pending(
            slug="test-game",
            game_title="Original",
            platform="pc",
            expires_at="2026-07-05T00:00:00",
        )
        db.record_pending(
            slug="test-game",
            game_title="Duplicate",
            platform="pc",
            expires_at="2026-07-05T00:00:00",
        )
        pending = db.get_pending()
        assert len(pending) == 1
        assert pending[0].game_title == "Original"  # unchanged
        db.close()

    def test_get_expired_pending(self, tmp_path: Path) -> None:
        """Games past their expiry should appear in get_expired_pending."""
        import datetime

        db = Database(str(tmp_path / "test.db"))
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=1)).isoformat()
        db.record_pending(
            slug="old-game",
            game_title="Old Game",
            platform="pc",
            expires_at=past,
        )
        expired = db.get_expired_pending()
        assert len(expired) == 1
        assert expired[0].slug == "old-game"
        db.close()

    def test_touch_pending_updates_timestamp(self, tmp_path: Path) -> None:
        """touch_pending should set last_checked_at."""
        import datetime

        db = Database(str(tmp_path / "test.db"))
        future = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="test-game",
            game_title="Test Game",
            platform="pc",
            expires_at=future,
        )
        db.touch_pending("test-game")
        pending = db.get_pending()
        assert pending[0].last_checked_at is not None
        db.close()

    def test_touch_pending_nonexistent_does_not_error(self, tmp_path: Path) -> None:
        """Touching a non-existent slug should silently do nothing."""
        db = Database(str(tmp_path / "test.db"))
        db.touch_pending("does-not-exist")  # should not raise
        db.close()


class TestSourceTitle:
    """SourceTitle operations."""

    def test_rebuild_and_query(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        titles = [
            {
                "source": "fitgirl",
                "title": "Elden Ring",
                "url": "https://fitgirl-repacks.site/elden-ring/",
            },
        ]
        db.rebuild_source_titles("fitgirl", titles)
        results = db.match_source_title("fitgirl", "elden ring")
        assert len(results) == 1
        assert "Elden Ring" in results[0]["title"]
        db.close()


class TestDatabaseAlreadyOwned:
    """Already owned stats tracking."""

    def test_get_stats_counts_already_owned(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        db.record_processed(source="fitgirl", source_title="A", result="Passed")
        db.record_processed(source="fitgirl", source_title="B", result="Already owned")
        db.record_processed(source="fitgirl", source_title="C", result="Already owned")
        stats = db.get_stats()
        assert stats["total"] == 3
        assert stats["passed"] == 1
        assert stats["already_owned"] == 2
        db.close()
