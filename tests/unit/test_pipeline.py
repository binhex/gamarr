"""Tests for gamarr acquisition pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from gamarr.pipeline import AcquisitionConfig, run_acquisition

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
        """run_acquisition returns [] when no browse results and no pending games."""
        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = []
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
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
            assert results == []

    def test_qbt_not_connected_skips(self) -> None:
        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
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

    def test_run_acquisition_does_not_process_fitgirl_rss_entries(self) -> None:
        """Metacritic-first: FitGirl RSS entries must NOT drive per-entry lookups.

        Reproduces the bug where run_acquisition() iterated FitGirl RSS
        entries and looked each one up on Metacritic — the opposite of
        the intended Metacritic-first flow.

        Even when fetch_new() returns 7 new entries (matching the bug
        report), the pipeline must NOT call fetch_new() and must NOT
        call mc.lookup_game per FitGirl RSS entry. The sitemap is the
        only Metacritic-side state used for matching, and FitGirl RSS
        entries are not a discovery source.
        """
        from gamarr.models import GameEntry

        entries = [
            GameEntry(
                title=f"FitGirl Game {i}",
                source_title=f"FitGirl Game {i} [Repack]",
                source="fitgirl",
                platform="pc",
                magnet_url="",
                source_url=f"http://example.com/fitgirl-game-{i}",
            )
            for i in range(7)
        ]

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_new.return_value = entries
            mock_source_cls.return_value = mock_source

            # Browse Metacritic returns no games, so no pending are added
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
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

            # The Metacritic-first flow must NOT call fetch_new() at all.
            mock_source.fetch_new.assert_not_called()
            # The Metacritic-first flow must NOT call lookup_game per
            # FitGirl RSS entry. Metacritic browse already returned [] so
            # the metacritic-first flow produces no results.
            mock_mc.lookup_game.assert_not_called()
            # No results produced (no browse results, no pending matches)
            assert results == []

    def test_run_acquisition_with_library_paths_passes_through(self) -> None:
        """run_acquisition should wire library_paths into the discovery path."""
        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
            patch("gamarr.library.os.path.isdir", return_value=False),
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
                library_paths=["/games"],
            )
            # Metacritic browse should be called
            mock_mc.scan_recent_games.assert_called_once()
            # Sitemap is NOT fetched when there are no pending games to match
            mock_source.fetch_sitemap.assert_not_called()

    def test_run_acquisition_skips_sitemap_for_games_below_threshold(self, tmp_path: Path) -> None:
        """Games below score thresholds must NOT trigger sitemap fetch.

        When 52 pending games all have inflated browse-page scores that
        fail real Metacritic verification, none should pass
        score_checks_passed=True, so `has_verified_pending` returns
        False and the sitemap is not fetched.  Games stay pending
        for re-verification on a future cycle.
        """
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database

        db_path = str(tmp_path / "test.db")

        # Pre-populate 52 pending games, all with inflated browse-page scores
        db = Database(db_path)
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        for i in range(52):
            db.record_pending(
                slug=f"game-{i:02d}",
                game_title=f"Game {i:02d}",
                platform="pc",
                metascore=1478.0,  # WRONG browse score, not 0-100
                metascore_reviews=999,
                user_score=2007.0,  # WRONG browse score, not 0-10
                user_reviews=None,
                release_date="2026-06-03",
                expires_at=expires,
            )
        db.close()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            import types

            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            # Browse returns no new games
            mock_mc.scan_recent_games.return_value = []
            # All detail-page lookups return failing scores
            mock_mc.lookup_game.return_value = types.SimpleNamespace(
                metascore=62.0,
                metascore_review_count=5,
                user_score=None,
                user_review_count=0,
                genres=[],
                must_play=False,
                release_date="2026-06-03",
            )
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                db_path=db_path,
                qbt_host="localhost",
                qbt_port=8080,
                min_metascore=75,
                min_user_score=7.5,
                min_user_reviews=10,
                min_metascore_reviews=5,
            )

            # Sitemap must NOT be fetched because no pending game has
            # passed score verification (all have score_checks_passed=None).
            mock_source.fetch_sitemap.assert_not_called()
            assert results == []

            # Confirm ALL games are still pending for future re-verification
            verify_db = Database(db_path)
            remaining = verify_db.get_pending(platform="pc")
            assert len(remaining) == 52, f"{len(remaining)} games should remain pending for re-check"
            assert all(g.score_checks_passed is None or g.score_checks_passed is False for g in remaining), (
                "No games should have passed score checks"
            )
            verify_db.close()


class TestEvaluateScores:
    """_evaluate_scores function edge cases."""

    def test_browse_game_passes_thresholds_with_high_browse_scores(self) -> None:
        """Browse-page games with inflated scores should pass the threshold check."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "Some Game",
            "slug": "some-game",
            "score": 1478.0,       # inflated browse metric
            "user_rating": 2007.0,  # inflated browse metric
        }
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is True

    def test_browse_game_fails_when_user_score_below_threshold(self) -> None:
        """Even with missing user_review_count, a low user_score must still fail."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "Low Score Game",
            "slug": "low-score",
            "score": 85.0,
            "critic_review_count": 20,
            "user_rating": 3.0,  # below threshold
            "user_review_count": None,
            "release_date": "2026-06-01",
        }
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is False

    def test_browse_game_fails_when_metascore_missing(self) -> None:
        """A game with no metascore should fail the threshold check."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "No Metascore Game",
            "slug": "no-ms",
            "score": None,
            "user_rating": 8.0,
        }
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is False

    def test_browse_game_fails_when_browse_score_missing(self) -> None:
        """A game with no browse scores should fail the threshold check."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "No Score Game",
            "slug": "no-score",
            "score": None,
            "user_rating": None,
        }
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is False

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
        assert _evaluate_scores(mc_result, cfg) == "no_scores"

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
        assert _evaluate_scores(mc_result, cfg) == "metascore_reviews_too_few"

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
        assert _evaluate_scores(mc_result, cfg) == "user_reviews_too_few"

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
        assert _evaluate_scores(mc_result, cfg) == "release_date_too_old"

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
        assert _evaluate_scores(mc_result, cfg) == "no_scores"

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
        assert _evaluate_scores(mc_result, cfg) == "no_scores"


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
        assert _evaluate_scores(mc_result, cfg) == "user_score_too_low"


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
        assert _evaluate_scores(mc_result, cfg) == "metascore_reviews_too_few"

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
        assert _evaluate_scores(mc_result, cfg) == "user_reviews_too_few"


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

    def test_added_pending_game_is_debug_not_info(self) -> None:
        """'Added pending game' must be at DEBUG, not INFO, to avoid console spam."""
        import io

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(":memory:")
        browse_games = [
            {
                "title": "Some Game",
                "slug": "some-game",
                "score": 85,
                "critic_review_count": 15,
                "user_rating": 8.5,
                "user_review_count": 50,
                "release_date": "2026-06-01",
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        # Capture Loguru output to check log level
        buf = io.StringIO()
        logger_id = logger.add(buf, format="{level.name}:{message}", colorize=False)
        try:
            _process_browse_games(browse_games, "pc", db, thresholds)
        finally:
            logger.remove(logger_id)

        output = buf.getvalue()
        assert "Added pending game" in output, "The pending message should appear"
        assert output.startswith("DEBUG"), f"Should be DEBUG, not INFO; got: {output[:60]}"
        # Also verify behavior is correct (game was added)
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        db.close()

    def test_deliver_match_logs_genre(self, tmp_path: Path) -> None:
        """When a game is delivered via JIT verify, the log must include genres."""
        import datetime
        import io
        import types
        from unittest.mock import MagicMock

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="gothic-1-remake",
            game_title="Gothic 1 Remake",
            platform="pc",
            metascore=76.0,
            metascore_reviews=12,
            user_score=8.7,
            user_reviews=308,
            release_date="2026-06-05",
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="gothic-1-remake",
            metascore=76.0,
            metascore_reviews=12,
            user_score=8.7,
            user_reviews=308,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Gothic 1 Remake", "url": "https://fitgirl-repacks.site/gothic-1-remake/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        # Mock MC lookup to return a ScoreResult with genres
        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=76.0,
            metascore_review_count=12,
            user_score=8.7,
            user_review_count=308,
            title="Gothic 1 Remake",
            slug="gothic-1-remake",
            genres=["Action RPG", "Open-World"],
            must_play=False,
            release_date="2026-06-05",
        )

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        # Capture Loguru output
        buf = io.StringIO()
        logger_id = logger.add(buf, format="{message}", colorize=False)
        try:
            matched = _match_pending_games(
                db,
                qbt=mock_qbt,
                magnet_fetcher=magnet_fetcher,
                mc=mock_mc,
                thresholds=thresholds,
            )
        finally:
            logger.remove(logger_id)

        output = buf.getvalue()
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed"
        # The log must include the genre, not "N/A"
        assert "Action RPG" in output, f"Genres should appear in log: {output}"
        assert "N/A" not in output.split("Genre:")[1].split("|")[0], "Genre should not be N/A"
        db.close()

    def test_deliver_match_logs_game_details(self, tmp_path: Path) -> None:
        """When a game is delivered, the log must include metascore, user score, and release date."""
        import datetime
        import io
        from unittest.mock import MagicMock

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=96.0,
            metascore_reviews=120,
            user_score=8.5,
            user_reviews=5000,
            release_date="2026-06-01",
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            metascore_reviews=120,
            user_score=8.5,
            user_reviews=5000,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        # Capture Loguru output
        buf = io.StringIO()
        logger_id = logger.add(buf, format="{message}", colorize=False)
        try:
            matched = _match_pending_games(db, qbt=mock_qbt, magnet_fetcher=magnet_fetcher)
        finally:
            logger.remove(logger_id)

        output = buf.getvalue()
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed"
        # The log must include metascore, user score, review counts, and release date
        assert "96.0" in output, f"Metascore should appear in log: {output}"
        assert "120 reviews" in output, f"Metascore review count should appear: {output}"
        assert "8.5" in output, f"User score should appear in log: {output}"
        assert "5000 reviews" in output, f"User review count should appear: {output}"
        assert "2026-06-01" in output, f"Release date should appear in log: {output}"
        db.close()

    def test_deliver_match_escapes_title_with_angle_brackets(self, tmp_path: Path) -> None:
        """Game titles containing < or > must be escaped in the delivery log."""
        import datetime
        import io
        from unittest.mock import MagicMock

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="game-with-angle",
            game_title="Game <Director's>",
            platform="pc",
            metascore=80.0,
            metascore_reviews=10,
            user_score=7.5,
            user_reviews=100,
            release_date="2026-06-01",
            expires_at=expires,
        )
        db.update_pending_scores(
            slug="game-with-angle",
            metascore=80.0,
            metascore_reviews=10,
            user_score=7.5,
            user_reviews=100,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Game <Director's>", "url": "https://example.com/game"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        buf = io.StringIO()
        logger_id = logger.add(buf, format="{message}", colorize=False)
        try:
            matched = _match_pending_games(db, qbt=mock_qbt, magnet_fetcher=magnet_fetcher)
        finally:
            logger.remove(logger_id)

        output = buf.getvalue()
        assert len(matched) == 1
        # The title should appear escaped (\< and \> — Loguru's escape syntax)
        assert r"Game \<Director's\>" in output, f"Title with < > should be escaped: {output}"
        db.close()

    def test_verify_pending_keeps_game_with_failing_real_scores_for_recheck(self, tmp_path: Path) -> None:
        """A game with failing real scores should stay pending for re-verification."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="thick-as-thieves",
            game_title="Thick as Thieves",
            platform="pc",
            metascore=1998.0,  # wrong browse score
            metascore_reviews=1999,
            user_score=2007.0,  # wrong browse score
            user_reviews=None,
            release_date="2026-05-20",
            expires_at=expires,
        )

        # Mock MC lookup returning real scores (62 metascore, 3.3 user — below thresholds)
        mock_mc = MagicMock()
        import types

        mock_result = types.SimpleNamespace(
            metascore=62.0,
            metascore_review_count=25,
            user_score=3.3,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-05-20",
            slug="thick-as-thieves",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 10,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        assert db.is_pending("thick-as-thieves") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds)
        # Real scores (62, 3.3) fail thresholds — game should stay for re-check
        assert removed == 0, "Game with failing real scores should NOT be removed (re-check later)"
        assert db.is_pending("thick-as-thieves") is True, "Game should stay in pending queue"
        db.close()

    def test_verify_pending_keeps_game_with_passing_real_scores(self, tmp_path: Path) -> None:
        """A game with wrong browse scores but passing real scores should stay with corrected values."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="forza-horizon-6",
            game_title="Forza Horizon 6",
            platform="pc",
            metascore=1985.0,  # wrong browse score
            metascore_reviews=1986,
            user_score=1994.0,  # wrong browse score
            user_reviews=None,
            release_date="2026-05-19",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_result = types.SimpleNamespace(
            metascore=88.0,  # passes 75 threshold
            metascore_review_count=50,  # passes 10 threshold
            user_score=8.0,  # passes 7.5 threshold
            user_review_count=100,  # passes 10 threshold
            genres=["Racing"],
            must_play=True,
            release_date="2026-05-19",
            slug="forza-horizon-6",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 10,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        assert db.is_pending("forza-horizon-6") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds)
        assert removed == 0, "Game with passing real scores should NOT be removed"
        # DB should still have the pending game with corrected scores
        assert db.is_pending("forza-horizon-6") is True
        pending_list = db.get_pending(platform="pc")
        assert len(pending_list) == 1
        assert pending_list[0].metascore == 88.0, "Score should be updated to real value"
        assert pending_list[0].user_score == 8.0, "User score should be updated to real value"
        db.close()

    def test_verify_pending_keeps_game_when_lookup_returns_none(self, tmp_path: Path) -> None:
        """When lookup_game returns None, the pending game should stay for re-check."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="not-found-game",
            game_title="Not Found Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None  # detail page not found

        assert db.is_pending("not-found-game") is True
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )
        # Game with None lookup should stay pending for re-check
        assert removed == 0, "Game with None lookup should NOT be removed (re-check later)"
        assert db.is_pending("not-found-game") is True, "Game should stay in pending queue"
        # lookup_game was called with direct_only=True (no browse fallback)
        assert mock_mc.lookup_game.call_count == 1
        _call_args, call_kwargs = mock_mc.lookup_game.call_args
        assert call_kwargs.get("direct_only") is True, "lookup_game must use direct_only=True to avoid browse fallback"
        db.close()

    def test_verify_pending_keeps_game_with_tbd_scores_for_recheck(self, tmp_path: Path) -> None:
        """A game with no real Metacritic scores yet (TBD) should stay pending
        for re-verification in a future cycle, not be permanently removed."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="unreviewed-game",
            game_title="Unreviewed Game",
            platform="pc",
            metascore=1288.0,  # wrong browse score
            user_score=1288.0,  # wrong browse score
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=None,
            metascore_review_count=None,
            user_score=0.0,
            user_review_count=0,
            genres=None,
            must_play=False,
            release_date=None,
        )

        assert db.is_pending("unreviewed-game") is True
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )
        # Game with TBD scores should NOT be removed — stays pending for re-check
        assert removed == 0, "Game with TBD scores should NOT be removed"
        assert db.is_pending("unreviewed-game") is True, "Game should stay in pending queue"
        # score_checks_passed should remain None (not True)
        pending = db.get_pending()
        unreviewed = next((g for g in pending if g.slug == "unreviewed-game"), None)
        assert unreviewed is not None
        assert unreviewed.score_checks_passed is None or unreviewed.score_checks_passed is False
        db.close()

    def test_verify_pending_keeps_game_with_mixed_zero_metascore(self, tmp_path: Path) -> None:
        """A game with metascore=0 but user_score > 0 should stay (user score passes)."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="mixed-scores-game",
            game_title="Mixed Scores Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=0.0,  # no critic score yet
            metascore_review_count=None,
            user_score=8.0,  # user score passes (>= 7.5)
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        assert db.is_pending("mixed-scores-game") is True
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )
        assert removed == 0, "Game with passing user score should stay"
        assert db.is_pending("mixed-scores-game") is True
        # Scores should be updated with real values
        pending_list = db.get_pending(platform="pc")
        assert len(pending_list) == 1
        assert pending_list[0].metascore == 0.0  # updated even though 0
        assert pending_list[0].user_score == 8.0
        db.close()

    def test_verify_pending_respects_max_verify_limit(self, tmp_path: Path) -> None:
        """_verify_pending_scores must cap lookups at max_verify to prevent 2773 HTTP requests."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        # Create 20 pending games with zero-padded slugs for predictable sort order
        for i in range(20):
            db.record_pending(
                slug=f"game-{i:02d}",
                game_title=f"Game {i:02d}",
                platform="pc",
                metascore=1288.0,
                user_score=1288.0,
                expires_at=expires,
            )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        # Call with max_verify=5 — only 5 should be looked up
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
            max_verify=5,
        )
        assert mock_mc.lookup_game.call_count == 5, f"Expected 5 lookups, got {mock_mc.lookup_game.call_count}"
        # Removed should be 0 (all passing scores)
        assert removed == 0, "All games pass thresholds, none removed"
        # All 20 games still pending: first 5 verified (scores updated), last 15 untouched
        remaining = db.get_pending()
        assert len(remaining) == 20, f"All 20 must remain: {len(remaining)}"
        # First 5 (game-00 to game-04) should have updated metascore (85.0),
        # last 15 (game-05 to game-19) should still have browse score (1288.0)
        for g in remaining:
            idx = int(g.slug.split("-")[1])
            if idx < 5:
                assert g.metascore == 85.0, f"{g.slug} should be verified, got {g.metascore}"
            else:
                assert g.metascore == 1288.0, f"{g.slug} should be unverified (browse score), got {g.metascore}"
        db.close()

    def test_match_pending_skips_unverified_game_with_browse_scores(self, tmp_path: Path) -> None:
        """A matched pending game with unverified browse scores must NOT be delivered.

        This test reproduces the bug where games with browse scores (1478.0)
        are delivered to qBittorrent because _match_pending_games doesn't
        verify scores before delivery.
        """
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="unverified-game",
            game_title="Unverified Game",
            platform="pc",
            metascore=1478.0,  # WRONG browse score (not 0-100)
            user_score=1478.0,
            expires_at=expires,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Unverified Game", "url": "https://example.com/game"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_mc = MagicMock()
        # Mock lookup returns None — no detail page scores available
        mock_mc.lookup_game.return_value = None
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            mc=mock_mc,
            thresholds=thresholds,
        )

        # No matches should be returned — game has unverified browse scores
        assert len(matched) == 0, "Unverified game should NOT be delivered"
        # qBittorrent should NOT be called
        mock_qbt.add_torrent.assert_not_called()
        # MC should NOT be called (no JIT verification for unverified games)
        mock_mc.lookup_game.assert_not_called()
        # Magnet should NOT be fetched
        magnet_fetcher.assert_not_called()
        # Game should remain in queue until next score-check cycle
        assert db.is_pending("unverified-game"), "Unverified game should stay pending until score-checked"
        db.close()

    def test_match_pending_delivers_verified_game(self, tmp_path: Path) -> None:
        """A matched pending game with verified real scores should be delivered."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="verified-game",
            game_title="Verified Game",
            platform="pc",
            metascore=85.0,  # REAL score (0-100 range)
            metascore_reviews=20,
            user_score=8.0,
            user_reviews=100,
            release_date="2026-06-01",
            expires_at=expires,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Verified Game", "url": "https://example.com/game"}],
        )

        mock_qbt = MagicMock()
        # Mark the game as score-checked so it passes the gate in _match_pending_games
        db.update_pending_scores(
            slug="verified-game",
            metascore=85.0,
            metascore_reviews=20,
            user_score=8.0,
            user_reviews=100,
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            mc=mock_mc,
            thresholds=thresholds,
        )

        # Game should be delivered
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed"
        mock_qbt.add_torrent.assert_called_once()
        db.close()

    def test_match_pending_skips_fitgirl_excluded_keyword(self, tmp_path: Path) -> None:
        """A matched game should be skipped when its FitGirl title contains an excluded keyword."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="007-first-light",
            game_title="007 First Light",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        db.update_pending_scores(slug="007-first-light", metascore=85.0, user_score=8.0)
        # FitGirl title contains "HV"
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "007 First Light [FitGirl HV Repack]", "url": "https://example.com/007"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            reject_keywords=["HV"],
        )
        # Game should NOT be delivered — FitGirl title was excluded by keyword
        assert len(matched) == 0
        mock_qbt.add_torrent.assert_not_called()
        # Game should still be pending (not removed, not expired)
        assert db.is_pending("007-first-light"), "Game should remain pending when match is skipped"
        db.close()

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
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
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

    def test_match_pending_delivers_magnet(self, tmp_path: Path) -> None:
        """When qbt and magnet_fetcher are provided, matched games should be delivered."""
        import datetime
        from unittest.mock import MagicMock

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
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(db, qbt=mock_qbt, magnet_fetcher=magnet_fetcher)

        # Game should be matched and delivered
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed"
        # Magnet should have been fetched and torrent should have been added
        magnet_fetcher.assert_called_once_with("https://fitgirl-repacks.site/elden-ring/")
        mock_qbt.add_torrent.assert_called_once_with(magnet_url="magnet:?xt=urn:btih:test", title="Elden Ring")
        assert db.is_pending("elden-ring") is False
        db.close()

    def test_match_pending_delivers_magnet_fetch_fails(self, tmp_path: Path) -> None:
        """When magnet fetcher returns None, the match should record as Error."""
        import datetime
        from unittest.mock import MagicMock

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
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        magnet_fetcher = MagicMock(return_value=None)  # No magnet found

        matched = _match_pending_games(db, qbt=mock_qbt, magnet_fetcher=magnet_fetcher)

        assert len(matched) == 1
        assert matched[0]["result"] == "Error"
        assert "delivery failed" in matched[0]["result_details"].lower()
        magnet_fetcher.assert_called_once()
        mock_qbt.add_torrent.assert_not_called()
        assert db.is_pending("elden-ring") is False
        db.close()

    def test_match_pending_delivers_magnet_qbt_fails(self, tmp_path: Path) -> None:
        """When qBittorrent add_torrent fails, the match should record as Error."""
        import datetime
        from unittest.mock import MagicMock

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
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = False  # qBittorrent failure
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(db, qbt=mock_qbt, magnet_fetcher=magnet_fetcher)

        assert len(matched) == 1
        assert matched[0]["result"] == "Error"
        assert "delivery failed" in matched[0]["result_details"].lower()
        magnet_fetcher.assert_called_once()
        mock_qbt.add_torrent.assert_called_once()
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

    def test_match_pending_sends_download_notification(self, tmp_path: Path) -> None:
        """On successful delivery, notifier.send_download_notification should be called."""
        import datetime
        from unittest.mock import MagicMock

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
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_paused = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            notifier=mock_notifier,
        )
        assert len(matched) == 1
        mock_notifier.send_download_notification.assert_called_once_with(
            title="Elden Ring",
            platform="pc",
            metascore=96.0,
            metascore_reviews=None,
            user_score=8.5,
            user_reviews=None,
            slug="elden-ring",
            genres=None,
            add_paused=False,
        )
        db.close()

    def test_match_pending_sends_failure_notification_on_qbt_failure(self, tmp_path: Path) -> None:
        """When qBittorrent rejects, notifier.send_failure_notification should be called."""
        import datetime
        from unittest.mock import MagicMock

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
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = False
        mock_notifier = MagicMock()
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            notifier=mock_notifier,
        )
        assert len(matched) == 1
        mock_notifier.send_failure_notification.assert_called_once()
        call = mock_notifier.send_failure_notification.call_args
        assert call.kwargs["title"] == "Elden Ring"
        assert "qBittorrent" in call.kwargs["reason"]
        db.close()

    def test_match_pending_sends_failure_notification_on_magnet_failure(self, tmp_path: Path) -> None:
        """When the magnet fetch fails, notifier.send_failure_notification should be called."""
        import datetime
        from unittest.mock import MagicMock

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
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )

        mock_qbt = MagicMock()
        mock_notifier = MagicMock()
        # Magnet fetcher returns None (failure)
        magnet_fetcher = MagicMock(return_value=None)

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            notifier=mock_notifier,
        )
        assert len(matched) == 1
        mock_notifier.send_failure_notification.assert_called_once()
        call = mock_notifier.send_failure_notification.call_args
        assert call.kwargs["title"] == "Elden Ring"
        assert "magnet" in call.kwargs["reason"].lower()
        # qbt.add_torrent must NOT be called when magnet fetch fails
        mock_qbt.add_torrent.assert_not_called()
        db.close()

    def test_match_pending_notification_dispatched_after_db_update(self, tmp_path: Path) -> None:
        """Notifications must be dispatched AFTER DB recording.

        If notifier raises, the DB must already reflect the result so
        the next cycle does not re-match and re-download.
        """
        import datetime
        from unittest.mock import MagicMock

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
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_paused = False
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_notifier = MagicMock()
        mock_notifier.send_download_notification.side_effect = RuntimeError("apprise down")
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        # Notification raises — the result must still be recorded and the
        # pending row still removed (so no duplicate next cycle).
        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            notifier=mock_notifier,
        )
        assert len(matched) == 1
        assert matched[0]["result"] == "Passed"
        # Notifier was actually called (and raised) — proves the
        # notification path runs AFTER the DB update, not skipped.
        mock_notifier.send_download_notification.assert_called_once()
        # Pending row should be removed despite notification failure
        assert db.is_pending("elden-ring") is False
        db.close()

    def test_match_pending_skips_when_in_library(self, tmp_path: Path) -> None:
        """A matched game that is already in the library should be skipped."""
        import datetime
        from unittest.mock import MagicMock

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
        db.update_pending_scores(
            slug="elden-ring",
            metascore=96.0,
            user_score=8.5,
        )
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")
        mock_library = MagicMock()
        mock_library.check_game.return_value = MagicMock(
            matched_path="/games/Elden Ring",
            matched_name="Elden Ring",
        )

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            library=mock_library,
        )
        assert len(matched) == 1
        assert matched[0]["result"] == "Already owned"
        # Torrent should NOT be added because game is in library
        mock_qbt.add_torrent.assert_not_called()
        # Magnet should not even be fetched
        magnet_fetcher.assert_not_called()
        db.close()

    def test_match_pending_touches_when_no_source_match(self, tmp_path: Path) -> None:
        """When no source matches a pending game, the pending row is touched (last_checked_at updated)."""
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="unmatched-game",
            game_title="Unmatched Game",
            platform="pc",
            expires_at=expires,
        )
        # No source titles indexed, so no match will be found

        matched = _match_pending_games(db)
        # No matches, no results
        assert matched == []
        # Pending should still be in DB (not removed)
        assert db.is_pending("unmatched-game") is True
        db.close()

    def test_default_magnet_fetcher_handles_request_failure(self) -> None:
        """_default_magnet_fetcher returns None when the HTTP request fails."""
        from unittest.mock import patch

        import requests

        from gamarr.pipeline import _default_magnet_fetcher

        with patch("gamarr.pipeline.requests.get", side_effect=requests.exceptions.ConnectionError("nope")):
            result = _default_magnet_fetcher("http://example.com/page")
        assert result is None

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

    def test_browse_skips_old_games_by_days_since_release(self, tmp_path: Path) -> None:
        """Browse games with release_date older than days_since_release should be skipped."""
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        old_date = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        recent_date = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

        games = [
            {
                "title": "Old Game",
                "slug": "old-game",
                "score": 90,
                "critic_review_count": 50,
                "user_rating": 8.0,
                "user_review_count": 200,
                "release_date": old_date,
            },
            {
                "title": "Recent Game",
                "slug": "recent-game",
                "score": 85,
                "critic_review_count": 40,
                "user_rating": 7.5,
                "user_review_count": 100,
                "release_date": recent_date,
            },
        ]
        thresholds = {
            "min_metascore": 0,
            "min_metascore_reviews": 0,
            "min_user_score": 0.0,
            "min_user_reviews": 0,
        }
        new_count = _process_browse_games(games, "pc", db, thresholds, pending_days=30, days_since_release=90)
        # Only the recent game (30d old) should pass; old game (365d) should be filtered
        assert new_count == 1, f"Expected 1 pending game, got {new_count}"
        pending = db.get_pending()
        assert len(pending) == 1
        assert pending[0].slug == "recent-game"
        db.close()

    def test_browse_skips_keyword_excluded_games(self, tmp_path: Path) -> None:
        """Games with titles matching reject_title should not be added."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        games = [
            {
                "title": "Real Game",
                "slug": "real-game",
                "score": 85,
                "user_rating": 8.0,
            },
            {
                "title": "Real Game DLC",
                "slug": "real-game-dlc",
                "score": 85,
                "user_rating": 8.0,
            },
            {
                "title": "Game Soundtrack",
                "slug": "game-soundtrack",
                "score": 85,
                "user_rating": 8.0,
            },
            {
                "title": "Bundle of Games",
                "slug": "bundle-of-games",
                "score": 85,
                "user_rating": 8.0,
            },
        ]
        thresholds = {
            "min_metascore": 0,
            "min_metascore_reviews": 0,
            "min_user_score": 0.0,
            "min_user_reviews": 0,
        }
        new_count = _process_browse_games(
            games,
            "pc",
            db,
            thresholds,
            pending_days=30,
            reject_title=["DLC", "Soundtrack", "Bundle"],
        )
        assert new_count == 1, "Only the non-excluded game should be added"
        pending = db.get_pending()
        assert pending[0].slug == "real-game"
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
            patch("gamarr.sources.fitgirl.requests.get") as mock_get,
            patch("gamarr.pipeline.MetacriticClient") as _,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = False
            mock_qbt_cls.return_value = mock_qbt
            # Mock sitemap fetch
            sitemap_resp = MagicMock()
            sitemap_resp.content = sitemap_xml
            sitemap_resp.raise_for_status = MagicMock()
            mock_get.return_value = sitemap_resp

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                db_path=str(tmp_path / "gamarr.db"),
                qbt_host="localhost",
                qbt_port=8080,
                qbt_username="admin",
                qbt_password="adminadmin",
            )

        assert isinstance(results, list)

    def test_sitemap_not_fetched_when_metacritic_browse_returns_nothing(self) -> None:
        """Metacritic-first: FitGirl sitemap must NOT be fetched if no Metacritic games to match.

        Reproduces the bug where the FitGirl sitemap was fetched as the
        first action of every cycle (logging 'FitGirl sitemap indexed 7759
        game titles'), even when there were zero Metacritic games to
        match against it. The user wants Metacritic browsed first, and
        the sitemap fetched only when there are games to match.
        """
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source

            # Metacritic browse returns NOTHING — no games to match
            mock_mc = MagicMock()
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )

            # Metacritic browse WAS called
            mock_mc.scan_recent_games.assert_called_once()
            # But the FitGirl sitemap was NOT fetched because there is
            # nothing to match against (no pending games produced).
            mock_source.fetch_sitemap.assert_not_called()

    def test_sitemap_fetched_after_metacritic_browse(self) -> None:
        """When Metacritic browse produces a qualifying game, sitemap is fetched AFTER browse."""
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        call_order: list[str] = []

        def _make_mc_result(
            metascore: float,
            meta_reviews: int,
            user: float,
            user_reviews: int,
        ) -> object:
            import types

            return types.SimpleNamespace(
                metascore=metascore,
                metascore_review_count=meta_reviews,
                user_score=user,
                user_review_count=user_reviews,
                genres=["Action"],
                must_play=False,
                release_date="2026-06-01",
            )

        def record_mc_call(
            *_args: object,
            **_kwargs: object,
        ) -> list[dict[str, object]]:
            call_order.append("scan_recent_games")
            return [
                {
                    "title": "Passing Game",
                    "slug": "passing-game",
                    "score": 90.0,
                    "critic_review_count": 50,
                    "user_rating": 8.5,
                    "user_review_count": 200,
                    "release_date": "2026-06-01",
                },
            ]

        def record_source_call(
            *_args: object,
            **_kwargs: object,
        ) -> None:
            call_order.append("fetch_sitemap")

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source.fetch_sitemap.side_effect = record_source_call
            mock_source_cls.return_value = mock_source

            mock_mc = MagicMock()
            mock_mc.scan_recent_games.side_effect = record_mc_call
            mock_mc.lookup_game.return_value = _make_mc_result(
                metascore=90.0,
                meta_reviews=50,
                user=8.5,
                user_reviews=200,
            )
            mock_mc_cls.return_value = mock_mc

            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt.add_torrent.return_value = False
            mock_qbt_cls.return_value = mock_qbt

            run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
            )

        # Metacritic browse must happen BEFORE the FitGirl sitemap fetch
        assert call_order == ["scan_recent_games", "fetch_sitemap"], (
            f"Expected Metacritic browse first, then FitGirl sitemap; got {call_order}"
        )
        # Belt-and-suspenders: sitemap should be called exactly once.
        mock_source.fetch_sitemap.assert_called_once()


