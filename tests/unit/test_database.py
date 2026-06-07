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

    def test_cache_orm_tables_created(self, tmp_path: Path) -> None:
        """GameDetailCache and BrowsePageCache tables should be created automatically."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        with db._session() as session:
            from sqlalchemy import text

            tables = [row[0] for row in session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))]
        assert "game_detail_cache" in tables
        assert "browse_page_cache" in tables
        assert "sitemap_cache" in tables
        db.close()

    def test_get_game_detail_cache_miss(self, tmp_path: Path) -> None:
        """Fresh cache should return None."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        assert db.get_game_detail_cache("elden-ring", ttl_days=7) is None
        db.close()

    def test_get_game_detail_cache_hit(self, tmp_path: Path) -> None:
        """After setting, cache should return the stored values."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_game_detail_cache("elden-ring", metascore=96.0, metascore_reviews=120, user_score=8.5, user_reviews=5000)
        result = db.get_game_detail_cache("elden-ring", ttl_days=7)
        assert result is not None
        assert result["metascore"] == 96.0
        assert result["metascore_reviews"] == 120
        assert result["user_score"] == 8.5
        assert result["user_reviews"] == 5000
        db.close()

    def test_get_game_detail_cache_expired(self, tmp_path: Path) -> None:
        """An expired cache entry should return None."""
        import datetime

        from gamarr.database import Database, GameDetailCache

        db = Database(str(tmp_path / "test.db"))
        db.set_game_detail_cache("old-game", metascore=50.0, user_score=5.0)
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=14)).isoformat()
        with db._session() as session:
            row = session.get(GameDetailCache, "old-game")
            assert row is not None
            row.cached_at = past
            session.commit()
        assert db.get_game_detail_cache("old-game", ttl_days=7) is None
        db.close()

    def test_get_browse_page_cache_miss(self, tmp_path: Path) -> None:
        """Fresh browse page cache should return None."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        assert db.get_browse_page_cache("pc", 1, ttl_hours=4) is None
        db.close()

    def test_get_browse_page_cache_hit(self, tmp_path: Path) -> None:
        """After setting, browse cache should return games list."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        games = [{"title": "Game", "slug": "game"}]
        db.set_browse_page_cache("pc", 1, games)
        result = db.get_browse_page_cache("pc", 1, ttl_hours=4)
        assert result is not None
        assert result[0]["slug"] == "game"
        db.close()

    def test_get_sitemap_cache_zero_ttl(self, tmp_path: Path) -> None:
        """With ttl_hours <= 0, get_sitemap_cache should return False."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        assert db.get_sitemap_cache("fitgirl", ttl_hours=0) is False
        db.close()

    def test_get_sitemap_cache_miss(self, tmp_path: Path) -> None:
        """Before setting, sitemap cache should miss."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        assert db.get_sitemap_cache("fitgirl", ttl_hours=6) is False
        db.close()

    def test_get_sitemap_cache_hit(self, tmp_path: Path) -> None:
        """After setting, sitemap cache should hit."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_sitemap_cache("fitgirl")
        assert db.get_sitemap_cache("fitgirl", ttl_hours=6) is True
        db.close()

    def test_get_sitemap_cache_expired(self, tmp_path: Path) -> None:
        """An expired sitemap cache should miss."""
        import datetime

        from gamarr.database import Database, SitemapCache

        db = Database(str(tmp_path / "test.db"))
        db.set_sitemap_cache("fitgirl")
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=12)).isoformat()
        with db._session() as session:
            row = session.get(SitemapCache, "fitgirl")
            assert row is not None
            row.cached_at = past
            session.commit()
        assert db.get_sitemap_cache("fitgirl", ttl_hours=6) is False
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

    def test_directory_based_db_path_creates_file(self, tmp_path: Path) -> None:
        """When db_path has no suffix, Database should create a subdirectory and gamarr.db."""
        from pathlib import Path

        from gamarr.database import Database

        db_dir = str(tmp_path / "subdir")
        db = Database(db_dir)
        assert Path(str(tmp_path / "subdir" / "gamarr.db")).exists()
        db.close()

    def test_get_all_source_titles_empty(self, tmp_path: Path) -> None:
        """get_all_source_titles returns empty list when no titles exist."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        titles = db.get_all_source_titles("fitgirl")
        assert titles == []
        db.close()

    def test_update_pending_scores_nonexistent_slug(self, tmp_path: Path) -> None:
        """update_pending_scores should silently skip rows that don't exist."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        # Should not raise
        db.update_pending_scores(slug="nonexistent", metascore=85.0)
        assert not db.is_pending("nonexistent")
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
