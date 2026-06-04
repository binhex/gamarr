"""Tests for gamarr Metacritic integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from gamarr.metacritic import MetacriticClient, ScoreResult
from gamarr.metacritic_cache import MetacriticCache


class TestScoreResult:
    """ScoreResult dataclass construction."""

    def test_passing_score(self) -> None:
        sr = ScoreResult(
            title="Elden Ring",
            slug="elden-ring",
            metascore=96.0,
            metascore_review_count=120,
            user_score=8.5,
            user_review_count=5000,
            passed=True,
        )
        assert sr.passed is True
        assert sr.metascore == 96.0

    def test_failing_score(self) -> None:
        sr = ScoreResult(
            title="Bad Game",
            slug="bad-game",
            metascore=30.0,
            metascore_review_count=5,
            user_score=2.0,
            user_review_count=10,
            passed=False,
        )
        assert sr.passed is False


class TestSlugGeneration:
    """Metacritic slug generation from game titles."""

    def test_simple_slug(self) -> None:
        from gamarr.metacritic import _make_slug

        assert _make_slug("Elden Ring") == "elden-ring"

    def test_slug_with_apostrophe(self) -> None:
        from gamarr.metacritic import _make_slug

        assert _make_slug("Baldur's Gate 3") == "baldurs-gate-3"

    def test_slug_with_colon(self) -> None:
        from gamarr.metacritic import _make_slug

        assert _make_slug("Hades II") == "hades-ii"

    def test_slug_with_ampersand(self) -> None:
        from gamarr.metacritic import _make_slug

        assert _make_slug("Crash & Spyro") == "crash-and-spyro"

    def test_slug_with_special_chars(self) -> None:
        from gamarr.metacritic import _make_slug

        assert _make_slug("Game: The Reckoning!") == "game-the-reckoning"


class TestMetacriticCache:
    """Metacritic cache operations."""

    def test_cache_miss(self, tmp_path: Path) -> None:
        cache = MetacriticCache(str(tmp_path / "cache.db"))
        result = cache.get_game_detail("elden-ring")
        assert result is None
        cache.close()

    def test_cache_set_and_get(self, tmp_path: Path) -> None:
        cache = MetacriticCache(str(tmp_path / "cache.db"))
        cache.set_game_detail("elden-ring", 96.0, 120, 8.5, 5000)
        result = cache.get_game_detail("elden-ring")
        assert result is not None
        assert result["user_score"] == 8.5
        cache.close()

    def test_cache_expiry(self, tmp_path: Path) -> None:
        import datetime

        cache = MetacriticCache(str(tmp_path / "cache.db"))
        cache.set_game_detail("old-game", 50.0, 10, 5.0, 100)
        cache._set_cached_at("old-game", (datetime.datetime.now() - datetime.timedelta(days=999)).isoformat())
        result = cache.get_game_detail("old-game", ttl_days=7)
        assert result is None
        cache.close()

    def test_browse_cache(self, tmp_path: Path) -> None:
        cache = MetacriticCache(str(tmp_path / "cache.db"))
        games = [{"title": "Test Game", "slug": "test-game"}]
        cache.set_browse_page("pc", 1, games)
        result = cache.get_browse_page("pc", 1, ttl_hours=4)
        assert result is not None
        assert result[0]["title"] == "Test Game"
        cache.close()

    def test_browse_cache_expired(self, tmp_path: Path) -> None:
        import datetime

        cache = MetacriticCache(str(tmp_path / "cache.db"))
        games = [{"title": "Old Game", "slug": "old-game"}]
        cache.set_browse_page("pc", 1, games)
        # Set cached_at to be old by using SQL directly
        old_time = (datetime.datetime.now() - datetime.timedelta(hours=999)).isoformat()
        cache._conn.execute(
            "UPDATE browse_page_cache SET cached_at = ? WHERE platform = ? AND page_number = ?",
            (old_time, "pc", 1),
        )
        cache._conn.commit()
        result = cache.get_browse_page("pc", 1, ttl_hours=4)
        assert result is None
        cache.close()


class TestMetacriticClient:
    """Metacritic client construction."""

    def test_client_defaults(self) -> None:
        client = MetacriticClient(cache_path=":memory:")
        assert client.user_agent is not None

    def test_score_for_title_returns_none_on_fetch_error(self) -> None:
        client = MetacriticClient(cache_path=":memory:")
        result = client.lookup_game("ThisGameDoesNotExistXYZ123")
        assert result is None