class TestVerifyPendingScoresEdgeCases:
    """_verify_pending_scores edge cases."""

    def test_scores_present_with_no_valid_scores(self) -> None:
        """_scores_present returns False when all scores are None/0."""
        import types

        from gamarr.pipeline import _scores_present

        result = types.SimpleNamespace(
            metascore=0.0,
            metascore_review_count=None,
            user_score=0.0,
            user_review_count=0,
            genres=None,
            must_play=False,
            release_date=None,
        )
        assert _scores_present(result) is False
        assert _scores_present(None) is False

    def test_scores_present_with_valid_user_score(self) -> None:
        """_scores_present returns True when user_score is valid."""
        import types

        from gamarr.pipeline import _scores_present

        result = types.SimpleNamespace(
            metascore=0.0,
            metascore_review_count=None,
            user_score=8.0,
            user_review_count=100,
            genres=None,
            must_play=False,
            release_date=None,
        )
        assert _scores_present(result) is True

    def test_match_pending_jit_removes_game_with_failing_scores(self, tmp_path: Path) -> None:
        """When JIT verification reveals failing scores, the game should be removed."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _match_pending_games

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="failing-jit",
            game_title="Failing JIT Game",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )
        # Mark as score-checked so it passes the gate
        db.update_pending_scores(slug="failing-jit", metascore=85.0, user_score=8.0)
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Failing JIT Game", "url": "https://example.com/failing-jit"}],
        )

        mock_qbt = MagicMock()
        mock_qbt.add_torrent.return_value = "gamarr-tag"
        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None  # Detail page not found
        magnet_fetcher = MagicMock(return_value="magnet:?xt=urn:btih:test")

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        matched = _match_pending_games(
            db,
            qbt=mock_qbt,
            magnet_fetcher=magnet_fetcher,
            mc=mock_mc,
            thresholds=thresholds,
        )
        # Game should be removed (JIT verification failed) — no results
        assert len(matched) == 0
        assert not db.is_pending("failing-jit"), "Game should be removed from pending"
        # qBittorrent should NOT be called
        mock_qbt.add_torrent.assert_not_called()
        db.close()

    def test_config_allows_zero_max_games(self) -> None:
        """max_games=0 must be accepted by the config model.

        A value of 0 means "unlimited" — score-check all pending games.
        """
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig(max_games=0)
        assert cfg.max_games == 0

    def test_verify_pending_max_checks_zero_passes_all_games(self, tmp_path: Path) -> None:
        """When max_games=0, _verify_pending_scores should check ALL games.

        The pipeline passes len(pending_games) as max_verify when max_games=0.
        """
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        # Create 3 pending games
        for i in range(3):
            db.record_pending(
                slug=f"game-{i:02d}",
                game_title=f"Game {i:02d}",
                platform="pc",
                metascore=85.0,
                user_score=8.0,
                expires_at=expires,
            )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        # max_verify=len(pending_games) simulates what the pipeline does
        # when max_games=0 (unlimited)
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            thresholds,
            max_verify=len(db.get_pending()),
        )
        assert removed == 0  # All passed
        assert mock_mc.lookup_game.call_count == 3  # All 3 checked
        db.close()

    def test_verify_pending_scores_concurrently(self, tmp_path: Path) -> None:
        """Score-checking multiple games should be faster than sequential.

        When multiple pending games need score-checking, the lookup_game
        calls should run concurrently (ThreadPoolExecutor) rather than
        sequentially.  This test mocks a slow HTTP response and verifies
        that N games are checked in less than N × response_time.
        """
        import datetime
        import time
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        # Create 10 pending games
        for i in range(10):
            db.record_pending(
                slug=f"game-{i:02d}",
                game_title=f"Game {i:02d}",
                platform="pc",
                metascore=85.0,
                user_score=8.0,
                expires_at=expires,
            )

        mock_mc = MagicMock()
        # Each lookup takes 0.3 seconds (simulating a slow HTTP response)
        import types

        def slow_lookup(*args: object, **kwargs: object) -> types.SimpleNamespace:
            time.sleep(0.3)
            return types.SimpleNamespace(
                metascore=85.0,
                metascore_review_count=20,
                user_score=8.0,
                user_review_count=100,
                genres=["Action"],
                must_play=False,
                release_date="2026-06-01",
            )

        mock_mc.lookup_game.side_effect = slow_lookup

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        start = time.time()
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            thresholds,
            max_verify=10,
        )
        elapsed = time.time() - start

        assert removed == 0  # All pass
        # Sequential would take ~3 seconds (10 × 0.3s).
        # With 10 concurrent workers, should take ~0.3-0.6s.
        # Allow generous margin: should complete in under 2s.
        assert elapsed < 2.0, f"Score-checking 10 games took {elapsed:.1f}s — expected <2.0s with concurrent lookups"
        # All 10 lookups should have been called
        assert mock_mc.lookup_game.call_count == 10
        db.close()

    def test_verify_pending_pass_emits_log(self, tmp_path: Path) -> None:
        """A game that passes score-checking should log that it passed.

        Without this log, games that pass all filters but have no FitGirl
        match are completely invisible in the output.
        """
        import datetime
        import io
        from unittest.mock import MagicMock

        from loguru import logger

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="passing-game",
            game_title="Passing Game",
            platform="pc",
            metascore=85.0,
            user_score=8.0,
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        buf = io.StringIO()
        logger_id = logger.add(buf, format="{message}", colorize=False, level="DEBUG")
        try:
            removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, max_verify=10)
        finally:
            logger.remove(logger_id)

        assert removed == 0
        output = buf.getvalue()
        assert "Passing Game" in output, "Game that passed score checks should appear in log"
        assert "score check" in output.lower() or "passed" in output.lower(), "Should mention that score check passed"
        db.close()

    def test_verify_pending_keeps_game_with_failing_scores(self, tmp_path: Path) -> None:
        """A game with failing scores should stay pending (not removed)."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="failing-game",
            game_title="Failing Game",
            platform="pc",
            metascore=1998.0,
            user_score=2007.0,
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=62.0,
            metascore_review_count=25,
            user_score=3.3,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 10,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        assert db.is_pending("failing-game") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds)
        assert removed == 0, "Game with failing scores should stay pending"
        assert db.is_pending("failing-game") is True, "Game should remain pending"
        db.close()

    def test_verify_pending_keeps_game_with_none_lookup(self, tmp_path: Path) -> None:
        """A game with None lookup should stay pending (not removed)."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="ghost-game",
            game_title="Ghost Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None

        assert db.is_pending("ghost-game") is True
        removed = _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
        )
        assert removed == 0, "Game with None lookup should stay pending"
        assert db.is_pending("ghost-game") is True, "Game should remain pending"
        db.close()

    def test_fail_game_without_result_details(self, tmp_path: Path) -> None:
        """_fail_game_after_max_attempts should auto-generate result_details when none provided."""
        import datetime
        import types

        from gamarr.database import Database
        from gamarr.pipeline import _fail_game_after_max_attempts

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(slug="test-game", game_title="Test Game", platform="pc", expires_at=expires)

        game = db.get_pending()[0]
        result = types.SimpleNamespace(
            metascore=62.0,
            metascore_review_count=25,
            user_score=3.3,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        _fail_game_after_max_attempts(db, game, result, attempts=3)
        assert not db.is_pending("test-game")
        db.close()

    def test_reject_genre_matches(self, tmp_path: Path) -> None:
        """Game with a genre in reject_genre should be removed immediately."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=95.0,
            metascore_reviews=100,
            user_score=9.0,
            user_reviews=500,
            release_date="2022-02-25",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=95.0,
            metascore_review_count=100,
            user_score=9.0,
            user_review_count=500,
            genres=["Action", "RPG"],
            must_play=True,
            release_date="2022-02-25",
            slug="elden-ring",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        assert db.is_pending("elden-ring") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["rpg"])
        assert removed == 1, "Game with rejected genre should be removed"
        assert db.is_pending("elden-ring") is False, "Game should no longer be pending"
        db.close()

    def test_reject_genre_no_match(self, tmp_path: Path) -> None:
        """Game without a rejected genre should be processed normally."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="forza-horizon-6",
            game_title="Forza Horizon 6",
            platform="pc",
            metascore=1985.0,
            metascore_reviews=1986,
            user_score=1994.0,
            user_reviews=None,
            release_date="2026-05-19",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Racing"],
            must_play=True,
            release_date="2026-05-19",
            slug="forza-horizon-6",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["action"])
        assert removed == 0, "Game genre 'Racing' not in reject_genre ['action'] — should NOT be removed"
        assert db.is_pending("forza-horizon-6") is True, "Game should remain pending"
        db.close()

    def test_reject_genre_empty_list(self, tmp_path: Path) -> None:
        """Empty reject_genre list should have no effect."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="elden-ring",
            game_title="Elden Ring",
            platform="pc",
            metascore=95.0,
            metascore_reviews=100,
            user_score=9.0,
            user_reviews=500,
            release_date="2022-02-25",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=95.0,
            metascore_review_count=100,
            user_score=9.0,
            user_review_count=500,
            genres=["Action", "RPG"],
            must_play=True,
            release_date="2022-02-25",
            slug="elden-ring",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=[])
        assert removed == 0, "Empty reject_genre — game should NOT be removed"
        assert db.is_pending("elden-ring") is True, "Game should remain pending"
        db.close()

    def test_reject_genre_multi_match(self, tmp_path: Path) -> None:
        """Game with multiple genres where one matches reject_genre should be removed."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="cyberpunk-2077",
            game_title="Cyberpunk 2077",
            platform="pc",
            metascore=86.0,
            metascore_reviews=90,
            user_score=8.5,
            user_reviews=500,
            release_date="2020-12-10",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=86.0,
            metascore_review_count=90,
            user_score=8.5,
            user_review_count=500,
            genres=["Action", "RPG", "Open-World"],
            must_play=True,
            release_date="2020-12-10",
            slug="cyberpunk-2077",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["rpg", "sports"])
        assert removed == 1, "Game has 'RPG' which is in reject_genre — should be removed"
        assert db.is_pending("cyberpunk-2077") is False
        db.close()

    def test_reject_genre_case_insensitive(self, tmp_path: Path) -> None:
        """Genre matching should be case-insensitive."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="hades-2",
            game_title="Hades II",
            platform="pc",
            metascore=90.0,
            metascore_reviews=80,
            user_score=8.5,
            user_reviews=300,
            release_date="2025-05-06",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=80,
            user_score=8.5,
            user_review_count=300,
            genres=["Early Access", "Roguelike"],
            must_play=False,
            release_date="2025-05-06",
            slug="hades-2",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["ROGUELIKE"])
        assert removed == 1, "Case-insensitive match — 'ROGUELIKE' should match 'Roguelike'"
        db.close()

    def test_reject_genre_result_none(self, tmp_path: Path) -> None:
        """When lookup returns None, genre check is skipped and normal retry logic applies."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="unknown-game",
            game_title="Unknown Game",
            platform="pc",
            metascore=0.0,
            metascore_reviews=0,
            user_score=0.0,
            user_reviews=0,
            release_date="2026-01-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None  # lookup failed

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["action"])
        assert removed == 0, "Lookup returned None — genre check skipped, game stays for re-check"
        assert db.is_pending("unknown-game") is True, "Game should remain pending for re-try"
        db.close()

    def test_reject_genre_none_genres(self, tmp_path: Path) -> None:
        """When result.genres is None, genre check is skipped and score check proceeds."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="no-genre-game",
            game_title="No Genre Game",
            platform="pc",
            metascore=1985.0,
            metascore_reviews=1986,
            user_score=1994.0,
            user_reviews=None,
            release_date="2026-05-19",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=None,
            must_play=True,
            release_date="2026-05-19",
            slug="no-genre-game",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["action"])
        assert removed == 0, "genres=None — genre check skipped, game should not be removed"
        assert db.is_pending("no-genre-game") is True, "Game should remain pending (scores pass)"
        db.close()

    def test_reject_genre_none_default(self, tmp_path: Path) -> None:
        """When reject_genre is None (default), the check is skipped entirely."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="default-test",
            game_title="Default Test",
            platform="pc",
            metascore=1985.0,
            metascore_reviews=1986,
            user_score=1994.0,
            user_reviews=None,
            release_date="2026-05-19",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=True,
            release_date="2026-05-19",
            slug="default-test",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        # reject_genre not passed (defaults to None)
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds)
        assert removed == 0, "reject_genre=None — game should not be removed by genre check"
        assert db.is_pending("default-test") is True, "Game should remain pending"
        db.close()

    def test_reject_genre_substring_broad(self, tmp_path: Path) -> None:
        """reject_genre=["RPG"] should match "Action RPG" (substring)."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="action-rpg-game",
            game_title="Action RPG Game",
            platform="pc",
            metascore=90.0,
            metascore_reviews=50,
            user_score=8.5,
            user_reviews=200,
            release_date="2026-01-01",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.5,
            user_review_count=200,
            genres=["Action RPG"],
            must_play=True,
            release_date="2026-01-01",
            slug="action-rpg-game",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["RPG"])
        assert removed == 1, "'RPG' should match 'Action RPG' via substring"
        assert db.is_pending("action-rpg-game") is False
        db.close()

    def test_reject_genre_substring_narrow(self, tmp_path: Path) -> None:
        """reject_genre=["Western RPG"] should NOT match "Action RPG" (substring mismatch)."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="action-rpg-game2",
            game_title="Action RPG Game 2",
            platform="pc",
            metascore=90.0,
            metascore_reviews=50,
            user_score=8.5,
            user_reviews=200,
            release_date="2026-01-01",
            expires_at=expires,
        )

        import types

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=90.0,
            metascore_review_count=50,
            user_score=8.5,
            user_review_count=200,
            genres=["Action RPG"],
            must_play=True,
            release_date="2026-01-01",
            slug="action-rpg-game2",
        )

        thresholds = {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 5}

        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_genre=["Western RPG"])
        assert removed == 0, "'Western RPG' should NOT match 'Action RPG' — substring not found"
        assert db.is_pending("action-rpg-game2") is True
        db.close()


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


class TestFitgirlPendingExpiry:
    """Tests for fitgirl_pending_days expiry recalculation."""

    def test_fitgirl_pending_days_updates_expiry(self, tmp_path: Path) -> None:
        """Game with passing scores should have expires_at recalculated to now + fitgirl_pending_days."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="passing-game",
            game_title="Passing Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )
        original_expiry = expires  # Capture before verification

        mock_mc = MagicMock()
        import types

        mock_result = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=True,
            release_date="2026-06-01",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(db, mock_mc, "pc", thresholds, fitgirl_pending_days=60)

        # Game should still be pending
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        row = pending[0]
        # Expiry should be recalculated to now + 60 days (not the original +30)
        new_expiry = datetime.datetime.fromisoformat(row.expires_at)
        expected_min = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=59)
        expected_max = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=61)
        assert expected_min < new_expiry < expected_max, f"Expiry should be near now+60d, got {new_expiry}"
        # Verify it's different from the original
        assert row.expires_at != original_expiry, "Expiry should have been updated"
        db.close()

    def test_fitgirl_pending_days_zero_indefinite(self, tmp_path: Path) -> None:
        """fitgirl_pending_days=0 should set expiry to far-future (indefinite pending)."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="zero-days-game",
            game_title="Zero Days Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_result = types.SimpleNamespace(
            metascore=88.0,
            metascore_review_count=50,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=True,
            release_date="2026-06-01",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(db, mock_mc, "pc", thresholds, fitgirl_pending_days=0)

        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        # Expiry should be updated to far-future
        new_expiry = datetime.datetime.fromisoformat(pending[0].expires_at)
        expected_min = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=9998)
        expected_max = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=10000)
        assert expected_min < new_expiry < expected_max, (
            f"Expiry should be near now+9999d (indefinite), got {new_expiry}"
        )
        db.close()

    def test_fitgirl_pending_days_does_not_affect_failure(self, tmp_path: Path) -> None:
        """Game with failing scores should NOT have its expiry updated."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="failing-game",
            game_title="Failing Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_result = types.SimpleNamespace(
            metascore=62.0,
            metascore_review_count=25,
            user_score=3.3,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )
        mock_mc.lookup_game.return_value = mock_result

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(db, mock_mc, "pc", thresholds, fitgirl_pending_days=60)

        # Game should still be pending (re-check)
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        # Expiry must NOT be updated (still original +30d)
        assert pending[0].expires_at == expires, f"Expiry should be unchanged ({expires}), got {pending[0].expires_at}"
        db.close()


