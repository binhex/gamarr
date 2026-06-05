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


class TestParseGameDetails:
    """Metacritic game page Nuxt JSON parsing."""

    def test_parse_game_details_with_valid_nuxt_data(self) -> None:
        from gamarr.metacritic import _parse_game_details

        # The parser skips scripts shorter than 1000 chars, so we pad
        page_data = [
            {"score": 85, "reviewCount": 50, "userScore": {"score": 8.0, "reviewCount": 200}},
            "x" * 2000,
        ]
        import json

        html = f"<html><body><script>{json.dumps(page_data)}</script></body></html>"

        result = _parse_game_details(html.encode())
        assert result is not None
        assert result["metascore"] == 85.0
        assert result["metascore_reviews"] == 50
        assert result["user_score"] == 8.0
        assert result["user_reviews"] == 200

    def test_parse_game_details_with_no_scripts(self) -> None:
        from gamarr.metacritic import _parse_game_details

        html = b"<html><body>No scripts here</body></html>"
        result = _parse_game_details(html)
        assert result is None

    def test_parse_game_details_with_empty_script(self) -> None:
        from gamarr.metacritic import _parse_game_details

        html = b"<html><body><script></script></body></html>"
        result = _parse_game_details(html)
        assert result is None

    def test_parse_game_details_with_bad_json(self) -> None:
        from gamarr.metacritic import _parse_game_details

        html = b"<html><body><script>not-json</script></body></html>"
        result = _parse_game_details(html)
        assert result is None

    def test_parse_game_details_nuxt_indices(self) -> None:
        from gamarr.metacritic import _parse_game_details

        # Nuxt pattern where score/reviewCount are indices into the root array.
        # index 1 = 85 (score value), index 2 = 50 (review count value)
        # index 3 = {"score": 8.0, "reviewCount": 200} (userScore block)
        # Need padding for the 1000-char script length threshold.
        page_data = [
            {"score": 1, "reviewCount": 2, "userScore": 3},
            85,
            50,
            {"score": 8.0, "reviewCount": 200},
            "x" * 2000,
        ]
        import json

        html = f"<html><body><script>{json.dumps(page_data)}</script></body></html>"
        result = _parse_game_details(html.encode())
        assert result is not None
        assert result["metascore"] == 85.0
        assert result["metascore_reviews"] == 50
        assert result["user_score"] == 8.0
        assert result["user_reviews"] == 200


class TestParseBrowsePage:
    """Metacritic browse page Nuxt JSON parsing."""

    def test_parse_browse_page_with_games(self) -> None:
        from gamarr.metacritic import _parse_browse_page

        # Build a browse page with Nuxt state containing browse-game items.
        # The Nuxt pattern: "browse-game-xxx": <root_index> appears inside
        # the JSON. The root object has "items": <int_index> pointing to
        # an array of game item indices. Games are at those indices.
        # Padding is needed for the 1000-char script length threshold.
        key = "browse-game-abc"
        game = {
            "title": "Test Game",
            "slug": "test-game-2024",
            "criticScoreSummary": {"score": 85, "reviewCount": 50},
            "userScore": {"score": 8.0, "reviewCount": 200},
        }
        # index 0: key lookup -> points to index 1 (root)
        # index 1: root object with "items": 2 (items is an INT index into array)
        # index 2: items array = [3] (game is at index 3)
        # index 3: game data
        # index 4: padding for 1000-char threshold
        page_data = [{key: 1}, {"items": 2}, [3], game, "x" * 2000]
        import json

        html = "<html><body><script>" + json.dumps(page_data) + "</script></body></html>"
        result = _parse_browse_page(html.encode())
        assert result is not None
        assert len(result) == 1
        assert result[0]["title"] == "Test Game"

    def test_parse_browse_page_empty_html(self) -> None:
        from gamarr.metacritic import _parse_browse_page

        result = _parse_browse_page(b"<html></html>")
        assert result is None

    def test_parse_browse_page_no_browse_games(self) -> None:
        from gamarr.metacritic import _parse_browse_page

        html = b'<html><body><script>[{"key": "value"}]</script></body></html>'
        result = _parse_browse_page(html)
        assert result is None


