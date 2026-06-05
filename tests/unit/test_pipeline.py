"""Tests for gamarr acquisition pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from gamarr.pipeline import AcquisitionConfig, run_acquisition

if TYPE_CHECKING:
    from pathlib import Path


class TestAcquisitionConfig:
    """AcquisitionConfig construction."""

    def test_defaults(self) -> None:
        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=90,
        )
        assert cfg.min_metascore == 75


class TestRunAcquisition:
    """End-to-end acquisition pipeline."""

    def test_no_entries_returns_empty(self) -> None:
        with patch("gamarr.pipeline.FitGirlSource") as mock_source_cls:
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = []
            mock_source_cls.return_value = mock_source

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )
            assert results == []

    def test_entry_passes_and_adds_to_qbt(self) -> None:
        from gamarr.models import GameEntry

        entry = GameEntry(
            title="Test Game",
            source_title="Test Game [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:abc",
            source_url="http://example.com/test-game",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            import types

            mock_mc_result = types.SimpleNamespace(
                title="Test Game",
                passed=True,
                metascore=85.0,
                user_score=8.0,
                metascore_review_count=50,
                user_review_count=200,
                genres=["Action", "RPG"],
                must_play=True,
                release_date="2026-06-01",
            )

            mock_mc = MagicMock()
            mock_mc.lookup_game.return_value = mock_mc_result
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.add_torrent.return_value = "gamarr-abc123"
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
                min_metascore=75,
                min_metascore_reviews=5,
                min_user_score=7.5,
                min_user_reviews=10,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Passed"

    def test_entry_fails_low_metascore(self) -> None:
        from gamarr.models import GameEntry

        entry = GameEntry(
            title="Bad Game",
            source_title="Bad Game [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:xyz",
            source_url="http://example.com/bad-game",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            import types

            mock_mc_result = types.SimpleNamespace(
                title="Bad Game",
                passed=False,
                metascore=30.0,
                user_score=2.0,
                metascore_review_count=5,
                user_review_count=10,
                genres=["Action"],
                must_play=False,
                release_date="2026-06-01",
            )

            mock_mc = MagicMock()
            mock_mc.lookup_game.return_value = mock_mc_result
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
                min_metascore=75,
                min_metascore_reviews=5,
                min_user_score=7.5,
                min_user_reviews=10,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Failed"

    def test_game_not_found_on_metacritic(self) -> None:
        from gamarr.models import GameEntry

        entry = GameEntry(
            title="Unknown Game",
            source_title="Unknown [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="",
            source_url="http://example.com/unknown",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc.lookup_game.return_value = None
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Failed"

    def test_qbt_not_connected_skips(self) -> None:
        from gamarr.models import GameEntry

        entry = GameEntry(
            title="Game",
            source_title="Game [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:abc",
            source_url="http://example.com/game",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = False
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )
            assert results == []


class TestEvaluateScores:
    """_evaluate_scores function edge cases."""

    def test_both_scores_none_returns_failed(self) -> None:
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )()
        mc_result = types.SimpleNamespace(metascore=None, user_score=None)
        assert _evaluate_scores(mc_result, cfg) == "Failed"

    def test_high_metascore_low_reviews_fails(self) -> None:
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=2,
            user_score=8.0,
            user_review_count=100,
        )
        assert _evaluate_scores(mc_result, cfg) == "Failed"

    def test_good_metascore_low_user_reviews_fails(self) -> None:
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=3,
        )
        assert _evaluate_scores(mc_result, cfg) == "Failed"

    def test_old_game_fails_days_since_release(self) -> None:
        """A game older than days_since_release should fail."""
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 0,
                "min_metascore_reviews": 0,
                "min_user_score": 0.0,
                "min_user_reviews": 0,
                "days_since_release": 30,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.5,
            user_review_count=100,
            release_date="2020-01-01",
        )
        assert _evaluate_scores(mc_result, cfg) == "Failed"

    def test_recent_game_passes_days_since_release(self) -> None:
        """A game within days_since_release should pass (if scores are fine)."""
        import datetime
        import types

        from gamarr.pipeline import _evaluate_scores

        recent = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=5)).strftime("%Y-%m-%d")

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 0,
                "min_metascore_reviews": 0,
                "min_user_score": 0.0,
                "min_user_reviews": 0,
                "days_since_release": 30,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.5,
            user_review_count=100,
            release_date=recent,
        )
        assert _evaluate_scores(mc_result, cfg) == "Passed"

    def test_no_release_date_passes_days_check(self) -> None:
        """When release_date is None, the game should NOT be failed
        (we don't know the release date, so assume it's fine)."""
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 0,
                "min_metascore_reviews": 0,
                "min_user_score": 0.0,
                "min_user_reviews": 0,
                "days_since_release": 30,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.5,
            user_review_count=100,
            release_date=None,
        )
        assert _evaluate_scores(mc_result, cfg) == "Passed"

    def test_malformed_release_date_treated_as_recent(self) -> None:
        """A malformed release date should not cause failure."""
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 0,
                "min_metascore_reviews": 0,
                "min_user_score": 0.0,
                "min_user_reviews": 0,
                "days_since_release": 30,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.5,
            user_review_count=100,
            release_date="not-a-date",
        )
        assert _evaluate_scores(mc_result, cfg) == "Passed"


class TestPipelineEdgeCases:
    """Pipeline edge cases with mocked dependencies."""

    def test_qbt_add_failure(self) -> None:
        import types

        from gamarr.models import GameEntry

        entry = GameEntry(
            title="QBT Fail",
            source_title="QBT Fail [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:fail",
            source_url="http://example.com/qbt-fail",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            mock_mc_result = types.SimpleNamespace(
                title="QBT Fail",
                passed=True,
                metascore=85.0,
                user_score=8.0,
                metascore_review_count=50,
                user_review_count=200,
                genres=["Action"],
                must_play=True,
                release_date="2026-06-01",
            )
            mock_mc = MagicMock()
            mock_mc.lookup_game.return_value = mock_mc_result
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt.add_torrent.return_value = False
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
                min_metascore=75,
                min_metascore_reviews=5,
                min_user_score=7.5,
                min_user_reviews=10,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Error"


class TestPipelineCoverageGaps:
    """Targeted tests for remaining uncovered lines."""

    def test_no_new_entries_after_skip(self) -> None:
        """When fetch_new returns [] after qBittorrent is connected, returns []."""
        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as _,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = []
            mock_source_cls.return_value = mock_source

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )
            assert results == []

    def test_game_not_found_with_magnet_still_fails(self) -> None:
        """Game not found on MC but with magnet URL should still fail."""
        from gamarr.models import GameEntry

        entry = GameEntry(
            title="Not Found",
            source_title="Not Found [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:abc",
            source_url="http://example.com/not-found",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc.lookup_game.return_value = None
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Failed"

    def test_game_passes_scores_no_magnet_fails(self) -> None:
        """Game that passes MC checks but has no magnet should fail."""
        import types

        from gamarr.models import GameEntry

        entry = GameEntry(
            title="No Magnet",
            source_title="No Magnet [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="",
            source_url="http://example.com/no-magnet",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            mock_mc_result = types.SimpleNamespace(
                title="No Magnet",
                metascore=85.0,
                user_score=8.0,
                metascore_review_count=50,
                user_review_count=200,
                genres=["Simulation"],
                must_play=False,
                release_date="2026-06-01",
            )
            mock_mc = MagicMock()
            mock_mc.lookup_game.return_value = mock_mc_result
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
                min_metascore=75,
                min_metascore_reviews=5,
                min_user_score=7.5,
                min_user_reviews=10,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Failed"
            assert "magnet" in results[0]["result_details"].lower()


class TestEvaluateScoresTbdBug:
    """Regression tests for TBD scores bug."""

    def test_missing_metascore_with_good_user_score_fails(self) -> None:
        """When metascore is None (TBD) but user_score is good, game should fail."""
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=None,
            metascore_review_count=None,
            user_score=8.5,
            user_review_count=200,
        )
        assert _evaluate_scores(mc_result, cfg) == "Failed"

    def test_missing_user_score_with_good_metascore_fails(self) -> None:
        """When user_score is None (TBD) but metascore is good, game should fail."""
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=50,
            user_score=None,
            user_review_count=None,
        )
        assert _evaluate_scores(mc_result, cfg) == "Failed"


class TestEvaluateScoresCoverage:
    """Additional _evaluate_scores coverage."""

    def test_user_score_below_threshold_metascore_good_fails(self) -> None:
        """When user_score is below threshold but metascore is good, should fail."""
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=50,
            user_score=5.0,
            user_review_count=200,
        )
        assert _evaluate_scores(mc_result, cfg) == "Failed"


class TestPipelineLibraryCheck:
    """Library check integration in the acquisition pipeline."""

    def test_library_match_skips_mc_lookup(self) -> None:
        """When a game is found in the library, MC lookup should NOT be called."""
        from unittest.mock import MagicMock, patch

        from gamarr.models import GameEntry

        entry = GameEntry(
            title="Elden Ring",
            source_title="Elden Ring [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:abc",
            source_url="http://example.com/elden-ring",
        )

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.library.os.path.isdir") as mock_isdir,
            patch("gamarr.library.os.walk") as mock_walk,
        ):
            mock_isdir.return_value = True
            mock_walk.return_value = [("/games", ["Elden Ring"], []), ("/games/Elden Ring", [], ["game.exe"])]

            mock_source = MagicMock()
            mock_source.fetch_new.return_value = [entry]
            mock_source_cls.return_value = mock_source

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
                library_paths=["/games"],
            )
            assert len(results) == 1
            assert results[0]["result"] == "Already owned"
            mock_mc = mock_mc_cls.return_value
            mock_mc.lookup_game.assert_not_called()


class TestEvaluateScoresNoneReviews:
    """When review counts are None, the game should fail."""

    def test_none_metascore_reviews_fails(self) -> None:
        """When metascore_review_count is None, game should fail."""
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=96.0,
            metascore_review_count=None,
            user_score=8.4,
            user_review_count=200,
        )
        assert _evaluate_scores(mc_result, cfg) == "Failed"

    def test_none_user_reviews_fails(self) -> None:
        """When user_review_count is None, game should fail."""
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type(
            "Cfg",
            (),
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )()
        mc_result = types.SimpleNamespace(
            metascore=96.0,
            metascore_review_count=93,
            user_score=8.4,
            user_review_count=None,
        )
        assert _evaluate_scores(mc_result, cfg) == "Failed"


class TestEscapeMarkup:
    """_escape_markup helper for log output."""

    def test_escape_markup_angle_brackets(self) -> None:
        from gamarr.pipeline import _escape_markup

        assert _escape_markup("<title>") == "\\<title\\>"
        assert _escape_markup("plain text") == "plain text"
        assert _escape_markup(42) == "42"


class TestEscapeOr:
    """_escape_or helper for conditional escaping."""

    def test_escape_or_escapes_value(self) -> None:
        from gamarr.pipeline import _escape_or

        assert _escape_or("hello", "N/A") == "hello"
        assert _escape_or("<b>bold</b>", "N/A") == "\\<b\\>bold\\</b\\>"

    def test_escape_or_none_returns_default(self) -> None:
        from gamarr.pipeline import _escape_or

        assert _escape_or(None, "?") == "?"
        assert _escape_or(None, "N/A") == "N/A"


class TestMetacriticBrowse:
    """Metacritic browse discovery phase."""

    def test_browse_qualifying_games_inserts_pending(self, tmp_path: Path) -> None:
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Elden Ring",
                "slug": "elden-ring",
                "score": 96,
                "critic_review_count": 120,
                "user_rating": 8.5,
                "user_review_count": 5000,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        _process_browse_games(browse_games, "pc", db, thresholds, pending_days=30)
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        assert pending[0].slug == "elden-ring"
        db.close()

    def test_match_pending_against_source(self, tmp_path: Path) -> None:
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))

        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            user_score=8.5,
            expires_at=expires,
        )

        db.rebuild_source_titles(
            "fitgirl",
            [
                {"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"},
            ],
        )

        matched = _match_pending_games(db)
        assert len(matched) == 1
        assert matched[0]["slug"] == "elden-ring"
        assert db.is_pending("elden-ring") is False
        db.close()

    def test_match_pending_expired_game(self, tmp_path: Path) -> None:
        """Expired pending games should be moved to history as 'Expired'."""
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        past = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=1)).isoformat()
        db.record_pending(
            slug="old-game",
            game_title="Old Game",
            platform="pc",
            expires_at=past,
        )
        matched = _match_pending_games(db)
        # Expired game should be returned with result "Expired"
        assert len(matched) == 1
        assert matched[0]["result"] == "Expired"
        assert db.is_pending("old-game") is False
        db.close()

    def test_browse_skips_below_threshold(self, tmp_path: Path) -> None:
        """Games below score thresholds should NOT be inserted as pending."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        games = [
            {
                "title": "Low Score Game",
                "slug": "low-score",
                "score": 30,
                "critic_review_count": 2,
                "user_rating": 4.0,
                "user_review_count": 1,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        new_count = _process_browse_games(games, "pc", db, thresholds, pending_days=30)
        assert new_count == 0
        pending = db.get_pending()
        assert len(pending) == 0
        db.close()


class TestRunAcquisitionMetacritic:
    """Full acquisition cycle with Metacritic browse enabled."""

    def test_acquisition_browse_and_match(self, tmp_path: Path) -> None:
        """End-to-end: browse inserts pending, sitemap matching moves to history."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        sitemap_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://fitgirl-repacks.site/elden-ring/</loc></url>
</urlset>"""

        with (
            patch("gamarr.sources.fitgirl.FitGirlSource.fetch_new", return_value=[]),
            patch("gamarr.sources.fitgirl.requests.get") as mock_get,
            patch("gamarr.pipeline.MetacriticClient") as _,
        ):
            # Mock sitemap fetch
            sitemap_resp = MagicMock()
            sitemap_resp.content = sitemap_xml
            sitemap_resp.raise_for_status = MagicMock()
            mock_get.return_value = sitemap_resp

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                db_path=str(tmp_path / "gamarr.db"),
                mc_cache_path=str(tmp_path / "mc-cache.db"),
                qbt_host="localhost",
                qbt_port=8080,
                qbt_username="admin",
                qbt_password="adminadmin",
            )

        assert isinstance(results, list)