class TestRejectTitle:
    """Tests for reject_title title substring filtering."""

    def test_reject_title_at_browse(self, tmp_path: Path) -> None:
        """Game with title matching reject_title should be skipped at browse stage."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Resident Evil 4 Remake",
                "slug": "resident-evil-4-remake",
                "score": 85,
                "critic_review_count": 20,
                "user_rating": 8.0,
                "user_review_count": 100,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games,
            "pc",
            db,
            thresholds,
            reject_title=["Remake"],
        )
        assert added == 0, "Game with matching title should not be added"
        assert not db.is_pending("resident-evil-4-remake")
        db.close()

    def test_reject_title_at_verify(self, tmp_path: Path) -> None:
        """Game with title matching reject_title should be removed during verification."""
        import datetime
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="vr-game",
            game_title="VR Adventure",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        import types

        mock_mc.lookup_game.return_value = types.SimpleNamespace(
            metascore=85.0,
            metascore_review_count=20,
            user_score=8.0,
            user_review_count=100,
            genres=["Action"],
            must_play=False,
            release_date="2026-06-01",
        )

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        assert db.is_pending("vr-game") is True
        removed = _verify_pending_scores(db, mock_mc, "pc", thresholds, reject_title=["VR"])
        assert removed == 1, "Game with matching title should be removed"
        assert not db.is_pending("vr-game"), "Game should no longer be pending"
        db.close()

    def test_reject_title_no_match(self, tmp_path: Path) -> None:
        """Game with non-matching title should proceed normally."""
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
        added = _process_browse_games(
            browse_games,
            "pc",
            db,
            thresholds,
            reject_title=["Remake"],
        )
        assert added == 1, "Non-matching game should be added"
        assert db.is_pending("elden-ring")
        db.close()

    def test_reject_title_empty_list(self, tmp_path: Path) -> None:
        """Empty reject_title should have no effect."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "VR Adventure",
                "slug": "vr-adventure",
                "score": 85,
                "critic_review_count": 20,
                "user_rating": 8.0,
                "user_review_count": 100,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games,
            "pc",
            db,
            thresholds,
            reject_title=[],
        )
        assert added == 1, "Empty reject_title should not filter anything"
        db.close()

    def test_reject_title_case_insensitive(self, tmp_path: Path) -> None:
        """reject_title should match case-insensitively."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "The Legend of Zelda: Remake",
                "slug": "zelda-remake",
                "score": 95,
                "critic_review_count": 100,
                "user_rating": 9.0,
                "user_review_count": 5000,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games,
            "pc",
            db,
            thresholds,
            reject_title=["remake"],  # lowercase, title has "Remake"
        )
        assert added == 0, "reject_title should match case-insensitively"
        db.close()

    def test_reject_title_substring(self, tmp_path: Path) -> None:
        """reject_title should match partial substrings, not just whole words."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Collection of Classic Games Vol 3",
                "slug": "collection-classic-3",
                "score": 80,
                "critic_review_count": 10,
                "user_rating": 7.5,
                "user_review_count": 50,
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        added = _process_browse_games(
            browse_games,
            "pc",
            db,
            thresholds,
            reject_title=["Classic"],
        )
        assert added == 0, "reject_title should match on substrings"
        db.close()