class TestNormaliseForCompare:
    """Title normalisation for fuzzy matching."""

    def test_normalise_simple(self) -> None:
        from gamarr.metacritic import _normalise_for_compare

        assert _normalise_for_compare("Elden Ring") == "elden ring"

    def test_normalise_punctuation(self) -> None:
        from gamarr.metacritic import _normalise_for_compare

        assert _normalise_for_compare("Baldur's Gate 3!") == "baldurs gate 3"

    def test_normalise_whitespace(self) -> None:
        from gamarr.metacritic import _normalise_for_compare

        assert _normalise_for_compare("  Game   Name  ") == "game name"


class TestNuxtVal:
    """Nuxt value resolution helper."""

    def test_nuxt_val_with_index(self) -> None:
        from gamarr.metacritic import _nuxt_val

        data = [None, "value", None]
        assert _nuxt_val(data, 1) == "value"

    def test_nuxt_val_with_literal(self) -> None:
        from gamarr.metacritic import _nuxt_val

        data = [None, "value", None]
        assert _nuxt_val(data, "literal") == "literal"

    def test_nuxt_val_out_of_range(self) -> None:
        from gamarr.metacritic import _nuxt_val

        data = [None]
        assert _nuxt_val(data, 99) == 99


class TestTryDirectSlug:
    """Direct slug lookup with mocked HTTP."""

    def test_try_direct_slug_cache_hit(self) -> None:
        client = MetacriticClient(cache_path=":memory:")
        client._cache.set_game_detail("elden-ring", 96.0, 120, 8.5, 5000)
        result = client._try_direct_slug("elden-ring", cache_ttl_days=7)
        assert result is not None
        assert result.metascore == 96.0
        assert result.user_score == 8.5

    def test_try_direct_slug_cache_miss_no_http(self) -> None:
        """Without cache or network, slug lookup returns None."""
        client = MetacriticClient(cache_path=":memory:")
        result = client._try_direct_slug("nonexistent-game-12345", cache_ttl_days=7)
        assert result is None

    def test_try_direct_slug_http_failure(self) -> None:
        """When requests.get raises, returns None."""
        from unittest.mock import patch

        import requests

        client = MetacriticClient(cache_path=":memory:")
        with patch("gamarr.metacritic.requests.get", side_effect=requests.exceptions.ConnectionError("mock error")):
            result = client._try_direct_slug("some-game", cache_ttl_days=7)
        assert result is None

    def test_try_direct_slug_http_not_found(self) -> None:
        """When requests.get returns 404, returns None."""
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        client = MetacriticClient(cache_path=":memory:")
        with patch("gamarr.metacritic.requests.get", return_value=mock_resp):
            result = client._try_direct_slug("nonexistent-game", cache_ttl_days=7)
        assert result is None


class TestMetacriticClient:
    """Metacritic client construction and mocked HTTP lookups."""

    def test_client_defaults(self) -> None:
        client = MetacriticClient(cache_path=":memory:")
        assert client.user_agent is not None

    def test_score_for_title_returns_none_on_fetch_error(self) -> None:
        client = MetacriticClient(cache_path=":memory:")
        result = client.lookup_game("ThisGameDoesNotExistXYZ123")
        assert result is None

    def test_scrape_browse_pages_empty(self) -> None:
        """With no network, browse page scan returns None."""
        client = MetacriticClient(cache_path=":memory:")
        result = client._scan_browse_pages("Nonexistent Game", "pc", 4, 7)
        assert result is None

    def test_lookup_game_direct_slug_http_success(self) -> None:
        """Mock a successful direct slug HTTP response with Nuxt data."""
        import json
        from unittest.mock import MagicMock, patch

        page_data = [
            {"score": 90, "reviewCount": 80, "userScore": {"score": 8.5, "reviewCount": 300}},
            "x" * 2000,
        ]
        html = f"<html><body><script>{json.dumps(page_data)}</script></body></html>"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = html.encode()

        client = MetacriticClient(cache_path=":memory:")
        with patch("gamarr.metacritic.requests.get", return_value=mock_resp):
            result = client.lookup_game("Elden Ring")
        assert result is not None
        assert result.metascore == 90.0
        assert result.user_score == 8.5
        assert result.slug == "elden-ring"

    def test_scan_browse_pages_with_cache_hit(self) -> None:
        """When browse cache has a matching game, scan returns a ScoreResult."""
        client = MetacriticClient(cache_path=":memory:")
        client._cache.set_browse_page(
            "pc",
            1,
            [
                {"title": "Test Game", "slug": "test-game-2024"},
            ],
        )
        client._cache.set_game_detail("test-game-2024", 85.0, 50, 8.0, 200)

        result = client._scan_browse_pages("Test Game!", "pc", 4, 7)
        assert result is not None
        assert result.metascore == 85.0
        assert result.slug == "test-game-2024"

    def test_scan_browse_pages_http_failure(self) -> None:
        """When browse page HTTP fails, scan returns None."""
        from unittest.mock import patch

        import requests

        client = MetacriticClient(cache_path=":memory:")
        with patch("gamarr.metacritic.requests.get", side_effect=requests.exceptions.ConnectionError("mock")):
            result = client._scan_browse_pages("Some Game", "pc", 4, 7)
        assert result is None


