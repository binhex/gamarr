"""Tests for gamarr database module."""

from __future__ import annotations

import datetime
from datetime import UTC
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

    def test_scan_state_migration_idempotent(self, tmp_path: Path) -> None:
        """_migrate_scan_state must add missing columns to an existing table."""
        from sqlalchemy import create_engine, text

        db_path = str(tmp_path / "old_schema.db")
        # Simulate an old gamarr DB where scan_state exists but lacks
        # the columns added later (last_max_weeks).
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE scan_state (platform TEXT PRIMARY KEY, last_cutoff_date TEXT)"))
            conn.commit()
        engine.dispose()

        # Opening with Database should migrate and add the missing columns
        db = Database(db_path)
        # Should not raise — migration handled it
        db.close()

        # Second open: migration should be a no-op (columns already exist)
        db2 = Database(db_path)
        db2.close()

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

    def test_browse_cache_multiple_years_do_not_conflict(self, tmp_path: Path) -> None:
        """Storing browse pages for different years must NOT raise IntegrityError.

        The PK was originally (platform, page_number) without year.
        Year-specific browsing stores multiple years for the same page.
        Without year in the PK, the second INSERT fails:
        UNIQUE constraint failed: browse_page_cache.platform, browse_page_cache.page_number
        """
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))

        data_2025 = [{"title": "Game 2025", "slug": "game-2025"}]
        data_2026 = [{"title": "Game 2026", "slug": "game-2026"}]

        # Store for year 2025 (page 1)
        db.set_browse_page_cache("pc", 1, data_2025, year=2025)
        # Store for year 2026 (same page 1) — raises IntegrityError without fix
        db.set_browse_page_cache("pc", 1, data_2026, year=2026)

        # Both must be retrievable independently
        cached_2025 = db.get_browse_page_cache("pc", 1, ttl_hours=24, year=2025)
        cached_2026 = db.get_browse_page_cache("pc", 1, ttl_hours=24, year=2026)

        assert cached_2025 is not None, "2025 cache entry lost"
        assert cached_2026 is not None, "2026 cache entry lost"
        assert cached_2025[0]["slug"] == "game-2025"
        assert cached_2026[0]["slug"] == "game-2026"
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
        future = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
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
            expires_at=future,
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
        future = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="test-game",
            game_title="Original",
            platform="pc",
            expires_at=future,
        )
        db.record_pending(
            slug="test-game",
            game_title="Duplicate",
            platform="pc",
            expires_at=future,
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


class TestPendingExpiry:
    """update_pending_expiry method tests."""

    def test_update_pending_expiry(self, tmp_path: Path) -> None:
        """update_pending_expiry should recalculate expires_at to now + max_queue_days."""
        import datetime

        db = Database(str(tmp_path / "test.db"))
        # Insert a pending game with a past expiry
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=10)).isoformat()
        db.record_pending(
            slug="expiry-test",
            game_title="Expiry Test",
            platform="pc",
            expires_at=past,
        )
        # Call update_pending_expiry with 60 days
        db.update_pending_expiry("expiry-test", 60)
        # Retrieve and verify new expiry
        pending = db.get_pending()
        assert len(pending) == 1
        row = pending[0]
        new_expiry = datetime.datetime.fromisoformat(row.expires_at)
        expected_min = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=59)
        expected_max = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=61)
        assert expected_min < new_expiry < expected_max, f"Expected expiry near now+60d, got {new_expiry}"
        db.close()

    def test_update_pending_expiry_nonexistent_slug(self, tmp_path: Path) -> None:
        """update_pending_expiry should silently skip non-existent slugs."""
        db = Database(str(tmp_path / "test.db"))
        db.update_pending_expiry("does-not-exist", 60)  # should not raise
        db.close()