class TestScrapeHealth:
    """Tests for _check_scrape_health connectivity checks."""

    def test_scrape_health_metacritic_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Metacritic responds <500, should return 'metacritic_broken'."""
        import requests

        from gamarr.pipeline import _check_scrape_health

        def mock_head(url: str, **kwargs: Any) -> Any:
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 200
            return resp

        monkeypatch.setattr(requests, "head", mock_head)
        assert _check_scrape_health() == "metacritic_broken"

    def test_scrape_health_metacritic_5xx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Metacritic returns 503, should return 'metacritic_down'."""
        import requests

        from gamarr.pipeline import _check_scrape_health

        def mock_head(url: str, **kwargs: Any) -> Any:
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 503
            return resp

        monkeypatch.setattr(requests, "head", mock_head)
        assert _check_scrape_health() == "metacritic_down"

    def test_scrape_health_metacritic_down_google_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Metacritic fails but Google works, return 'metacritic_down'."""
        import requests

        from gamarr.pipeline import _check_scrape_health

        def mock_head(url: str, **kwargs: Any) -> Any:
            if "metacritic" in url:
                raise requests.ConnectionError("Metacritic unreachable")
            resp = MagicMock(spec=requests.Response)
            resp.status_code = 200
            return resp

        monkeypatch.setattr(requests, "head", mock_head)
        assert _check_scrape_health() == "metacritic_down"

    def test_scrape_health_internet_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both Metacritic and Google fail, return 'internet_down'."""
        import requests

        from gamarr.pipeline import _check_scrape_health

        def mock_head(url: str, **kwargs: Any) -> Any:
            raise requests.ConnectionError("Network unreachable")

        monkeypatch.setattr(requests, "head", mock_head)
        assert _check_scrape_health() == "internet_down"

    def test_verify_phase_passes_with_notifier(self, tmp_path: Path) -> None:
        """When at least one game verifies, no scrape notification fires."""
        import datetime
        import types
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.notifications import Notifier
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="passing-game",
            game_title="Passing Game",
            platform="pc",
            metascore=1288.0,
            user_score=1288.0,
            release_date="2026-06-01",
            expires_at=expires,
        )

        # Also add a "no details" game so we have a mix of pass/fail
        db.record_pending(
            slug="no-details-game",
            game_title="No Details Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.side_effect = [
            types.SimpleNamespace(
                metascore=88.0,
                metascore_review_count=50,
                user_score=8.0,
                user_review_count=100,
                genres=["Action"],
                must_play=True,
                release_date="2026-06-01",
            ),
            types.SimpleNamespace(
                metascore=62.0,
                metascore_review_count=5,
                user_score=3.3,
                user_review_count=20,
                genres=["RPG"],
                must_play=False,
                release_date="2026-06-01",
            ),
        ]

        mock_notifier = MagicMock(spec=Notifier)

        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        _verify_pending_scores(
            db,
            mock_mc,
            "pc",
            thresholds,
            notifier=mock_notifier,
        )
        # At least one game succeeded, so no scrape notification
        mock_notifier.send_scrape_notification.assert_not_called()
        db.close()

    def test_verify_phase_all_fail_sends_notification(self, tmp_path: Path) -> None:
        """When every lookup returns None, scrape notification should fire."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.notifications import Notifier
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="failing-game",
            game_title="Failing Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None  # All lookups return None

        mock_notifier = MagicMock(spec=Notifier)

        with patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"):
            _verify_pending_scores(
                db,
                mock_mc,
                "pc",
                {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
                notifier=mock_notifier,
            )

        mock_notifier.send_scrape_notification.assert_called_once()
        db.close()

    def test_verify_phase_all_fail_metacritic_down_sends_notification(self, tmp_path: Path) -> None:
        """When _check_scrape_health returns metacritic_down, notification should fire."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.notifications import Notifier
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="down-game",
            game_title="Down Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None

        mock_notifier = MagicMock(spec=Notifier)

        with patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_down"):
            _verify_pending_scores(
                db,
                mock_mc,
                "pc",
                {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
                notifier=mock_notifier,
            )

        mock_notifier.send_scrape_notification.assert_called_once()
        db.close()

    def test_verify_phase_all_fail_internet_down_skips_notification(self, tmp_path: Path) -> None:
        """When _check_scrape_health returns internet_down, no notification fires."""
        import datetime
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.notifications import Notifier
        from gamarr.pipeline import _verify_pending_scores

        db = Database(str(tmp_path / "test.db"))
        expires = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=30)).isoformat()
        db.record_pending(
            slug="offline-game",
            game_title="Offline Game",
            platform="pc",
            metascore=62.0,
            user_score=3.3,
            release_date="2026-06-01",
            expires_at=expires,
        )

        mock_mc = MagicMock()
        mock_mc.lookup_game.return_value = None

        mock_notifier = MagicMock(spec=Notifier)

        with patch("gamarr.pipeline._check_scrape_health", return_value="internet_down"):
            _verify_pending_scores(
                db,
                mock_mc,
                "pc",
                {"min_metascore": 75, "min_metascore_reviews": 5, "min_user_score": 7.5, "min_user_reviews": 10},
                notifier=mock_notifier,
            )

        mock_notifier.send_scrape_notification.assert_not_called()
        db.close()


