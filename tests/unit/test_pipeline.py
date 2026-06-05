"""Tests for gamarr acquisition pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gamarr.pipeline import AcquisitionConfig, run_acquisition


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

        cfg = type("Cfg", (), {
            "min_metascore": 75, "min_metascore_reviews": 5,
            "min_user_score": 7.5, "min_user_reviews": 10,
        })()
        mc_result = types.SimpleNamespace(metascore=None, user_score=None)
        assert _evaluate_scores(mc_result, cfg) == "Failed"

    def test_high_metascore_low_reviews_fails(self) -> None:
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type("Cfg", (), {
            "min_metascore": 75, "min_metascore_reviews": 5,
            "min_user_score": 7.5, "min_user_reviews": 10,
        })()
        mc_result = types.SimpleNamespace(
            metascore=90.0, metascore_review_count=2,
            user_score=8.0, user_review_count=100,
        )
        assert _evaluate_scores(mc_result, cfg) == "Failed"

    def test_good_metascore_low_user_reviews_fails(self) -> None:
        import types

        from gamarr.pipeline import _evaluate_scores

        cfg = type("Cfg", (), {
            "min_metascore": 75, "min_metascore_reviews": 5,
            "min_user_score": 7.5, "min_user_reviews": 10,
        })()
        mc_result = types.SimpleNamespace(
            metascore=90.0, metascore_review_count=50,
            user_score=8.0, user_review_count=3,
        )
        assert _evaluate_scores(mc_result, cfg) == "Failed"


class TestPipelineEdgeCases:
    """Pipeline edge cases with mocked dependencies."""

    def test_qbt_add_failure(self) -> None:
        import types

        from gamarr.models import GameEntry

        entry = GameEntry(
            title="QBT Fail", source_title="QBT Fail [Repack]",
            source="fitgirl", platform="pc",
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
                title="QBT Fail", passed=True, metascore=85.0,
                user_score=8.0, metascore_review_count=50, user_review_count=200,
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
                platform="pc", qbt_host="localhost", qbt_port=8080,
                min_metascore=75, min_metascore_reviews=5,
                min_user_score=7.5, min_user_reviews=10,
            )
            assert len(results) == 1
            assert results[0]["result"] == "Error"
