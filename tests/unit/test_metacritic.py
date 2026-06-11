"""Tests for gamarr Metacritic integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from gamarr.database import Database
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

    def test_cache_accepts_database_instance(self) -> None:
        """MetacriticCache must accept a Database instance (not a cache_path string).

        This proves the cache merge is complete: the cache uses the shared
        gamarr.db instead of creating a separate gamarr-cache.db.
        """
        db = Database(":memory:")
        cache = MetacriticCache(db)
        cache.set_game_detail("test-game", 85.0, 10, 7.5, 100)
        result = cache.get_game_detail("test-game", ttl_days=7)
        assert result is not None
        assert result["metascore"] == 85.0
        assert result["user_score"] == 7.5
        cache.close()
        db.close()

    def test_cache_miss(self, tmp_path: Path) -> None:
        cache = MetacriticCache(Database(str(tmp_path / "test.db")))
        result = cache.get_game_detail("elden-ring")
        assert result is None
        cache.close()

    def test_cache_set_and_get(self, tmp_path: Path) -> None:
        cache = MetacriticCache(Database(str(tmp_path / "test.db")))
        cache.set_game_detail("elden-ring", 96.0, 120, 8.5, 5000)
        result = cache.get_game_detail("elden-ring")
        assert result is not None
        assert result["user_score"] == 8.5
        cache.close()

    def test_cache_expiry(self, tmp_path: Path) -> None:
        import datetime

        cache = MetacriticCache(Database(str(tmp_path / "test.db")))
        cache.set_game_detail("old-game", 50.0, 10, 5.0, 100)
        cache._set_cached_at("old-game", (datetime.datetime.now() - datetime.timedelta(days=999)).isoformat())
        result = cache.get_game_detail("old-game", ttl_days=7)
        assert result is None
        cache.close()

    def test_browse_cache(self, tmp_path: Path) -> None:
        cache = MetacriticCache(Database(str(tmp_path / "test.db")))
        games = [{"title": "Test Game", "slug": "test-game"}]
        cache.set_browse_page("pc", 1, games)
        result = cache.get_browse_page("pc", 1, ttl_hours=4)
        assert result is not None
        assert result[0]["title"] == "Test Game"
        cache.close()

    def test_browse_cache_expired(self, tmp_path: Path) -> None:
        import datetime

        cache = MetacriticCache(Database(str(tmp_path / "test.db")))
        games = [{"title": "Old Game", "slug": "old-game"}]
        cache.set_browse_page("pc", 1, games)
        # Set cached_at to be old by using SQL directly
        old_time = (datetime.datetime.now() - datetime.timedelta(hours=999)).isoformat()
        from gamarr.database import BrowsePageCache

        with cache._db._session() as session:
            row = session.get(BrowsePageCache, ("pc", 1))
            if row is not None:
                row.cached_at = old_time
                session.commit()
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

    def test_browse_game_list_includes_release_date(self) -> None:
        """_resolve_browse_game_list should include release_date from Nuxt data."""
        from gamarr.metacritic import _resolve_browse_game_list

        nuxt_data = [
            {"items": 1},
            [2],
            {
                "title": "Test Game",
                "slug": "test-game",
                "releaseDate": "2025-06-01",
                "criticScoreSummary": {"score": 85, "reviewCount": 50},
                "userScore": {"score": 8.0, "reviewCount": 200},
            },
        ]
        result = _resolve_browse_game_list(nuxt_data, [2])
        assert len(result) == 1
        assert result[0]["release_date"] == "2025-06-01"

    def test_browse_game_list_missing_release_date(self) -> None:
        """When Nuxt data has no releaseDate, release_date should be None."""
        from gamarr.metacritic import _resolve_browse_game_list

        nuxt_data = [
            {"items": 1},
            [2],
            {
                "title": "Test Game",
                "slug": "test-game",
                "criticScoreSummary": {"score": 85, "reviewCount": 50},
                "userScore": {"score": 8.0, "reviewCount": 200},
            },
        ]
        result = _resolve_browse_game_list(nuxt_data, [2])
        assert len(result) == 1
        assert result[0].get("release_date") is None

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
        from gamarr.utils import normalise_for_compare

        assert normalise_for_compare("Elden Ring") == "eldenring"

    def test_normalise_punctuation(self) -> None:
        from gamarr.utils import normalise_for_compare

        assert normalise_for_compare("Baldur's Gate 3!") == "baldursgate3"

    def test_normalise_whitespace(self) -> None:
        from gamarr.utils import normalise_for_compare

        assert normalise_for_compare("  Game   Name  ") == "gamename"

    def test_normalise_en_dash(self) -> None:
        """Strip en-dash (U+2013) like other punctuation."""
        from gamarr.utils import normalise_for_compare

        result = normalise_for_compare("Magin: The Rat Project Stories \u2013 Essence Edition")
        assert result == "magintheratprojectstoriesessenceedition"


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
        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        client._cache.set_game_detail("elden-ring", 96.0, 120, 8.5, 5000)
        result = client._try_direct_slug("elden-ring", cache_details_days=7)
        assert result is not None
        assert result.metascore == 96.0
        assert result.user_score == 8.5

    def test_try_direct_slug_cache_miss_no_http(self) -> None:
        """Without cache or network, slug lookup returns None."""
        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        result = client._try_direct_slug("nonexistent-game-12345", cache_details_days=7)
        assert result is None

    def test_try_direct_slug_http_failure(self) -> None:
        """When requests.get raises, returns None."""
        from unittest.mock import patch

        import requests

        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        with patch("gamarr.metacritic.requests.get", side_effect=requests.exceptions.ConnectionError("mock error")):
            result = client._try_direct_slug("some-game", cache_details_days=7)
        assert result is None

    def test_try_direct_slug_http_not_found(self) -> None:
        """When requests.get returns 404, returns None."""
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        with patch("gamarr.metacritic.requests.get", return_value=mock_resp):
            result = client._try_direct_slug("nonexistent-game", cache_details_days=7)
        assert result is None


class TestMetacriticClient:
    """Metacritic client construction and mocked HTTP lookups."""

    def test_client_defaults(self) -> None:
        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        assert client.user_agent is not None

    def test_score_for_title_returns_none_on_fetch_error(self) -> None:
        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        result = client.lookup_game("ThisGameDoesNotExistXYZ123")
        assert result is None

    def test_scrape_browse_pages_empty(self) -> None:
        """With no network, browse page scan returns None."""
        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
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

        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        with patch("gamarr.metacritic.requests.get", return_value=mock_resp):
            result = client.lookup_game("Elden Ring")
        assert result is not None
        assert result.metascore == 90.0
        assert result.user_score == 8.5
        assert result.slug == "elden-ring"

    def test_scan_browse_pages_with_cache_hit(self) -> None:
        """When browse cache has a matching game, scan returns a ScoreResult."""
        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
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

        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
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
    """_find_game_details_in_nuxt_data edge cases."""

    def test_non_dict_items_skipped(self) -> None:
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        result = _find_game_details_in_nuxt_data([None, "string", 42])
        assert result is None


class TestFindGameDetailsInNuxtData:
    """Extraction of game metadata from Nuxt data."""

    def test_extracts_genres(self) -> None:
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        data = [
            {"score": 1, "reviewCount": 2, "userScore": {"score": 3, "reviewCount": 4}},
            {"mustPlay": False, "genres": [{"name": "Action"}], "releaseDate": "2025-01-01"},
            "x" * 2000,
        ]
        result = _find_game_details_in_nuxt_data(data)
        assert result is not None
        assert result["genres"] == ["Action"]
        assert result["must_play"] is False

    def test_extracts_release_date(self) -> None:
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        data = [
            {"score": 1, "reviewCount": 2, "userScore": {"score": 3, "reviewCount": 4}},
            {"mustPlay": True, "genres": [{"name": "RPG"}], "releaseDate": "2024-06-15"},
            "x" * 2000,
        ]
        result = _find_game_details_in_nuxt_data(data)
        assert result is not None
        assert result["release_date"] == "2024-06-15"
        assert result["must_play"] is True

    def test_backward_compat_no_metadata(self) -> None:
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        data = [
            {"score": 85, "reviewCount": 50, "userScore": {"score": 8.0, "reviewCount": 200}},
            "x" * 2000,
        ]
        result = _find_game_details_in_nuxt_data(data)
        assert result is not None
        assert result["metascore"] == 85.0
        assert result["user_score"] == 8.0
        assert result["genres"] is None

    def test_non_dict_items_skipped(self) -> None:
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        result = _find_game_details_in_nuxt_data([None, "string", 42])
        assert result is None

    def test_scan_user_review_count_from_summary(self) -> None:
        """Coverage: scanning path picks user review count from summary."""
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        # Nuxt data with inline userScore dict (no reviewCount) and
        # a separate user review summary. The scanning path must
        # call _extract_user_review_count_from_summary.
        data = [
            {"score": 1, "reviewCount": 2, "userScore": {"score": 8.0}},
            96,
            50,
            {"score": 4, "reviewCount": 5, "url": "/game/x/user-reviews/"},
            8.0,
            300,
            "x" * 2000,
        ]
        result = _find_game_details_in_nuxt_data(data)
        assert result is not None
        assert result["user_score"] == 8.0
        # userScore dict has no reviewCount, so the summary must provide it
        assert result["user_reviews"] == 300, f"Got {result['user_reviews']}"

    def test_find_game_details_filters_by_slug(self) -> None:
        """Reproduce cross-game score pollution.

        Modern Metacritic pages embed Nuxt data for many games
        (target + "similar games"). The parser must pick scores
        from the game matching the slug, not the first game found.
        """
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        page_data = [
            # --- OTHER GAME (decoy — appears first, high scores) ---
            {"criticScoreSummary": 1, "userScore": 4, "slug": "other-game"},
            {"score": 2, "reviewCount": 3},  # criticScoreSummary
            96,  # score value
            50,  # reviewCount value
            {"score": 5},  # userScore sub-dict
            8.5,  # user score
            {"score": 5, "reviewCount": 6, "url": "/game/other-game/user-reviews/"},  # user review summary
            200,  # user review count
            # --- TARGET GAME (second, correct scores) ---
            {"criticScoreSummary": 9, "userScore": 12, "slug": "target-game"},
            {"score": 10, "reviewCount": 11},  # criticScoreSummary
            65,  # score value
            5,  # reviewCount value
            {"score": 13},  # userScore sub-dict
            7.2,  # user score
            {"score": 13, "reviewCount": 15, "url": "/game/target-game/user-reviews/"},  # user review summary
            29,  # user review count
            "x" * 2000,
        ]

        result = _find_game_details_in_nuxt_data(page_data, slug="target-game")
        assert result is not None
        assert result["metascore"] == 65.0, f"Got {result['metascore']}"
        assert result["metascore_reviews"] == 5
        assert result["user_score"] == 7.2, f"Got {result['user_score']}"
        assert result["user_reviews"] == 29, f"Got {result['user_reviews']}"


class TestScanBrowsePagesEdgeCase:
    """_scan_browse_pages with mocked HTTP."""

    def test_scan_browse_pages_http_success_no_match(self) -> None:
        """When browse page HTTP succeeds but no matching game found, returns None."""
        import json
        from unittest.mock import MagicMock, patch

        # Build a browse page with one non-matching game
        key = "browse-game-abc"
        game = {"title": "Wrong Game", "slug": "wrong-game-2024"}
        page_data = [{key: 1}, {"items": 2}, [3], game, "x" * 2000]

        html = "<html><body><script>" + json.dumps(page_data) + "</script></body></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = html.encode()

        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        with patch("gamarr.metacritic.requests.get", return_value=mock_resp):
            result = client._scan_browse_pages("Target Game", "pc", 4, 7)
        assert result is None


class TestScanBrowseCacheHit:
    """Browse cache hit with matching game."""

    def test_scan_browse_cache_hit_matching_game(self) -> None:
        """When browse cache has a matching game, it returns the score."""
        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        client._cache.set_browse_page(
            "pc",
            1,
            [
                {"title": "Elden Ring", "slug": "elden-ring-2022"},
            ],
        )
        client._cache.set_game_detail("elden-ring-2022", 96.0, 100, 8.5, 5000)

        result = client._scan_browse_pages("Elden Ring!", "pc", 4, 7)
        assert result is not None
        assert result.metascore == 96.0
        assert result.slug == "elden-ring-2022"

    def test_extracts_description(self) -> None:
        """Description should be extracted from Nuxt data."""
        from gamarr.metacritic import _find_game_details_in_nuxt_data

        data = [
            {"score": 1, "reviewCount": 2, "userScore": {"score": 3, "reviewCount": 4}},
            {
                "mustPlay": False,
                "genres": [{"name": "Action"}],
                "releaseDate": "2025-01-01",
                "description": "A great game about action",
            },
            "x" * 2000,
        ]
        result = _find_game_details_in_nuxt_data(data)
        assert result is not None
        assert "great game" in result["description"]


class TestDateParsing:
    """_is_before_date and _parse_date_flexible helpers."""

    def test_parse_date_flexible_iso(self) -> None:
        from gamarr.metacritic import _parse_date_flexible

        d = _parse_date_flexible("2025-01-01")
        assert d is not None
        assert d.year == 2025 and d.month == 1 and d.day == 1

    def test_parse_date_flexible_uk_format(self) -> None:
        from gamarr.metacritic import _parse_date_flexible

        d = _parse_date_flexible("01-01-2025")
        assert d is not None
        assert d.year == 2025 and d.month == 1 and d.day == 1

    def test_parse_date_flexible_invalid(self) -> None:
        from gamarr.metacritic import _parse_date_flexible

        assert _parse_date_flexible("not-a-date") is None
        assert _parse_date_flexible("") is None

    def test_is_before_date_iso_cutoff(self) -> None:
        from gamarr.metacritic import _is_before_date

        assert _is_before_date("2024-12-31", "2025-01-01") is True
        assert _is_before_date("2025-01-01", "2025-01-01") is False
        assert _is_before_date("2025-01-02", "2025-01-01") is False

    def test_is_before_date_uk_format_cutoff(self) -> None:
        from gamarr.metacritic import _is_before_date

        assert _is_before_date("31-12-2024", "01-01-2025") is True
        assert _is_before_date("2024-12-31", "01-01-2025") is True
        assert _is_before_date("01-01-2025", "2024-12-31") is False

    def test_is_before_date_edge_cases(self) -> None:
        from gamarr.metacritic import _is_before_date

        assert _is_before_date(None, "2025-01-01") is False
        assert _is_before_date("2025-01-01", None) is False
        assert _is_before_date("bad-date", "2025-01-01") is False


class TestScanRecentGames:
    """Metacritic browse-page scanning for discovery."""

    def test_scan_recent_games_returns_list(self) -> None:
        """With no network, scan returns empty list (not crash)."""
        from unittest.mock import patch

        from gamarr.metacritic import MetacriticClient

        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        with patch.object(client, "_fetch_browse_page", return_value=None):
            result = client.scan_recent_games("pc", max_games=1)
        assert result == []

    def test_scan_recent_games_cutoff_date_skips_old(self) -> None:
        """With cutoff_date set, pages whose games are all older should be skipped."""
        from unittest.mock import patch

        from gamarr.metacritic import MetacriticClient

        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        # Return games with release dates all before cutoff
        mock_page = [
            {"title": "Old Game 1", "slug": "old-1", "release_date": "2024-06-01"},
            {"title": "Old Game 2", "slug": "old-2", "release_date": "2024-05-01"},
        ]
        with patch.object(client, "_fetch_browse_page", return_value=mock_page):
            result = client.scan_recent_games("pc", max_games=100, cutoff_date="2025-01-01")
        assert result == [], "All games before cutoff should be excluded"

    def test_scan_recent_games_cutoff_date_includes_new(self) -> None:
        """With cutoff_date set, pages with at least one new game should be included."""
        from unittest.mock import patch

        from gamarr.metacritic import MetacriticClient

        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        # First page has a mix of new and old
        page1 = [
            {"title": "New Game", "slug": "new-1", "release_date": "2025-06-01"},
            {"title": "Old Game", "slug": "old-1", "release_date": "2024-06-01"},
        ]
        # Second page has only old games → should stop
        page2 = [
            {"title": "Old Game 2", "slug": "old-2", "release_date": "2024-05-01"},
        ]
        with patch.object(client, "_fetch_browse_page", side_effect=[page1, page2]):
            result = client.scan_recent_games("pc", max_games=100, cutoff_date="2025-01-01")
        assert len(result) == 2, "Only page1 games should be included"
        assert result[0]["slug"] == "new-1"

    def test_scan_recent_games_cutoff_date_invalid_logs_warning(self) -> None:
        """An invalid cutoff_date should log a warning and fall back to no cutoff."""
        from unittest.mock import patch

        from gamarr.metacritic import MetacriticClient

        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        mock_page = [
            {"title": "Game", "slug": "game-1", "release_date": "2026-06-01"},
        ]
        with patch.object(client, "_fetch_browse_page", return_value=mock_page):
            # Should not crash — invalid date fallback to no cutoff
            # The cutoff is disabled, so it collects up to max_games.
            # Each fetch returns 1 game, so 10 fetches = 10 games for max_games=10.
            result = client.scan_recent_games("pc", max_games=10, cutoff_date="bad-date")
        assert len(result) == 10
        # All games should be included despite the bad cutoff
        assert result[0]["slug"] == "game-1"

    def test_scan_recent_games_respects_max_games_limit(self) -> None:
        """scan_recent_games should collect at most max_games games."""
        from unittest.mock import patch

        from gamarr.metacritic import MetacriticClient

        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))
        page1 = [{"title": f"Game {i}", "slug": f"game-{i}"} for i in range(50)]
        page2 = [{"title": f"Game {i + 50}", "slug": f"game-{i + 50}"} for i in range(50)]
        with patch.object(client, "_fetch_browse_page", side_effect=[page1, page2]):
            result = client.scan_recent_games("pc", max_games=75)
        assert len(result) == 75, "Should collect exactly max_games games"
        assert result[0]["slug"] == "game-0"
        assert result[74]["slug"] == "game-74"


class TestCheckUserReviewItem:
    """_check_user_review_item must require /user-reviews/ in URL."""

    def test_returns_none_for_critic_review_urls(self) -> None:
        """Critic review summary items (with /critic-reviews/ URL) must not match."""
        from gamarr.metacritic import _check_user_review_item

        page_data: list[dict[str, object]] = []
        item = {
            "score": 78,
            "reviewCount": 24,
            "url": "/game/people-of-note/critic-reviews/?platform=pc",
        }
        result = _check_user_review_item(page_data, item, "people-of-note")
        assert result is None, f"Critic review item should not match: {result}"

    def test_returns_scores_for_user_review_urls(self) -> None:
        """User review summary items (with /user-reviews/ URL) must match."""
        from gamarr.metacritic import _check_user_review_item

        page_data: list[dict[str, object]] = []
        item = {
            "score": 6.8,
            "reviewCount": 35,
            "url": "/game/people-of-note/user-reviews/?platform=pc",
        }
        result = _check_user_review_item(page_data, item, "people-of-note")
        assert result is not None
        assert result[0] == 35, f"Expected 35 reviews, got {result[0]}"
        assert result[1] == 6.8, f"Expected user score 6.8, got {result[1]}"

    def test_returns_none_for_different_slug(self) -> None:
        """Items with a different slug must not match."""
        from gamarr.metacritic import _check_user_review_item

        page_data: list[dict[str, object]] = []
        item = {
            "score": 7.5,
            "reviewCount": 10,
            "url": "/game/other-game/user-reviews/?platform=pc",
        }
        result = _check_user_review_item(page_data, item, "people-of-note")
        assert result is None, f"Different slug should not match: {result}"


class TestScanRecentGamesCancellation:
    """Cancel event support in scan_recent_games."""

    def test_scan_recent_games_returns_early_on_cancel(self) -> None:
        """When cancel_event is set mid-scan, scan_recent_games returns
        with partial results instead of completing all pages."""
        import threading
        from unittest.mock import patch

        from gamarr.metacritic import MetacriticClient

        client = MetacriticClient(cache=MetacriticCache(Database(":memory:")))

        cancel_event = threading.Event()
        call_count = 0

        def mock_fetch(
            _platform: str,
            _page_number: int,
            _cache_ttl_hours: int,
        ) -> list[dict] | None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                cancel_event.set()
            return [{"title": f"Game {i}", "slug": f"game-{i}", "score": 85, "user_rating": 8.0} for i in range(20)]

        with patch.object(client, "_fetch_browse_page", side_effect=mock_fetch):
            result = client.scan_recent_games(
                "pc",
                max_games=0,
                cancel_event=cancel_event,
            )

        # After cancel was set (call_count >= 2), scan_recent_games should
        # have stopped. We expect at most 3 pages: page 1 (no cancel),
        # page 2 (cancel set inside mock_fetch), then cancel check stops it.
        assert call_count <= 3, f"Expected early stop but fetched {call_count} pages"
        assert len(result) <= 60, f"Expected <=60 games but got {len(result)}"  # 3 pages * 20 games