class TestLogGameDetails:
    """_log_game_details end-to-end behaviour."""

    def test_log_game_details_logs_all_fields(self) -> None:
        from unittest.mock import patch

        from gamarr.metacritic import ScoreResult
        from gamarr.pipeline import _log_game_details

        mc = ScoreResult(
            title="Elden Ring",
            slug="elden-ring",
            metascore=96.0,
            metascore_review_count=120,
            user_score=8.5,
            user_review_count=5000,
            passed=True,
            genres=["Action", "RPG"],
            must_play=True,
            release_date="2022-02-25",
            description="A great game",
        )

        with patch("gamarr.pipeline.logger") as mock_logger:
            _log_game_details(mc)

        mock_logger.opt.assert_called_once_with(colors=True)
        mock_logger.opt.return_value.info.assert_called_once()
        msg = mock_logger.opt.return_value.info.call_args[0][0]
        assert "Elden Ring" in msg
        assert "96" in msg
        assert "120" in msg
        assert "8.5" in msg
        assert "5000" in msg
        assert "Action" in msg
        assert "Yes" in msg
        assert "2022-02-25" in msg

    def test_log_game_details_none_result_skips(self) -> None:
        from unittest.mock import patch

        from gamarr.pipeline import _log_game_details

        with patch("gamarr.pipeline.logger") as mock_logger:
            _log_game_details(None)

        mock_logger.opt.assert_not_called()

    def test_log_game_details_none_fields_use_fallbacks(self) -> None:
        from unittest.mock import patch

        from gamarr.metacritic import ScoreResult
        from gamarr.pipeline import _log_game_details

        mc = ScoreResult(
            title="Unknown Game",
            slug="unknown",
            metascore=None,
            metascore_review_count=None,
            user_score=None,
            user_review_count=None,
            passed=False,
            genres=None,
            must_play=None,
            release_date=None,
            description=None,
        )

        with patch("gamarr.pipeline.logger") as mock_logger:
            _log_game_details(mc)

        mock_logger.opt.assert_called_once_with(colors=True)
        mock_logger.opt.return_value.info.assert_called_once()
        msg = mock_logger.opt.return_value.info.call_args[0][0]
        assert "TBD" in msg
        assert "?" in msg
        assert "N/A" in msg
        assert "No" in msg