class TestParseGameDetailsEdgeCases:
    """Additional edge cases for game detail parsing."""

    def test_parse_game_details_with_exception(self) -> None:
        from gamarr.metacritic import _parse_game_details

        # Non-bytes input raises TypeError which is caught
        result = _parse_game_details(None)  # type: ignore[arg-type]
        assert result is None

    def test_parse_game_details_json_decode_error(self) -> None:
        from gamarr.metacritic import _parse_game_details

        # Short script with non-JSON content that won't be parsed
        html = b"<html><body><script>" + b"x" * 1001 + b"</script></body></html>"
        result = _parse_game_details(html)
        assert result is None


class TestParseBrowsePageEdgeCases:
    """Additional edge cases for browse page parsing."""

    def test_parse_browse_page_non_html(self) -> None:
        from gamarr.metacritic import _parse_browse_page

        # Non-bytes input
        result = _parse_browse_page(None)  # type: ignore[arg-type]
        assert result is None

    def test_parse_browse_page_empty(self) -> None:
        from gamarr.metacritic import _parse_browse_page

        result = _parse_browse_page(b"")
        assert result is None


class TestFindScoresInNuxtData:
    """_find_scores_in_nuxt_data edge cases."""

    def test_non_dict_items_skipped(self) -> None:
        from gamarr.metacritic import _find_scores_in_nuxt_data

        result = _find_scores_in_nuxt_data([None, "string", 42])
        assert result is None


class TestScanBrowsePagesEdgeCase:
    """_scan_browse_pages with mocked HTTP."""

    def test_scan_browse_pages_http_success_no_match(self) -> None:
        """When browse page HTTP succeeds but no matching game found, returns None."""
        import json
        from unittest.mock import patch, MagicMock

        # Build a browse page with one non-matching game
        key = "browse-game-abc"
        game = {"title": "Wrong Game", "slug": "wrong-game-2024"}
        page_data = [{key: 1}, {"items": 2}, [3], game, "x" * 2000]

        html = '<html><body><script>' + json.dumps(page_data) + '</script></body></html>'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = html.encode()

        client = MetacriticClient(cache_path=":memory:")
        with patch("gamarr.metacritic.requests.get", return_value=mock_resp):
            result = client._scan_browse_pages("Target Game", "pc", 4, 7)
        assert result is None


class TestScanBrowseCacheHit:
    """Browse cache hit with matching game."""

    def test_scan_browse_cache_hit_matching_game(self) -> None:
        """When browse cache has a matching game, it returns the score."""
        client = MetacriticClient(cache_path=":memory:")
        client._cache.set_browse_page("pc", 1, [
            {"title": "Elden Ring", "slug": "elden-ring-2022"},
        ])
        client._cache.set_game_detail("elden-ring-2022", 96.0, 100, 8.5, 5000)

        result = client._scan_browse_pages("Elden Ring!", "pc", 4, 7)
        assert result is not None
        assert result.metascore == 96.0
        assert result.slug == "elden-ring-2022"
