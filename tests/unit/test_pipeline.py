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


class TestEvaluateScores:
    """_evaluate_scores function edge cases."""

    def test_browse_game_passes_thresholds_without_user_review_count(self) -> None:
        """Browse-page games without user_review_count should pass if user_score meets threshold.

        The Metacritic browse page Nuxt data only includes ``userScore.score``
        for each game — it does NOT include a user review count.  The
        threshold check must therefore tolerate a missing
        ``user_review_count`` rather than treating it as 0 (which would
        silently drop every browse-page game).
        """
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "Some Game",
            "slug": "some-game",
            "score": 85.0,
            "critic_review_count": 20,
            "user_rating": 8.5,
            "user_review_count": None,  # browse page doesn't provide this
            "release_date": "2026-06-01",
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

    def test_browse_game_passes_thresholds_without_critic_review_count(self) -> None:
        """Browse-page games without critic_review_count should pass if score is high."""
        from gamarr.pipeline import _game_passes_thresholds

        game = {
            "title": "Some Game",
            "slug": "some-game",
            "score": 85.0,
            "critic_review_count": None,  # browse page doesn't provide this
            "user_rating": 8.5,
            "user_review_count": None,
            "release_date": "2026-06-01",
        }
        thresholds = {
            "min_metascore": 75,
            "min_metascore_reviews": 5,
            "min_user_score": 7.5,
            "min_user_reviews": 10,
        }
        assert _game_passes_thresholds(game, thresholds) is True

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

    def test_verify_pending_removes_game_with_failing_real_scores(self, tmp_path: Path) -> None:
        """A game added with wrong browse scores should be removed if real scores fail thresholds."""
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
        # Real scores (62, 3.3) fail thresholds — game should be removed
        assert removed == 1, "Game with failing real scores should be removed"
        assert db.is_pending("thick-as-thieves") is False
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

    def test_verify_pending_removes_game_when_lookup_returns_none(self, tmp_path: Path) -> None:
        """When lookup_game returns None, the pending game should be removed."""
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
        assert removed == 1, "Game with None lookup should be removed"
        assert db.is_pending("not-found-game") is False
        # lookup_game was called with direct_only=True (no browse fallback)
        assert mock_mc.lookup_game.call_count == 1
        _call_args, call_kwargs = mock_mc.lookup_game.call_args
        assert call_kwargs.get("direct_only") is True, "lookup_game must use direct_only=True to avoid browse fallback"
        db.close()

    def test_verify_pending_removes_game_with_all_zero_real_scores(self, tmp_path: Path) -> None:
        """When detail page returns all None/0 scores, the game should be removed (unreviewed)."""
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
        assert removed == 1, "Game with no real scores should be removed"
        assert db.is_pending("unreviewed-game") is False
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
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
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
            user_score=8.5,
            magnet_url="magnet:?xt=urn:btih:test",
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
        db.rebuild_source_titles(
            "fitgirl",
            [{"title": "Elden Ring", "url": "https://fitgirl-repacks.site/elden-ring/"}],
        )

        mock_qbt = MagicMock()
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
                mc_cache_path=str(tmp_path / "mc-cache.db"),
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