class TestSitemapDiscovery:
    """Sitemap-based game discovery respects days_since_release."""

    def test_process_sitemap_entries_filters_by_days_since_release(self, tmp_path: Path) -> None:
        """Sitemap entries older than days_since_release should be skipped.

        This reproduces the bug where increasing days_since_release had
        no effect on the discovery path — the browse pages (1-10) only
        contain unreviewed indies, and there was no sitemap-iterative
        discovery that actually used days_since_release.
        """
        import datetime
        import types
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_sitemap_entries

        db = Database(str(tmp_path / "gamarr.db"))

        recent_date = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        old_date = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=365)).strftime("%Y-%m-%d")

        db.rebuild_source_titles(
            "fitgirl",
            [
                {"title": "Recent Game", "url": "https://fitgirl-repacks.site/recent-game/"},
                {"title": "Old Game", "url": "https://fitgirl-repacks.site/old-game/"},
            ],
        )

        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=90,
        )

        result_recent = types.SimpleNamespace(
            title="Recent Game",
            slug="recent-game",
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=200,
            genres=["Action"],
            must_play=False,
            release_date=recent_date,
            description=None,
        )
        result_old = types.SimpleNamespace(
            title="Old Game",
            slug="old-game",
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=200,
            genres=["Action"],
            must_play=False,
            release_date=old_date,
            description=None,
        )

        def _lookup_side_effect(title: str, **kwargs: object) -> object:
            if "Recent" in title:
                return result_recent
            return result_old

        mock_mc = MagicMock()
        mock_mc.lookup_game.side_effect = _lookup_side_effect

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-test"
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        results = _process_sitemap_entries(
            source_name="fitgirl",
            mc=mock_mc,
            db=db,
            cfg=cfg,
            qbt=mock_qbt,
            notifier=mock_notifier,
            max_entries=10,
            magnet_fetcher=magnet_fetcher,
        )

        # Both entries are processed; Recent Game (30d) passes days_since_release=90,
        # Old Game (365d) fails the age check
        assert len(results) == 2, f"Expected 2 results, got {len(results)}: {results}"
        passed = [r for r in results if r["result"] == "Passed"]
        failed = [r for r in results if r["result"] == "Failed"]
        assert len(passed) == 1, f"Expected 1 passed, got {len(passed)}: {passed}"
        assert len(failed) == 1, f"Expected 1 failed, got {len(failed)}: {failed}"
        assert passed[0]["game_title"] == "Recent Game"

        assert mock_mc.lookup_game.call_count == 2
        mock_qbt.add_torrent.assert_called_once()
        # Magnet fetcher was called for the passing game (whichever it was)
        assert magnet_fetcher.call_count == 1
        assert any("recent" in call[0][0].lower() for call in magnet_fetcher.call_args_list)

        assert db.is_processed("fitgirl", "https://fitgirl-repacks.site/recent-game/") is True
        assert db.is_processed("fitgirl", "https://fitgirl-repacks.site/old-game/") is True

        db.close()

    def test_process_sitemap_entries_skips_already_processed(self, tmp_path: Path) -> None:
        """Sitemap entries already in the history DB should be skipped."""
        import types
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_sitemap_entries

        db = Database(str(tmp_path / "gamarr.db"))

        db.record_processed(
            source="fitgirl",
            source_title="Already Processed",
            source_url="https://fitgirl-repacks.site/already-done/",
            game_title="Already Processed",
            platform="pc",
            result="Passed",
        )

        db.rebuild_source_titles(
            "fitgirl",
            [
                {"title": "Already Processed", "url": "https://fitgirl-repacks.site/already-done/"},
                {"title": "New Game", "url": "https://fitgirl-repacks.site/new-game/"},
            ],
        )

        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=9999,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            title="New Game",
            slug="new-game",
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=200,
            genres=["Action"],
            must_play=False,
            release_date=None,
            description=None,
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-test"
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:new")

        results = _process_sitemap_entries(
            source_name="fitgirl",
            mc=mock_mc,
            db=db,
            cfg=cfg,
            qbt=mock_qbt,
            notifier=mock_notifier,
            max_entries=10,
            magnet_fetcher=magnet_fetcher,
        )

        assert len(results) == 1
        assert results[0]["game_title"] == "New Game"
        mock_mc.lookup_game.assert_called_once()
        magnet_fetcher.assert_called_once_with("https://fitgirl-repacks.site/new-game/")
        mock_qbt.add_torrent.assert_called_once()

        db.close()

    def test_process_sitemap_entries_mc_lookup_fails(self, tmp_path: Path) -> None:
        """When MC lookup returns None, the entry should be recorded as Failed."""
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_sitemap_entries

        db = Database(str(tmp_path / "gamarr.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Unknown Game", "url": "https://fitgirl-repacks.site/unknown-game/"}],
        )

        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=9999,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None  # Game not found on MC

        mock_qbt = MagicMock()
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock()

        results = _process_sitemap_entries(
            source_name="fitgirl",
            mc=mock_mc,
            db=db,
            cfg=cfg,
            qbt=mock_qbt,
            notifier=mock_notifier,
            max_entries=10,
            magnet_fetcher=magnet_fetcher,
        )

        assert len(results) == 1
        assert results[0]["result"] == "Failed"
        assert "not found" in results[0]["result_details"].lower()
        mock_mc.lookup_game.assert_called_once()
        mock_qbt.add_torrent.assert_not_called()
        magnet_fetcher.assert_not_called()

        db.close()

    def test_process_sitemap_entries_no_magnet_fails(self, tmp_path: Path) -> None:
        """When magnet fetcher returns None, the entry should be recorded as Failed."""
        import datetime
        import types
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_sitemap_entries

        db = Database(str(tmp_path / "gamarr.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Recent Game", "url": "https://fitgirl-repacks.site/recent-game/"}],
        )

        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=9999,
        )

        recent_date = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            title="Recent Game",
            slug="recent-game",
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=200,
            genres=["Action"],
            must_play=False,
            release_date=recent_date,
            description=None,
        )

        mock_qbt = MagicMock()
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock(return_value=None)  # No magnet found

        results = _process_sitemap_entries(
            source_name="fitgirl",
            mc=mock_mc,
            db=db,
            cfg=cfg,
            qbt=mock_qbt,
            notifier=mock_notifier,
            max_entries=10,
            magnet_fetcher=magnet_fetcher,
        )

        assert len(results) == 1
        assert results[0]["result"] == "Failed"
        assert "magnet" in results[0]["result_details"].lower()
        mock_mc.lookup_game.assert_called_once()
        magnet_fetcher.assert_called_once()
        mock_qbt.add_torrent.assert_not_called()

        db.close()

    def test_process_sitemap_entries_qbt_fails(self, tmp_path: Path) -> None:
        """When qBittorrent add_torrent fails, the entry should be recorded as Error."""
        import datetime
        import types
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_sitemap_entries

        db = Database(str(tmp_path / "gamarr.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Recent Game", "url": "https://fitgirl-repacks.site/recent-game/"}],
        )

        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=9999,
        )

        recent_date = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            title="Recent Game",
            slug="recent-game",
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=200,
            genres=["Action"],
            must_play=False,
            release_date=recent_date,
            description=None,
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = False  # qBittorrent failure
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        results = _process_sitemap_entries(
            source_name="fitgirl",
            mc=mock_mc,
            db=db,
            cfg=cfg,
            qbt=mock_qbt,
            notifier=mock_notifier,
            max_entries=10,
            magnet_fetcher=magnet_fetcher,
        )

        assert len(results) == 1
        assert results[0]["result"] == "Error"
        assert "torrent" in results[0]["result_details"].lower()
        mock_mc.lookup_game.assert_called_once()
        magnet_fetcher.assert_called_once()
        mock_qbt.add_torrent.assert_called_once()

        db.close()

    def test_process_sitemap_entries_score_fails(self, tmp_path: Path) -> None:
        """When a game fails score checks, it should be recorded as Failed."""
        import datetime
        import types
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_sitemap_entries

        db = Database(str(tmp_path / "gamarr.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Low Score", "url": "https://fitgirl-repacks.site/low-score/"}],
        )

        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=9999,
        )

        recent_date = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

        mock_mc = MagicMock()
        # Low metascore should fail thresholds
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            title="Low Score",
            slug="low-score",
            metascore=30.0,
            metascore_review_count=5,
            user_score=2.0,
            user_review_count=10,
            genres=["Action"],
            must_play=False,
            release_date=recent_date,
            description=None,
        )

        mock_qbt = MagicMock()
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock()

        results = _process_sitemap_entries(
            source_name="fitgirl",
            mc=mock_mc,
            db=db,
            cfg=cfg,
            qbt=mock_qbt,
            notifier=mock_notifier,
            max_entries=10,
            magnet_fetcher=magnet_fetcher,
        )

        assert len(results) == 1
        assert results[0]["result"] == "Failed"
        assert "score" in results[0]["result_details"].lower()
        mock_mc.lookup_game.assert_called_once()
        mock_qbt.add_torrent.assert_not_called()
        magnet_fetcher.assert_not_called()

        db.close()

    def test_process_sitemap_entries_exception_isolation(self, tmp_path: Path) -> None:
        """When one entry raises an exception, the batch should continue."""
        import datetime
        import types
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_sitemap_entries

        db = Database(str(tmp_path / "gamarr.db"))
        db.rebuild_source_titles(
            "fitgirl",
            [
                {"title": "Good Game", "url": "https://fitgirl-repacks.site/good-game/"},
                {"title": "Crash Game", "url": "https://fitgirl-repacks.site/crash-game/"},
                {"title": "Another Good", "url": "https://fitgirl-repacks.site/another-good/"},
            ],
        )

        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=9999,
        )

        recent_date = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

        good_result = types.SimpleNamespace(
            title="Good Game",
            slug="good-game",
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=200,
            genres=["Action"],
            must_play=False,
            release_date=recent_date,
            description=None,
        )

        call_count = [0]

        def _side_effect(*args: object, **kwargs: object) -> object:
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("Simulated crash")
            return good_result

        mock_mc = MagicMock()
        mock_mc.lookup_game.side_effect = _side_effect

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-test"
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        results = _process_sitemap_entries(
            source_name="fitgirl",
            platform="pc",
            mc=mock_mc,
            db=db,
            cfg=cfg,
            qbt=mock_qbt,
            notifier=mock_notifier,
            max_entries=10,
            magnet_fetcher=magnet_fetcher,
        )

        assert len(results) == 3, f"Expected 3 results, got {len(results)}: {results}"
        passed = [r for r in results if r["result"] == "Passed"]
        errors = [r for r in results if r["result"] == "Error"]
        assert len(passed) == 2, f"Expected 2 passed, got {len(passed)}"
        assert len(errors) == 1, f"Expected 1 error, got {len(errors)}"

        db.close()

    def test_default_magnet_fetcher_requests_failure(self) -> None:
        """_default_magnet_fetcher should return None on HTTP failure."""
        from unittest.mock import patch

        from gamarr.pipeline import _default_magnet_fetcher

        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_get.side_effect = ConnectionError("Network error")
            result = _default_magnet_fetcher("https://example.com/")
            assert result is None
            mock_get.assert_called_once()

    def test_default_magnet_fetcher_requests_success_no_magnet(self) -> None:
        """_default_magnet_fetcher should return None when no magnet found."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _default_magnet_fetcher

        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = "<html>No magnet here</html>"
            mock_get.return_value = mock_resp
            result = _default_magnet_fetcher("https://example.com/")
            assert result is None
            mock_get.assert_called_once()
            mock_resp.raise_for_status.assert_called_once()

    def test_default_magnet_fetcher_requests_success_with_magnet(self) -> None:
        """_default_magnet_fetcher should return magnet URI when found."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import _default_magnet_fetcher

        with patch("gamarr.pipeline.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = '<html><a href="magnet:?xt=urn:btih:abc123&dn=test">magnet</a></html>'
            mock_get.return_value = mock_resp
            result = _default_magnet_fetcher("https://example.com/")
            assert result == "magnet:?xt=urn:btih:abc123&dn=test"
            mock_get.assert_called_once()
            mock_resp.raise_for_status.assert_called_once()

    def test_process_sitemap_entries_skips_when_empty(self, tmp_path: Path) -> None:
        """_process_sitemap_entries returns empty when no source titles exist."""
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_sitemap_entries

        db = Database(str(tmp_path / "gamarr.db"))
        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=9999,
        )

        results = _process_sitemap_entries(
            source_name="fitgirl",
            platform="pc",
            mc=MagicMock(),
            db=db,
            cfg=cfg,
            qbt=MagicMock(),
            notifier=MagicMock(),
        )

        assert results == []
        db.close()

    def test_process_sitemap_entries_respects_max_entries(self, tmp_path: Path) -> None:
        """Only max_entries should be processed per run."""
        import datetime
        import types
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig, _process_sitemap_entries

        db = Database(str(tmp_path / "gamarr.db"))
        # Add 5 entries but set max_entries=2
        entries = [{"title": f"Game {i}", "url": f"https://fitgirl-repacks.site/game-{i}/"} for i in range(5)]
        db.rebuild_source_titles("fitgirl", entries)

        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            days_since_release=9999,
        )

        recent_date = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

        good_result = types.SimpleNamespace(
            title="Game 0",
            slug="game-0",
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=200,
            genres=["Action"],
            must_play=False,
            release_date=recent_date,
            description=None,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = good_result

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-test"
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        results = _process_sitemap_entries(
            source_name="fitgirl",
            platform="pc",
            mc=mock_mc,
            db=db,
            cfg=cfg,
            qbt=mock_qbt,
            notifier=mock_notifier,
            max_entries=2,
            magnet_fetcher=magnet_fetcher,
        )

        # Only 2 of 5 entries processed
        assert len(results) == 2, f"Expected 2 results, got {len(results)}"
        assert all(r["result"] == "Passed" for r in results)
        assert mock_mc.lookup_game.call_count == 2

        db.close()