class TestSourceTitle:
    """SourceTitle operations."""

    def test_rebuild_and_query(self, tmp_path: Path) -> None:
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        titles = [
            {
                "source": "fitgirl",
                "title": "Elden Ring",
                "url": "https://fitgirl-repacks.site/elden-ring/",
            },
        ]
        db.rebuild_source_titles("fitgirl", titles)  # type: ignore[arg-type]
        normalized = normalise_for_compare("Elden Ring")
        results = db.match_source_title("fitgirl", normalized)
        assert len(results) == 1
        assert results[0]["title"] is not None
        assert "Elden Ring" in results[0]["title"]
        db.close()

    def test_match_with_version_suffix(self, tmp_path: Path) -> None:
        """FitGirl titles with version/bonus suffixes should still match the base game title.

        Real FitGirl sitemap entries include version strings and bonus
        descriptions appended to the game name, e.g.
        "MOUSE: P.I. For Hire – v1.0.1.8044 + 2 Bonus DLCs".
        The normalised game title "mouse pi for hire" should still match.
        """
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        fitgirl_title = "MOUSE: P.I. For Hire – v1.0.1.8044 + 2 Bonus DLCs"
        titles = [
            {
                "source": "fitgirl",
                "title": fitgirl_title,
                "url": "https://fitgirl-repacks.site/mouse-p-i-for-hire/",
            },
        ]
        db.rebuild_source_titles("fitgirl", titles)  # type: ignore[arg-type]

        game_title = "MOUSE: P.I. For Hire"
        normalized = normalise_for_compare(game_title)
        results = db.match_source_title("fitgirl", normalized)
        assert len(results) == 1, (
            f"Expected match for '{game_title}' against '{fitgirl_title}', got {len(results)} results"
        )
        assert results[0]["title"] is not None
        assert fitgirl_title in results[0]["title"]
        db.close()

    def test_match_url_slug_title(self, tmp_path: Path) -> None:
        """FitGirl titles derived from URL slugs should still match the game title.

        ``_title_from_url`` converts URL slugs like ``mouse-p-i-for-hire``
        to ``Mouse P I For Hire`` (hyphens → spaces, title case).
        The Metacritic title ``MOUSE: P.I. For Hire`` normalises to
        ``"mouse pi for hire"`` while the slug-derived title normalises
        to ``"mouse p i for hire"`` — they differ only by a space in
        the abbreviation ``P.I.`` vs ``P I``.
        """
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        # This is what _title_from_url produces from the FitGirl slug
        slug_title = "Mouse P I For Hire"
        titles = [
            {
                "source": "fitgirl",
                "title": slug_title,
                "url": "https://fitgirl-repacks.site/mouse-p-i-for-hire/",
            },
        ]
        db.rebuild_source_titles("fitgirl", titles)  # type: ignore[arg-type]

        game_title = "MOUSE: P.I. For Hire"
        normalized = normalise_for_compare(game_title)
        results = db.match_source_title("fitgirl", normalized)
        assert len(results) == 1, (
            f"Expected match for '{game_title}' against slug-derived title '{slug_title}', got {len(results)} results"
        )
        db.close()

    def test_no_substring_match_when_metacritic_title_is_longer(self, tmp_path: Path) -> None:
        """FitGirl title that is a prefix of a longer Metacritic title should NOT match.

        Regression test for a bug where "DAVE THE DIVER: In the Jungle" (new
        standalone game on Metacritic) matched "Dave The Diver" (original game
        on FitGirl) because the normalised FitGirl title "davethediver" is a
        substring of the normalised Metacritic title "davethediverinthejungle".

        The substring fallback should only match when the FitGirl title *contains*
        the query (i.e. FitGirl has version/bonus metadata appended), not the
        reverse where a shorter FitGirl title is inside a longer Metacritic title.
        """
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        fitgirl_title = "Dave The Diver"
        titles = [
            {
                "source": "fitgirl",
                "title": fitgirl_title,
                "url": "https://fitgirl-repacks.site/dave-the-diver/",
            },
        ]
        db.rebuild_source_titles("fitgirl", titles)  # type: ignore[arg-type]

        # The Metacritic game has a longer title that contains the FitGirl
        # title as a prefix — this should NOT match.
        game_title = "DAVE THE DIVER: In the Jungle"
        normalized = normalise_for_compare(game_title)
        results = db.match_source_title("fitgirl", normalized)
        assert len(results) == 0, (
            f"Substring match should NOT trigger when FitGirl title "
            f"'{fitgirl_title}' is a prefix of Metacritic title '{game_title}', "
            f"got {len(results)} match(es)"
        )
        db.close()

    def test_source_title_with_magnet(self) -> None:
        """rebuild_source_titles stores magnets, get_all_source_titles returns them."""
        db = Database(":memory:")
        db.rebuild_source_titles(
            "fitgirl",
            [
                {
                    "title": "Elden Ring",
                    "url": "https://fitgirl-repacks.site/elden-ring/",
                    "magnet": "magnet:?xt=urn:btih:abc",
                },
                {"title": "Hades II", "url": "https://fitgirl-repacks.site/hades-ii/"},
            ],
        )
        titles = db.get_all_source_titles("fitgirl")
        assert len(titles) == 2
        assert titles[0]["title"] == "Elden Ring"
        assert titles[0]["magnet"] == "magnet:?xt=urn:btih:abc"
        assert titles[1]["title"] == "Hades II"
        assert titles[1]["magnet"] is None
        db.close()

    def test_store_source_title_inserts_single_entry(self) -> None:
        """store_source_title should insert a single SourceTitle row."""
        db = Database(":memory:")
        db.store_source_title(
            source="freegog",
            title="Test Game",
            url="https://example.com/game/",
            magnet="magnet:?xt=urn:btih:abc",
        )
        titles = db.get_all_source_titles("freegog")
        assert len(titles) == 1
        assert titles[0]["title"] == "Test Game"
        assert titles[0]["url"] == "https://example.com/game/"
        assert titles[0]["magnet"] == "magnet:?xt=urn:btih:abc"
        db.close()

    def test_store_source_title_with_none_magnet(self) -> None:
        """store_source_title should accept magnet=None."""
        db = Database(":memory:")
        db.store_source_title(
            source="freegog",
            title="Test",
            url="https://example.com/game/",
            magnet=None,
        )
        titles = db.get_all_source_titles("freegog")
        assert len(titles) == 1
        assert titles[0]["magnet"] is None
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