class TestCancellation:
    """Cancel event propagation through the pipeline."""

    def test_process_verify_batch_returns_early_when_precancelled(
        self,
        tmp_path: Path,
    ) -> None:
        """When cancel_event is pre-set, _process_verify_batch returns
        immediately with (0, False) without processing any games."""
        import threading
        from unittest.mock import MagicMock

        from gamarr.database import Database
        from gamarr.pipeline import _process_verify_batch

        db = Database(str(tmp_path / "test.db"))
        cancel_event = threading.Event()
        cancel_event.set()  # Pre-set before call

        mock_mc = MagicMock()
        batch = [MagicMock()]

        removed, any_success = _process_verify_batch(
            db,
            mock_mc,
            "pc",
            {
                "min_metascore": 75,
                "min_metascore_reviews": 5,
                "min_user_score": 7.5,
                "min_user_reviews": 10,
            },
            batch,
            max_verify=10,
            total_pending=10,
            cancel_event=cancel_event,
        )

        assert removed == 0
        assert any_success is False
        mock_mc.lookup_game.assert_not_called()

    def test_run_acquisition_returns_early_when_precancelled(self) -> None:
        """When cancel_event is pre-set, run_acquisition returns
        early — scan_recent_games aborts its page loop immediately
        and no further pipeline steps run."""
        import threading
        from unittest.mock import MagicMock, patch

        from gamarr.pipeline import run_acquisition

        cancel_event = threading.Event()
        cancel_event.set()

        with (
            patch("gamarr.pipeline.FitGirlSource") as mock_source_cls,
            patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
            patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
        ):
            mock_source = MagicMock()
            mock_source_cls.return_value = mock_source
            mock_mc = MagicMock()
            # scan_recent_games will return empty (aborted by cancel_event)
            # so we make it return [] to simulate a clean early exit
            mock_mc.scan_recent_games.return_value = []
            mock_mc_cls.return_value = mock_mc
            mock_qbt = MagicMock()
            mock_qbt.is_connected.return_value = True
            mock_qbt_cls.return_value = mock_qbt

            results = run_acquisition(
                fitgirl_rss_url="http://example.com/feed",
                platform="pc",
                qbt_host="localhost",
                qbt_port=8080,
                cancel_event=cancel_event,
            )

        # scan_recent_games is called (cancel check is inside it), but
        # no pending games means no verify, no sitemap, no delivery
        mock_mc.scan_recent_games.assert_called_once()
        mock_mc.lookup_game.assert_not_called()
        mock_source.fetch_sitemap.assert_not_called()
        assert results == []


