"""Tests for gamarr FitGirl RSS source."""

from __future__ import annotations

from gamarr.sources.fitgirl import FitGirlSource, _clean_title


class TestTitleCleaning:
    """RSS title cleansing logic."""

    def test_clean_simple_title(self) -> None:
        assert _clean_title("Hades II [Repack]") == "Hades II"

    def test_clean_title_with_version(self) -> None:
        result = _clean_title("Elden Ring (v1.12 + DLCs, MULTi13) [Repack]")
        assert result == "Elden Ring"

    def test_clean_title_with_complex_version(self) -> None:
        result = _clean_title("Baldur's Gate 3 (v4.1.1.4.64194 Hotfix 28, MULTi17) [Repack]")
        assert result == "Baldur's Gate 3"

    def test_clean_title_with_multi_only(self) -> None:
        result = _clean_title("Some Game (MULTi5) [Repack]")
        assert result == "Some Game"

    def test_clean_title_no_repack(self) -> None:
        result = _clean_title("Game Name (v1.0) [Repack]")
        assert result == "Game Name"

    def test_clean_title_repack_no_version(self) -> None:
        result = _clean_title("Cyberpunk 2077 [Repack]")
        assert result == "Cyberpunk 2077"

    def test_clean_title_preserves_ampersand(self) -> None:
        result = _clean_title("Crash & Spyro [Repack]")
        assert result == "Crash & Spyro"

    def test_clean_title_apostrophe(self) -> None:
        result = _clean_title("Assassin's Creed [Repack]")
        assert result == "Assassin's Creed"

    def test_clean_title_strips_selective_download(self) -> None:
        result = _clean_title("Game Name (Selective Download) [Repack]")
        assert result == "Game Name"


class TestFitGirlSource:
    """FitGirlSource construction and protocol conformance."""

    def test_implements_base_source(self) -> None:
        from gamarr.sources import BaseSource

        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        assert isinstance(source, BaseSource)

    def test_source_name(self) -> None:
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        assert source.source_name == "fitgirl"

    def test_platform(self) -> None:
        source = FitGirlSource("http://example.com/feed.xml", platform="pc", db_path=":memory:")
        assert source.platform == "pc"

    def test_fetch_new_returns_list(self) -> None:
        source = FitGirlSource("http://example.com/feed.xml", db_path=":memory:")
        entries = source.fetch_new()
        assert isinstance(entries, list)
