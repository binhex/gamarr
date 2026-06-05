"""Tests for gamarr library scanning."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gamarr.library import LibraryMatch, LibraryScanner, _normalise_name

if TYPE_CHECKING:
    from pathlib import Path


class TestNormaliseName:
    """Game name normalization for cross-comparison."""

    def test_normalise_simple(self) -> None:
        assert _normalise_name("Elden Ring") == "elden ring"

    def test_normalise_with_underscores(self) -> None:
        assert _normalise_name("elden_ring_[dodi]") == "elden ring"

    def test_normalise_file_extension(self) -> None:
        assert _normalise_name("cyberpunk-2077.iso") == "cyberpunk 2077"

    def test_normalise_zip_file(self) -> None:
        assert _normalise_name("hades_ii.zip") == "hades ii"

    def test_normalise_strips_year_in_parens(self) -> None:
        assert _normalise_name("Game Name (2024)") == "game name"

    def test_normalise_mixed_punctuation(self) -> None:
        assert _normalise_name("FINAL FANTASY VII REBIRTH") == "final fantasy vii rebirth"

    def test_normalise_dotted_name(self) -> None:
        assert _normalise_name("baldurs.gate.3.iso") == "baldurs gate 3"

    def test_normalise_strips_version_suffix(self) -> None:
        assert _normalise_name("game-name_v1.0.rar") == "game name"

    def test_normalise_preserves_numeric_name(self) -> None:
        assert _normalise_name("Hades II") == "hades ii"

    def test_normalise_empty_string(self) -> None:
        assert _normalise_name("") == ""


class TestLibraryMatch:
    """LibraryMatch dataclass construction."""

    def test_library_match_creation(self) -> None:
        match = LibraryMatch(found=True, matched_name="Elden Ring", matched_path="/games/Elden Ring")
        assert match.found is True
        assert match.matched_name == "Elden Ring"


class TestLibraryScanner:
    """LibraryScanner index building and game lookup."""

    def test_scanner_empty(self) -> None:
        scanner = LibraryScanner()
        assert scanner.check_game("Elden Ring") is None

    def test_scanner_empty_paths(self) -> None:
        scanner = LibraryScanner([])
        assert scanner.check_game("Elden Ring") is None

    def test_check_game_exact_match(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Elden Ring"
        game_dir.mkdir()
        scanner = LibraryScanner([str(tmp_path)])
        match = scanner.check_game("Elden Ring")
        assert match is not None
        assert match.found is True
        assert match.matched_name == "elden ring"

    def test_check_game_not_found(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Some Game"
        game_dir.mkdir()
        scanner = LibraryScanner([str(tmp_path)])
        match = scanner.check_game("Elden Ring")
        assert match is None

    def test_check_game_partial_match(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Elden Ring Deluxe Edition"
        game_dir.mkdir()
        scanner = LibraryScanner([str(tmp_path)])
        match = scanner.check_game("Elden Ring")
        assert match is not None
        assert match.found is True

    def test_check_game_partial_reverse(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Elden Ring"
        game_dir.mkdir()
        scanner = LibraryScanner([str(tmp_path)])
        match = scanner.check_game("Elden Ring Deluxe Edition")
        assert match is not None
        assert match.found is True

    def test_mixed_structure_dirs_and_files(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Hades II"
        game_dir.mkdir()
        (tmp_path / "cyberpunk-2077.iso").write_text("")
        scanner = LibraryScanner([str(tmp_path)])
        assert scanner.check_game("Hades II") is not None
        assert scanner.check_game("Cyberpunk 2077") is not None


class TestPartialMatchDirect:
    """Direct tests for _partial_match function."""

    def test_partial_match_exact(self) -> None:
        from gamarr.library import _partial_match

        result = _partial_match("elden ring", "elden ring")
        assert result is not None
        assert result >= 0.5

    def test_partial_match_substring(self) -> None:
        from gamarr.library import _partial_match

        result = _partial_match("elden ring", "elden ring deluxe edition")
        assert result is not None

    def test_partial_match_too_short(self) -> None:
        from gamarr.library import _partial_match

        result = _partial_match("ab", "abcdef")
        assert result is None

    def test_partial_match_no_commonality(self) -> None:
        from gamarr.library import _partial_match

        result = _partial_match("abcdefghij", "klmnopqrst")
        assert result is None


class TestLibraryScannerEdgeCases:
    """Edge cases for LibraryScanner."""

    def test_nonexistent_path_warns(self, tmp_path: Path) -> None:
        scanner = LibraryScanner([str(tmp_path / "nonexistent")])
        assert scanner.check_game("Elden Ring") is None

    def test_check_game_empty_title(self, tmp_path: Path) -> None:
        game_dir = tmp_path / "Game"
        game_dir.mkdir()
        scanner = LibraryScanner([str(tmp_path)])
        assert scanner.check_game("") is None

    def test_check_game_not_found_no_index(self) -> None:
        scanner = LibraryScanner([])
        assert scanner.check_game("Elden Ring") is None