class TestGameDetailCacheMetadata:
    """Game detail cache should store and return metadata (genres, etc.)."""

    def test_get_game_detail_cache_with_genres(self, tmp_path: Path) -> None:
        """set_game_detail_cache should store genres and get_game_detail_cache should return them.

        Reproduces the bug where the cache only stores score fields, so
        games served from cache have genres=None, must_play=None, etc.
        """
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_game_detail_cache(
            "opus-prism-peak",
            metascore=86.0,
            metascore_reviews=32,
            user_score=8.3,
            user_reviews=38,
            genres=["Adventure", "Third-Person"],
            must_play=True,
            release_date="2026-04-16",
        )
        result = db.get_game_detail_cache("opus-prism-peak", ttl_days=7)
        assert result is not None
        assert result["metascore"] == 86.0
        assert result["metascore_reviews"] == 32
        assert result["user_score"] == 8.3
        assert result["user_reviews"] == 38
        assert result["genres"] == ["Adventure", "Third-Person"]
        assert result["must_play"] is True
        assert result["release_date"] == "2026-04-16"
        db.close()

    def test_get_game_detail_cache_updates_metadata(self, tmp_path: Path) -> None:
        """Updating an existing cache entry should replace metadata."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        # First insert
        db.set_game_detail_cache(
            "test-game",
            metascore=80.0,
            metascore_reviews=10,
            user_score=7.5,
            user_reviews=50,
            genres=["Action"],
        )
        # Update with different metadata
        db.set_game_detail_cache(
            "test-game",
            metascore=85.0,
            metascore_reviews=20,
            user_score=8.0,
            user_reviews=100,
            genres=["Action", "RPG"],
            must_play=True,
            release_date="2026-01-01",
        )
        result = db.get_game_detail_cache("test-game", ttl_days=7)
        assert result is not None
        assert result["metascore"] == 85.0
        assert result["genres"] == ["Action", "RPG"]
        assert result["must_play"] is True
        assert result["release_date"] == "2026-01-01"
        db.close()

    def test_get_game_detail_cache_genres_none_on_bad_json(self, tmp_path: Path) -> None:
        """Corrupt genres JSON in cache should return None for genres, not crash."""
        from gamarr.database import Database, GameDetailCache

        db = Database(str(tmp_path / "test.db"))
        db.set_game_detail_cache(
            "bad-genres-game",
            metascore=85.0,
            user_score=8.0,
            genres=["Action"],
        )
        # Manually corrupt the genres JSON
        with db._session() as session:
            row = session.get(GameDetailCache, "bad-genres-game")
            assert row is not None
            row.genres = "not-valid-json{"
            session.commit()
        result = db.get_game_detail_cache("bad-genres-game", ttl_days=7)
        assert result is not None
        assert result["metascore"] == 85.0
        assert result["genres"] is None  # Should gracefully degrade
        db.close()

    def test_game_detail_cache_migration_adds_metadata_columns(self, tmp_path: Path) -> None:
        """_migrate() should add genres, must_play, release_date, description columns."""
        from sqlalchemy import Column, Float, Integer, MetaData, String, Table, create_engine
        from sqlalchemy import inspect as sa_inspect

        # Create a database with the pre-migration schema (missing metadata columns)
        db_path = str(tmp_path / "pre_migrate.db")
        engine = create_engine(f"sqlite:///{db_path}")
        metadata = MetaData()
        Table(
            "game_detail_cache",
            metadata,
            Column("slug", String, primary_key=True),
            Column("metascore", Float),
            Column("metascore_reviews", Integer),
            Column("user_score", Float),
            Column("user_reviews", Integer),
            Column("cached_at", String),
        )
        metadata.create_all(engine)
        engine.dispose()

        # Now open with Database — _migrate() should add the missing columns
        db = Database(db_path)
        inspector = sa_inspect(db._engine)
        columns = [c["name"] for c in inspector.get_columns("game_detail_cache")]
        assert "genres" in columns, "Migration should add genres column"
        assert "must_play" in columns, "Migration should add must_play column"
        assert "release_date" in columns, "Migration should add release_date column"
        assert "description" in columns, "Migration should add description column"
        db.close()


class TestMigration:
    """Database schema migration tests."""

    def test_migrate_adds_missing_columns(self, tmp_path: Path) -> None:
        """_migrate() should add score_checks_passed column."""
        from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine
        from sqlalchemy import inspect as sa_inspect

        # Create a database with the pre-migration schema (missing migration columns)
        db_path = str(tmp_path / "pre_migrate.db")
        engine = create_engine(f"sqlite:///{db_path}")
        metadata = MetaData()
        Table(
            "pending_games",
            metadata,
            Column("slug", String, primary_key=True),
            Column("game_title", Text, nullable=False),
            Column("platform", String, nullable=False),
            Column("metascore", Integer),
            Column("user_score", Integer),
            Column("release_date", String),
            Column("expires_at", String),
            Column("created_at", String),
            Column("last_checked_at", String),
        )
        metadata.create_all(engine)
        engine.dispose()

        # Now open with Database — _migrate() should add the missing columns
        db = Database(db_path)
        inspector = sa_inspect(db._engine)
        columns = [c["name"] for c in inspector.get_columns("pending_games")]
        assert "score_checks_passed" in columns, "Migration should add score_checks_passed column"
        db.close()

    def test_migrate_already_has_columns(self, tmp_path: Path) -> None:
        """_migrate() should not error when columns already exist."""
        db = Database(str(tmp_path / "already_migrated.db"))
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(db._engine)
        columns = [c["name"] for c in inspector.get_columns("pending_games")]
        assert "score_checks_passed" in columns
        db.close()

    def test_migrate_drops_verify_attempts_column(self, tmp_path: Path) -> None:
        """_migrate() should drop verify_attempts column to prevent NOT NULL crash."""
        import datetime

        from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, create_engine
        from sqlalchemy import inspect as sa_inspect

        db_path = str(tmp_path / "old_schema.db")
        engine = create_engine(f"sqlite:///{db_path}")
        metadata = MetaData()
        Table(
            "pending_games",
            metadata,
            Column("slug", String, primary_key=True),
            Column("game_title", String, nullable=False),
            Column("platform", String, nullable=False),
            Column("metascore", Integer),
            Column("metascore_reviews", Integer),
            Column("user_score", Integer),
            Column("user_reviews", Integer),
            Column("genres", String),
            Column("release_date", String),
            Column("discovered_at", String),
            Column("expires_at", String),
            Column("last_checked_at", String),
            Column("score_checks_passed", Boolean),
            Column("verify_attempts", Integer, nullable=False, server_default="0"),
        )
        metadata.create_all(engine)
        engine.dispose()

        # Open with Database — _migrate() should drop verify_attempts
        db = Database(db_path)
        inspector = sa_inspect(db._engine)
        columns = [c["name"] for c in inspector.get_columns("pending_games")]
        assert "verify_attempts" not in columns, "Migration should drop verify_attempts column"

        # Recording a pending game should NOT raise IntegrityError
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="test-game",
            game_title="Test Game",
            platform="pc",
            expires_at=expires,
        )
        assert db.is_pending("test-game") is True
        db.close()

    def test_browse_cache_migration_adds_year_column(self, tmp_path: Path) -> None:
        """_migrate_browse_cache must add year column to existing browse_page_cache table."""
        from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine
        from sqlalchemy import inspect as sa_inspect

        # Create a database with the pre-migration schema (no year column)
        db_path = str(tmp_path / "old_browse_schema.db")
        engine = create_engine(f"sqlite:///{db_path}")
        metadata = MetaData()
        Table(
            "browse_page_cache",
            metadata,
            Column("platform", String, primary_key=True),
            Column("page_number", Integer, primary_key=True),
            Column("games_json", Text, nullable=False),
            Column("cached_at", String, nullable=False),
        )
        metadata.create_all(engine)
        engine.dispose()

        # Opening with Database should add the year column
        db = Database(db_path)
        inspector = sa_inspect(db._engine)
        columns = [c["name"] for c in inspector.get_columns("browse_page_cache")]
        assert "year" in columns, "Migration should add year column to browse_page_cache"
        db.close()

    def test_get_last_cutoff_returns_none_when_not_set(self, tmp_path: Path) -> None:
        """get_last_cutoff returns None when no cutoff has been stored."""
        db = Database(str(tmp_path / "test.db"))
        result = db.get_last_cutoff("pc")
        assert result is None
        db.close()

    def test_set_and_get_last_cutoff(self, tmp_path: Path) -> None:
        """set_last_cutoff stores and get_last_cutoff retrieves the value."""
        db = Database(str(tmp_path / "test.db"))
        db.set_last_cutoff("pc", "2026-04-17")
        result = db.get_last_cutoff("pc")
        assert result == "2026-04-17"
        # Different platform returns None
        result = db.get_last_cutoff("ps5")
        assert result is None
        db.close()

    def test_set_last_cutoff_updates_value(self, tmp_path: Path) -> None:
        """set_last_cutoff overwrites an existing value."""
        db = Database(str(tmp_path / "test.db"))
        db.set_last_cutoff("pc", "2026-04-17")
        db.set_last_cutoff("pc", "2026-03-20")
        result = db.get_last_cutoff("pc")
        assert result == "2026-03-20"
        db.close()


class TestGameDetailCacheConcurrent:
    """Concurrent cache writes must not raise IntegrityError."""

    def test_concurrent_same_slug_no_race(self, tmp_path: Path) -> None:
        """Calling set_game_detail_cache for the same slug from multiple
        threads must not raise IntegrityError.  The fix uses SQLite
        ``INSERT OR REPLACE`` to atomically insert-or-update at the
        database level, eliminating the TOCTOU race that the old
        ``session.get()`` + ``session.add()`` pattern had.
        """
        import threading

        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        slug = "race-condition-slug"
        errors: list[Exception] = []
        lock = threading.Lock()

        def set_cache() -> None:
            try:
                db.set_game_detail_cache(slug, metascore=85.0, user_score=8.0)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=set_cache) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent cache calls raised {len(errors)} error(s): {errors}"

        # Verify the entry was stored and readable
        cached = db.get_game_detail_cache(slug, ttl_days=7)
        assert cached is not None
        assert cached["metascore"] == 85.0
        db.close()


class TestMigrationSourceTitles:
    """Migration adds magnet column to source_titles."""

    def test_migrate_adds_magnet_column_to_source_titles(self, tmp_path: Path) -> None:
        """_migrate() should add the magnet column to source_titles if missing.

        Simulates an old database created before the magnet column was added
        to the SourceTitle ORM model. Without the migration, calling
        rebuild_source_titles with magnet data raises:
            sqlite3.OperationalError: table source_titles has no column named magnet
        """
        from sqlalchemy import Column, MetaData, String, Table, create_engine
        from sqlalchemy import inspect as sa_inspect

        # Create a database with the old source_titles schema (no magnet column)
        db_path = str(tmp_path / "pre_magnet.db")
        engine = create_engine(f"sqlite:///{db_path}")
        metadata = MetaData()
        Table(
            "source_titles",
            metadata,
            Column("source", String, primary_key=True),
            Column("title", String, nullable=False),
            Column("url", String, primary_key=True),
        )
        metadata.create_all(engine)
        engine.dispose()

        # Open with Database — _migrate() MUST add the magnet column
        db = Database(db_path)

        # Verify column was added
        inspector = sa_inspect(db._engine)
        columns = [c["name"] for c in inspector.get_columns("source_titles")]
        assert "magnet" in columns, "Migration should add magnet column to source_titles"

        # Verify rebuild_source_titles works after migration
        db.rebuild_source_titles(
            "fitgirl",
            [
                {"title": "Test Game", "url": "https://example.com/game", "magnet": None},
                {
                    "title": "Fetched Game",
                    "url": "https://fitgirl-repacks.site/game-1",
                    "magnet": "magnet:?xt=urn:btih:abc",
                },
            ],
        )
        titles = db.get_all_source_titles("fitgirl")
        assert len(titles) == 2
        # get_all_source_titles orders by URL — find entries by title
        by_title = {t["title"]: t["magnet"] for t in titles}
        assert by_title["Test Game"] is None
        assert by_title["Fetched Game"] == "magnet:?xt=urn:btih:abc"
        db.close()

    def test_migrate_source_titles_already_has_magnet(self, tmp_path: Path) -> None:
        """_migrate() should not error when magnet column already exists."""
        from sqlalchemy import inspect as sa_inspect

        db = Database(str(tmp_path / "fresh.db"))
        inspector = sa_inspect(db._engine)
        columns = [c["name"] for c in inspector.get_columns("source_titles")]
        assert "magnet" in columns
        db.close()


class TestKnownSlugs:
    """Batch slug lookup — replaces N+1 is_processed + is_pending calls."""

    def test_get_known_slugs_returns_processed_and_pending(self, tmp_path: Path) -> None:
        """get_known_slugs returns slugs from history and pending tables.

        ``_is_game_eligible`` calls ``is_processed`` and ``is_pending`` for
        every browse game individually — 70K+ DB round-trips for 35K games.
        ``get_known_slugs`` replaces this with a single batch query.
        """
        db = Database(str(tmp_path / "test.db"))

        # Games that are already processed (via record_processed)
        db.record_processed(
            source="metacritic",
            source_title="Game A",
            source_url="mc:game-a",
            game_title="Game A",
            result="Passed",
        )
        db.record_processed(
            source="metacritic",
            source_title="Game B",
            source_url="mc:game-b",
            game_title="Game B",
            result="Passed",
        )

        # Game that is pending (not yet processed)
        db.record_pending(
            slug="game-c",
            game_title="Game C",
            platform="pc",
            expires_at="2099-01-01T00:00:00",
        )

        # Game that is BOTH processed and pending (should appear once)
        db.record_processed(
            source="metacritic",
            source_title="Game D",
            source_url="mc:game-d",
            game_title="Game D",
            result="Passed",
        )
        db.record_pending(
            slug="game-d",
            game_title="Game D",
            platform="pc",
            expires_at="2099-01-01T00:00:00",
        )

        slugs = db.get_known_slugs(source="metacritic", platform="pc")
        assert "game-a" in slugs, "Should include processed slug"
        assert "game-b" in slugs, "Should include processed slug"
        assert "game-c" in slugs, "Should include pending-only slug"
        assert "game-d" in slugs, "Should include slug in both tables"
        assert len(slugs) == 4, "Should have 4 unique slugs"
        db.close()

    def test_get_known_slugs_empty(self, tmp_path: Path) -> None:
        """get_known_slugs returns empty set when nothing is known."""
        db = Database(str(tmp_path / "empty.db"))
        slugs = db.get_known_slugs(source="metacritic", platform="pc")
        assert isinstance(slugs, set)
        assert len(slugs) == 0
        db.close()

    def test_get_known_slugs_includes_all_platforms(self, tmp_path: Path) -> None:
        """get_known_slugs includes pending slugs regardless of platform.

        Matches old ``is_pending(slug)`` behavior which checked by slug
        primary key only, without platform filtering.
        """
        db = Database(str(tmp_path / "by_platform.db"))
        # Pending on different platform — should still be returned
        db.record_pending(
            slug="ps5-game",
            game_title="PS5 Game",
            platform="ps5",
            expires_at="2099-01-01T00:00:00",
        )
        # Pending on matching platform
        db.record_pending(
            slug="pc-game",
            game_title="PC Game",
            platform="pc",
            expires_at="2099-01-01T00:00:00",
        )
        slugs = db.get_known_slugs(source="metacritic", platform="pc")
        assert "pc-game" in slugs
        assert "ps5-game" in slugs, "Should include cross-platform pending slugs"
        assert len(slugs) == 2
        db.close()


class TestBacklogProgress:
    """Tests for the backlog_progress table and CRUD methods."""

    def test_default_returns_zero(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        assert db.get_last_scanned_page("pc", 2026) == 0
        db.close()

    def test_set_and_get(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 42)
        assert db.get_last_scanned_page("pc", 2026) == 42
        db.close()

    def test_overwrite(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        db.set_last_scanned_page("pc", 2026, 20)
        assert db.get_last_scanned_page("pc", 2026) == 20
        db.close()

    def test_per_year_isolation(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        db.set_last_scanned_page("pc", 2025, 50)
        assert db.get_last_scanned_page("pc", 2026) == 10
        assert db.get_last_scanned_page("pc", 2025) == 50
        db.close()

    def test_per_platform_isolation(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        db.set_last_scanned_page("ps4", 2026, 5)
        assert db.get_last_scanned_page("pc", 2026) == 10
        assert db.get_last_scanned_page("ps4", 2026) == 5
        db.close()

    def test_sum_scanned_pages_single_year(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        assert db.sum_scanned_pages("pc", 2026, 2026) == 10
        db.close()

    def test_sum_scanned_pages_multi_year(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        db.set_last_scanned_page("pc", 2025, 50)
        db.set_last_scanned_page("pc", 2024, 0)
        assert db.sum_scanned_pages("pc", 2024, 2026) == 60
        db.close()

    def test_sum_scanned_pages_empty_returns_zero(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        assert db.sum_scanned_pages("pc", 2020, 2025) == 0
        db.close()

    def test_reset_backlog_progress_new_sort(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 2026, 10)
        db.set_last_scanned_page("pc", 2025, 50)
        db.reset_backlog_progress("pc", "new")
        assert db.get_last_scanned_page("pc", 2026) == 0
        assert db.get_last_scanned_page("pc", 2025) == 0
        db.close()

    def test_reset_backlog_progress_metascore_sentinel(self, tmp_path: Path) -> None:
        """Reset deletes all rows for the platform regardless of sort_order."""
        from gamarr.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.set_last_scanned_page("pc", 0, 25)
        db.set_last_scanned_page("pc", 2026, 10)
        db.reset_backlog_progress("pc", "metascore")
        assert db.get_last_scanned_page("pc", 0) == 0
        assert db.get_last_scanned_page("pc", 2026) == 0  # all rows deleted
        db.close()


class TestPostProcessColumns:
    """Tests for post_process_state and post_process_copied_at columns."""

    def test_history_columns_migrated(self, tmp_path: Path) -> None:
        """New columns should exist after Database init on an existing DB."""
        from sqlalchemy import create_engine, text

        db_path = str(tmp_path / "old_history.db")
        # Create a DB with old schema (no new columns)
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE history ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "source VARCHAR NOT NULL, source_title VARCHAR NOT NULL, "
                "source_url VARCHAR, game_title VARCHAR, platform VARCHAR NOT NULL, "
                "metascore FLOAT, user_score FLOAT, result VARCHAR NOT NULL, "
                "result_details TEXT, magnet_url VARCHAR, torrent_tag VARCHAR, "
                "processed_at VARCHAR NOT NULL)"
            ))
            conn.execute(text("INSERT INTO history (source, source_title, platform, result, processed_at) "
                            "VALUES ('fitgirl', 'Test Game', 'pc', 'Passed', '2025-01-01T00:00:00')"))
            conn.commit()

        db = Database(db_path)
        # After init, new columns should exist
        with db._session() as session:
            columns = [row[1] for row in session.execute(text("PRAGMA table_info(history)"))]
        assert "genres" in columns
        assert "post_process_state" in columns
        assert "post_process_copied_at" in columns
        db.close()

    def test_record_processed_with_genres(self, tmp_path: Path) -> None:
        """record_processed should accept and store a genres string."""
        from sqlalchemy import text

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.record_processed(
            source="fitgirl",
            source_title="Elden Ring [Repack]",
            source_url="http://example.com/elden",
            game_title="Elden Ring",
            platform="pc",
            result="Passed",
            torrent_tag="gamarr-test123",
            genres="Action, RPG",
        )
        with db._session() as session:
            row = session.execute(
                text("SELECT genres, torrent_tag FROM history WHERE torrent_tag = 'gamarr-test123'")
            ).first()
        assert row is not None
        assert row[0] == "Action, RPG"
        db.close()

    def test_find_by_tag_returns_row(self, tmp_path: Path) -> None:
        """find_by_tag should return the matching HistoryRow."""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.record_processed(
            source="fitgirl",
            source_title="Test Game",
            source_url="http://example.com/test",
            game_title="Test Game",
            platform="pc",
            result="Passed",
            torrent_tag="gamarr-findme",
            genres="Action",
        )
        row = db.find_by_tag("gamarr-findme")
        assert row is not None
        assert row.game_title == "Test Game"
        assert row.source == "fitgirl"
        assert row.platform == "pc"
        assert row.genres == "Action"
        db.close()

    def test_find_by_tag_returns_none_for_unknown_tag(self, tmp_path: Path) -> None:
        """find_by_tag should return None for a non-existent tag."""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        row = db.find_by_tag("gamarr-nope")
        assert row is None
        db.close()


class TestClearCache:
    """Database.clear_cache method tests."""

    def test_clear_cache_fitgirl(self, tmp_path: Path) -> None:
        """clear_cache('fitgirl') deletes the fitgirl sitemap cache row."""
        db = Database(str(tmp_path / "test.db"))
        db.set_sitemap_cache("fitgirl")
        db.clear_cache("fitgirl")
        assert not db.get_sitemap_cache("fitgirl", 9999)
        db.close()

    def test_clear_cache_metacritic(self, tmp_path: Path) -> None:
        """clear_cache('metacritic') clears browse + detail caches, leaves sitemap alone."""
        from datetime import datetime

        from sqlalchemy import text

        db = Database(str(tmp_path / "test.db"))

        # Insert a browse cache row
        with db._session() as session:
            session.execute(
                text(
                    "INSERT INTO browse_page_cache (platform, page_number, year, games_json, cached_at) "
                    "VALUES (:p, :pn, :y, :j, :ca)"
                ),
                {"p": "pc", "pn": 1, "y": 0, "j": "[]", "ca": datetime.now(UTC).isoformat()},
            )
            # Insert a detail cache row
            session.execute(
                text("INSERT INTO game_detail_cache (slug, metascore, cached_at) VALUES (:s, :m, :ca)"),
                {"s": "test-game", "m": 85, "ca": datetime.now(UTC).isoformat()},
            )
            session.commit()

        db.set_sitemap_cache("fitgirl")
        db.clear_cache("metacritic")

        # Browse + detail caches cleared
        with db._session() as session:
            row = session.execute(text("SELECT COUNT(*) FROM browse_page_cache")).scalar()
        assert row == 0, f"browse_page_cache has {row} rows"

        with db._session() as session:
            row = session.execute(text("SELECT COUNT(*) FROM game_detail_cache")).scalar()
        assert row == 0, f"game_detail_cache has {row} rows"

        # Sitemap cache untouched
        assert db.get_sitemap_cache("fitgirl", 9999)
        db.close()

    def test_clear_cache_unknown_source_logs_warning(self, tmp_path: Path) -> None:
        """clear_cache with an unrecognised source logs a warning."""
        from unittest.mock import patch

        db = Database(str(tmp_path / "test.db"))
        with patch("gamarr.database.logger.warning") as mock_warning:
            db.clear_cache("nonexistent")
            mock_warning.assert_called_once_with("Unknown cache source '{}' \u2014 skipping", "nonexistent")
        db.close()


class TestPendingModeSplit:
    """Tests for backlog/latest mode-split pending tables."""

    def test_record_and_get_backlog_pending(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_backlog_pending(slug="bg-test", game_title="Backlog Game", platform="pc", metascore=85.0)
        rows = db.get_backlog_pending(platform="pc")
        assert len(rows) == 1
        assert rows[0].game_title == "Backlog Game"
        assert rows[0].metascore == 85.0
        db.close()

    def test_record_and_get_latest_pending(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_latest_pending(slug="lt-test", game_title="Latest Game", platform="pc", metascore=90.0)
        rows = db.get_latest_pending(platform="pc")
        assert len(rows) == 1
        assert rows[0].game_title == "Latest Game"
        assert rows[0].metascore == 90.0
        db.close()

    def test_backlog_and_latest_are_independent(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_backlog_pending(slug="shared-slug", game_title="Backlog Only", platform="pc")
        db.record_latest_pending(slug="shared-slug", game_title="Latest Only", platform="pc")
        assert len(db.get_backlog_pending()) == 1
        assert len(db.get_latest_pending()) == 1
        assert db.get_backlog_pending()[0].game_title == "Backlog Only"
        assert db.get_latest_pending()[0].game_title == "Latest Only"
        db.close()

    def test_remove_pending_mode_specific(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_backlog_pending(slug="x", game_title="X", platform="pc")
        db.record_latest_pending(slug="x", game_title="X", platform="pc")
        db.remove_backlog_pending("x")
        assert len(db.get_backlog_pending()) == 0
        assert len(db.get_latest_pending()) == 1
        db.close()

    def test_update_pending_scores_mode_specific(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_backlog_pending(slug="bg", game_title="BG", platform="pc")
        db.update_backlog_pending_scores(slug="bg", metascore=92.0, metascore_reviews=15)
        rows = db.get_backlog_pending()
        assert rows[0].score_checks_passed is True
        assert rows[0].metascore == 92.0
        assert rows[0].metascore_reviews == 15
        db.close()

    def test_has_verified_pending_mode_specific(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        assert not db.has_verified_backlog_pending(platform="pc")
        db.record_backlog_pending(slug="v", game_title="V", platform="pc")
        db.update_backlog_pending_scores(slug="v", metascore=80.0)
        assert db.has_verified_backlog_pending(platform="pc")
        assert not db.has_verified_latest_pending(platform="pc")
        db.close()

    def test_known_slugs_mixed_mode_shared_history(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_processed(source="metacritic", source_url="mc:old-game", source_title="Old", result="Passed")
        db.record_backlog_pending(slug="bg-pending", game_title="BG", platform="pc")
        db.record_latest_pending(slug="lt-pending", game_title="LT", platform="pc")
        backlog_known = db.get_known_backlog_slugs(source="metacritic", platform="pc")
        latest_known = db.get_known_latest_slugs(source="metacritic", platform="pc")
        assert "old-game" in backlog_known
        assert "old-game" in latest_known
        assert "bg-pending" in backlog_known
        assert "bg-pending" in latest_known  # cross-mode dedup: latest sees backlog slugs too
        assert "lt-pending" in latest_known
        assert "lt-pending" in backlog_known  # cross-mode dedup: backlog sees latest slugs too
        db.close()

    def test_touch_backlog_pending_updates_timestamp(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_backlog_pending(slug="bg", game_title="BG", platform="pc")
        db.touch_backlog_pending("bg")
        rows = db.get_backlog_pending()
        assert len(rows) == 1
        assert rows[0].last_checked_at is not None
        db.close()

    def test_touch_latest_pending_updates_timestamp(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_latest_pending(slug="lt", game_title="LT", platform="pc")
        db.touch_latest_pending("lt")
        rows = db.get_latest_pending()
        assert len(rows) == 1
        assert rows[0].last_checked_at is not None
        db.close()

    def test_touch_backlog_pending_nonexistent(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.touch_backlog_pending("does-not-exist")  # should not raise
        db.close()

    def test_touch_latest_pending_nonexistent(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.touch_latest_pending("does-not-exist")  # should not raise
        db.close()

    def test_update_backlog_pending_scores_nonexistent(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.update_backlog_pending_scores(slug="nonexistent", metascore=85.0)  # should not raise
        assert not db.is_backlog_pending("nonexistent")
        db.close()

    def test_update_latest_pending_scores_nonexistent(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.update_latest_pending_scores(slug="nonexistent", metascore=85.0)
        assert not db.is_latest_pending("nonexistent")
        db.close()

    def test_is_backlog_pending_returns_true(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_backlog_pending(slug="bg", game_title="BG", platform="pc")
        assert db.is_backlog_pending("bg") is True
        assert db.is_backlog_pending("unknown") is False
        db.close()

    def test_is_latest_pending_returns_true(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_latest_pending(slug="lt", game_title="LT", platform="pc")
        assert db.is_latest_pending("lt") is True
        assert db.is_latest_pending("unknown") is False
        db.close()

    def test_update_backlog_pending_expiry(self, tmp_path: Path) -> None:
        import datetime

        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_backlog_pending(slug="bg", game_title="BG", platform="pc")
        db.update_backlog_pending_expiry("bg", 60)
        rows = db.get_backlog_pending()
        assert len(rows) == 1
        new_expiry = datetime.datetime.fromisoformat(rows[0].expires_at)
        expected_min = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=59)
        expected_max = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=61)
        assert expected_min < new_expiry < expected_max
        db.close()

    def test_update_latest_pending_expiry(self, tmp_path: Path) -> None:
        import datetime

        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_latest_pending(slug="lt", game_title="LT", platform="pc")
        db.update_latest_pending_expiry("lt", 30)
        rows = db.get_latest_pending()
        assert len(rows) == 1
        new_expiry = datetime.datetime.fromisoformat(rows[0].expires_at)
        expected_min = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=29)
        expected_max = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=31)
        assert expected_min < new_expiry < expected_max
        db.close()

    def test_update_backlog_pending_expiry_nonexistent(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.update_backlog_pending_expiry("does-not-exist", 60)  # should not raise
        db.close()

    def test_update_latest_pending_expiry_nonexistent(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.update_latest_pending_expiry("does-not-exist", 60)  # should not raise
        db.close()

    def test_get_expired_backlog_pending(self, tmp_path: Path) -> None:
        import datetime

        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=1)).isoformat()
        db.record_backlog_pending(slug="old", game_title="Old", platform="pc", expires_at=past)
        expired = db.get_expired_backlog_pending()
        assert len(expired) == 1
        assert expired[0].slug == "old"
        db.close()

    def test_get_expired_latest_pending(self, tmp_path: Path) -> None:
        import datetime

        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=1)).isoformat()
        db.record_latest_pending(slug="old", game_title="Old", platform="pc", expires_at=past)
        expired = db.get_expired_latest_pending()
        assert len(expired) == 1
        assert expired[0].slug == "old"
        db.close()

    def test_record_backlog_pending_duplicate_slug(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_backlog_pending(slug="bg", game_title="Original", platform="pc")
        db.record_backlog_pending(slug="bg", game_title="Duplicate", platform="pc")
        rows = db.get_backlog_pending()
        assert len(rows) == 1
        assert rows[0].game_title == "Original"
        db.close()

    def test_record_latest_pending_duplicate_slug(self, tmp_path: Path) -> None:
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_latest_pending(slug="lt", game_title="Original", platform="pc")
        db.record_latest_pending(slug="lt", game_title="Duplicate", platform="pc")
        rows = db.get_latest_pending()
        assert len(rows) == 1
        assert rows[0].game_title == "Original"
        db.close()

    def test_update_pending_scores_all_params(self, tmp_path: Path) -> None:
        """Legacy update_pending_scores covers all branches."""
        from gamarr.database import Database, PendingGame, PendingGameBacklog

        db = Database(tmp_path / "test.db")
        db.record_backlog_pending(slug="full", game_title="Full", platform="pc")
        with db._session() as session:
            row = session.get(PendingGameBacklog, "full")
            assert row is not None
            # Simulate the legacy table access by writing to the old table
            from gamarr.database import PendingGame

            session.add(
                PendingGame(
                    slug="full",
                    game_title="Full",
                    platform="pc",
                    discovered_at=row.discovered_at,
                    expires_at=row.expires_at,
                )
            )
            session.commit()
        db.update_pending_scores(slug="full", metascore=88.0, metascore_reviews=12, user_score=8.5, user_reviews=30)
        with db._session() as session:
            from gamarr.database import PendingGame

            pg_row = session.get(PendingGame, "full")
            assert pg_row is not None
            assert pg_row.metascore == 88.0
            assert pg_row.metascore_reviews == 12
            assert pg_row.user_score == 8.5
            assert pg_row.user_reviews == 30
            assert pg_row.score_checks_passed is True
            assert pg_row.last_checked_at is not None
        db.close()

    def test_update_pending_scores_partial(self, tmp_path: Path) -> None:
        """Legacy update_pending_scores with only metascore updates partial fields."""
        from gamarr.database import Database, PendingGame, PendingGameBacklog

        db = Database(tmp_path / "test.db")
        db.record_backlog_pending(slug="partial", game_title="Partial", platform="pc")
        with db._session() as session:
            bg_row = session.get(PendingGameBacklog, "partial")
            assert bg_row is not None
            session.add(
                PendingGame(
                    slug="partial",
                    game_title="Partial",
                    platform="pc",
                    discovered_at=bg_row.discovered_at,
                    expires_at=bg_row.expires_at,
                )
            )
            session.commit()
        db.update_pending_scores(slug="partial", metascore=75.0)
        with db._session() as session:
            pg_row = session.get(PendingGame, "partial")
            assert pg_row is not None
            assert pg_row.metascore == 75.0
            assert pg_row.score_checks_passed is True
        db.close()

    def test_update_latest_pending_scores_full(self, tmp_path: Path) -> None:
        """update_latest_pending_scores handles full score update."""
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_latest_pending(slug="lt-full", game_title="LT Full", platform="pc")
        db.update_latest_pending_scores(
            slug="lt-full", metascore=92.0, metascore_reviews=15, user_score=8.8, user_reviews=25
        )
        rows = db.get_latest_pending()
        assert rows[0].metascore == 92.0
        assert rows[0].metascore_reviews == 15
        assert rows[0].user_score == 8.8
        assert rows[0].user_reviews == 25
        assert rows[0].score_checks_passed is True
        db.close()

    def test_update_latest_pending_scores_partial(self, tmp_path: Path) -> None:
        """update_latest_pending_scores with only user_score updates partial."""
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_latest_pending(slug="lt-partial", game_title="LT Partial", platform="pc")
        db.update_latest_pending_scores(slug="lt-partial", user_score=9.0)
        rows = db.get_latest_pending()
        assert rows[0].user_score == 9.0
        assert rows[0].metascore is None
        assert rows[0].score_checks_passed is True
        db.close()

    def test_update_latest_pending_scores_empty(self, tmp_path: Path) -> None:
        """update_latest_pending_scores with no scores does not flag as checked."""
        from gamarr.database import Database

        db = Database(tmp_path / "test.db")
        db.record_latest_pending(slug="lt-empty", game_title="LT Empty", platform="pc")
        db.update_latest_pending_scores(slug="lt-empty")
        rows = db.get_latest_pending()
        assert rows[0].score_checks_passed is None
        db.close()

    def test_migrate_pending_mode_split_with_data(self, tmp_path: Path) -> None:
        """Migration copies legacy pending_games rows to backlog table."""
        import datetime
        import os
        import sqlite3

        db_path = os.path.join(str(tmp_path), "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pending_games (slug TEXT PRIMARY KEY, game_title TEXT NOT NULL, platform TEXT NOT NULL, metascore REAL, metascore_reviews INTEGER, user_score REAL, user_reviews INTEGER, genres TEXT, release_date TEXT, discovered_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_checked_at TEXT, score_checks_passed INTEGER)"
        )
        future = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=365)).isoformat()
        now = datetime.datetime.now(datetime.UTC).isoformat()
        conn.execute(
            "INSERT INTO pending_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("migrate-test", "Migrate Game", "pc", 85.0, 20, 8.5, 50, None, "2024-01-01", now, future, None, 1),
        )
        conn.commit()
        conn.close()
        from gamarr.database import Database

        db = Database(db_path)
        backlog = db.get_backlog_pending(platform="pc")
        assert len(backlog) >= 1, f"Expected >=1 backlog row, got {len(backlog)}"
        assert any(r.slug == "migrate-test" for r in backlog)
        db.close()
        # Verify legacy table is now empty
        conn2 = sqlite3.connect(db_path)
        legacy = conn2.execute("SELECT COUNT(*) FROM pending_games").fetchone()[0]
        assert legacy == 0, f"Legacy table should be empty, got {legacy}"
        conn2.close()