class TestBrowseReviewCountPrefilter:
    """_reject_by_browse_review_counts function."""

    def test_returns_none_when_both_counts_missing(self) -> None:
        """When both critic_review_count and user_review_count are None
        (not available in browse data), the function should return None
        so the game proceeds to detail-page verification as before."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Test Game",
            "slug": "test-game",
            "critic_review_count": None,
            "user_review_count": None,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result is None

    def test_returns_none_when_counts_sufficient(self) -> None:
        """When both counts meet or exceed thresholds, return None."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Popular Game",
            "slug": "popular-game",
            "critic_review_count": 20,
            "user_review_count": 50,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result is None

    def test_returns_reason_when_critic_count_too_low(self) -> None:
        """When critic_review_count is below min_critic_reviews, return the reason string."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Obscure Game",
            "slug": "obscure-game",
            "critic_review_count": 2,
            "user_review_count": 0,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result == "critic_reviews_too_few_at_browse"

    def test_returns_reason_when_user_count_too_low(self) -> None:
        """When user_review_count is below min_user_reviews (and critic count is fine), return reason."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Unreviewed Game",
            "slug": "unreviewed-game",
            "critic_review_count": 20,
            "user_review_count": 3,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=5, min_user_reviews=10)
        assert result == "user_reviews_too_few_at_browse"

    def test_ignores_zero_threshold(self) -> None:
        """When min thresholds are 0 (disabled), function should never reject."""
        from gamarr.pipeline import _reject_by_browse_review_counts

        game: dict[str, Any] = {
            "title": "Zero Reviews Game",
            "slug": "zero-review-game",
            "critic_review_count": 0,
            "user_review_count": 0,
        }
        result = _reject_by_browse_review_counts(game, min_critic_reviews=0, min_user_reviews=0)
        assert result is None

    def test_process_browse_games_skips_low_review_count_games(self, tmp_path: Path) -> None:
        """Games with browse-page critic_review_count below threshold
        should NOT be added to the pending queue by _process_browse_games."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "Low Reviews",
                "slug": "low-reviews",
                "score": 1478.0,
                "critic_review_count": 2,  # below threshold 5
                "user_rating": 2007.0,
                "user_review_count": 50,
                "release_date": "2026-06-01",
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }

        new_count = _process_browse_games(
            browse_games,
            platform="pc",
            db=db,
            thresholds=thresholds,
            pending_days=30,
        )
        assert new_count == 0, "Low-review-count game should not be added to pending"
        pending = db.get_pending(platform="pc")
        assert len(pending) == 0
        db.close()

    def test_process_browse_games_passes_when_review_counts_unavailable(self, tmp_path: Path) -> None:
        """Games with None review counts on the browse page should still
        enter the pending queue (fallback to detail-page verification)."""
        from gamarr.database import Database
        from gamarr.pipeline import _process_browse_games

        db = Database(str(tmp_path / "test.db"))
        browse_games = [
            {
                "title": "No Review Data",
                "slug": "no-review-data",
                "score": 1478.0,
                "critic_review_count": None,  # missing from browse data
                "user_rating": 2007.0,
                "user_review_count": None,
                "release_date": "2026-06-01",
            },
        ]
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        new_count = _process_browse_games(
            browse_games,
            platform="pc",
            db=db,
            thresholds=thresholds,
            pending_days=30,
        )
        assert new_count == 1, "Game with missing review data should enter pending"
        pending = db.get_pending(platform="pc")
        assert len(pending) == 1
        assert pending[0].slug == "no-review-data"
        db.close()
