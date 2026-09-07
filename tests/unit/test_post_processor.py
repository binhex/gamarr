"""Tests for gamarr post-processor."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from gamarr.post_processor import (
    _build_destination_path,
    _compile_exclusion_regexes,
    _file_excluded,
    _safe_path_component,
    run_post_processing,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestSafePathComponent:
    """Tests for filesystem-safe path component sanitization."""

    def test_strips_unsafe_chars(self) -> None:
        assert _safe_path_component("Game: Title/With*Bad?Chars") == "Game TitleWithBadChars"

    def test_strips_dotdot(self) -> None:
        assert _safe_path_component("../etc/passwd") == "etcpasswd"

    def test_preserves_normal_text(self) -> None:
        assert _safe_path_component("Elden Ring") == "Elden Ring"

    def test_empty_returns_unknown(self) -> None:
        assert _safe_path_component("") == "Unknown"

    def test_dots_only_returns_unknown(self) -> None:
        assert _safe_path_component("...") == "Unknown"

    def test_sanitizes_windows_reserved_and_long_components(self) -> None:
        assert _safe_path_component("CON") == "_CON"
        assert _safe_path_component("NUL.txt") == "_NUL.txt"
        assert _safe_path_component("Game. ") == "Game"
        result = _safe_path_component("é" * 200)
        assert len(result.encode("utf-8")) <= 255
        assert not result.endswith((".", " "))


class TestBuildDestinationPath:
    """Tests for template-based path building."""

    def test_resolves_all_placeholders(self) -> None:
        result = _build_destination_path(
            template="/lib/{site}/{platform}/{genre}/{title}",
            source="fitgirl",
            platform="pc",
            genres="Action, RPG",
            game_title="Elden Ring",
        )
        assert result == "/lib/FitGirl/pc/Action/Elden Ring"

    def test_uses_first_genre_only(self) -> None:
        result = _build_destination_path(
            template="/lib/{genre}",
            source="fitgirl",
            platform="pc",
            genres="Strategy, Action, RPG",
            game_title="Civ VI",
        )
        assert result == "/lib/Strategy"

    def test_missing_genre_defaults_to_unknown(self) -> None:
        result = _build_destination_path(
            template="/lib/{genre}/{title}",
            source="fitgirl",
            platform="pc",
            genres=None,
            game_title="Test Game",
        )
        assert result == "/lib/Unknown/Test Game"

    def test_empty_library_path_returns_empty(self) -> None:
        result = _build_destination_path(
            template="",
            source="fitgirl",
            platform="pc",
            genres="Action",
            game_title="Test",
        )
        assert result == ""

    def test_placeholder_tokens_in_values_are_not_replaced_recursively(self) -> None:
        result = _build_destination_path(
            template="/lib/{site}",
            source="fitgirl-{title}",
            platform="pc",
            genres="Action",
            game_title="Test",
        )

        assert result == "/lib/fitgirl-{title}"


class TestFileExclusion:
    """Tests for file exclusion logic."""

    def test_min_kb_excludes_small_files(self) -> None:
        exclude_file_regexes = _compile_exclusion_regexes([], "file")
        exclude_folder_regexes = _compile_exclusion_regexes([], "folder")
        assert _file_excluded("setup.exe", ".", 50, exclude_file_regexes, exclude_folder_regexes, 100) is True
        assert _file_excluded("setup.exe", ".", 200, exclude_file_regexes, exclude_folder_regexes, 100) is False

    def test_file_regex_excludes_matching(self) -> None:
        exclude_file_regexes = _compile_exclusion_regexes(["sample", "proof"], "file")
        exclude_folder_regexes = _compile_exclusion_regexes([], "folder")
        assert _file_excluded("Sample.mkv", ".", 999999, exclude_file_regexes, exclude_folder_regexes, 0) is True
        assert _file_excluded("game.iso", ".", 999999, exclude_file_regexes, exclude_folder_regexes, 0) is False

    def test_folder_regex_excludes_matching(self) -> None:
        exclude_file_regexes = _compile_exclusion_regexes([], "file")
        exclude_folder_regexes = _compile_exclusion_regexes(["subs", "extras"], "folder")
        assert _file_excluded("movie.mkv", "Subs", 999999, exclude_file_regexes, exclude_folder_regexes, 0) is True
        assert _file_excluded("movie.mkv", "Bonus", 999999, exclude_file_regexes, exclude_folder_regexes, 0) is False

    def test_empty_regex_patterns_are_ignored(self) -> None:
        regexes = _compile_exclusion_regexes(["", "  ", "sample"], "file")

        assert len(regexes) == 1
        assert regexes[0].search("sample.bin") is not None


class TestRunPostProcessing:
    """Tests for the main post-processing entry point."""

    def test_disabled_returns_immediately(self) -> None:
        from gamarr.config import Config

        config = Config()
        config.post_process.post_process_enabled = False
        qbt = MagicMock()
        db = MagicMock()
        run_post_processing(config, qbt, db)
        qbt.is_connected.assert_not_called()

    def test_unreachable_qbt_logs_and_returns(self) -> None:
        from gamarr.config import Config

        config = Config()
        config.post_process.post_process_enabled = True
        qbt = MagicMock()
        qbt.is_connected.return_value = False
        db = MagicMock()
        run_post_processing(config, qbt, db)
        qbt.list_completed.assert_not_called()

    def test_no_completed_torrents_returns_early(self) -> None:
        from gamarr.config import Config

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.library_path = "/lib/{title}"
        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = ([], 0)
        db = MagicMock()
        run_post_processing(config, qbt, db)
        db.find_by_tag.assert_not_called()

    def test_skip_when_no_db_record(self) -> None:
        from gamarr.config import Config

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.library_path = "/lib/{title}"
        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = (
            [
                {
                    "torrent_tag": "gamarr-unknown",
                    "torrent_hash": "abc",
                    "torrent_name": "Unknown Game",
                    "torrent_save_path": "/dl",
                    "torrent_state": "uploading",
                    "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
                }
            ],
            1,
        )
        db = MagicMock()
        db.find_by_tag.return_value = None
        run_post_processing(config, qbt, db)
        # find_by_tag was called, but no copy attempted
        db.find_by_tag.assert_called_once_with("gamarr-unknown")

    def test_copy_phase_success(self) -> None:

        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.library_path = "/lib/{title}"
        config.post_process.exclude_file_min_kb = 0
        config.post_process.exclude_file_regex_list = []
        config.post_process.exclude_folder_regex_list = []

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = (
            [
                {
                    "torrent_tag": "gamarr-test",
                    "torrent_hash": "abc",
                    "torrent_name": "Elden Ring",
                    "torrent_save_path": "/dl/Elden Ring",
                    "torrent_state": "uploading",
                    "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
                }
            ],
            1,
        )

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.source = "fitgirl"
        fake_row.platform = "pc"
        fake_row.genres = "Action, RPG"
        fake_row.game_title = "Elden Ring"
        fake_row.post_process_state = None
        fake_row.post_process_copied_at = None
        db.find_by_tag.return_value = fake_row

        with (
            patch("gamarr.post_processor.make_directory", return_value=True),
            patch("gamarr.post_processor.copy_with_verify", return_value=True),
            patch("gamarr.post_processor.os.path.isdir", return_value=False),
        ):
            run_post_processing(config, qbt, db)

        db.set_post_process_state.assert_called_once()
        args, kwargs = db.set_post_process_state.call_args
        assert args[0] == "gamarr-test"
        assert args[1] == "copied"
        assert kwargs.get("copied_at") is not None

    def test_existing_destination_reconciles_expected_files(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.library_path = "/lib/{title}"

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = (
            [
                {
                    "torrent_tag": "gamarr-test",
                    "torrent_hash": "abc",
                    "torrent_name": "Elden Ring",
                    "torrent_save_path": "/dl/Elden Ring",
                    "torrent_state": "uploading",
                    "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
                }
            ],
            1,
        )

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.source = "fitgirl"
        fake_row.platform = "pc"
        fake_row.genres = "Action"
        fake_row.game_title = "Elden Ring"
        fake_row.post_process_state = None
        db.find_by_tag.return_value = fake_row

        with (
            patch("gamarr.post_processor.os.path.isdir", return_value=True),
            patch("gamarr.post_processor.make_directory", return_value=True),
            patch("gamarr.post_processor.copy_with_verify", return_value=True) as mock_copy,
        ):
            run_post_processing(config, qbt, db)

        mock_copy.assert_called_once_with("/dl/Elden Ring/game.iso", "/lib/Elden Ring/game.iso")
        db.set_post_process_state.assert_called_once()
        args, kwargs = db.set_post_process_state.call_args
        assert args[0] == "gamarr-test"
        assert args[1] == "copied"
        assert kwargs["copied_at"] is not None

    def test_empty_library_path_allows_deletion_when_copy_enabled(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.copy_completed = True
        config.post_process.remove_completed = True
        config.post_process.library_path = ""

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = (
            [
                {
                    "torrent_tag": "gamarr-test",
                    "torrent_hash": "abc",
                    "torrent_name": "Test Game",
                    "torrent_save_path": "/dl/Test Game",
                    "torrent_state": "pausedUP",
                    "torrent_file_list": [],
                }
            ],
            1,
        )

        row = MagicMock(spec=HistoryRow)
        row.post_process_state = None
        row.post_process_copied_at = None
        row.game_title = "Test Game"
        db = MagicMock()
        db.find_by_tag.return_value = row

        run_post_processing(config, qbt, db)

        qbt.delete_torrent.assert_called_once_with("abc", delete_data=True)
        db.set_post_process_state.assert_called_once_with("gamarr-test", "deleted")

    def test_copy_failure_retains_completed_torrent_for_retry(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.copy_completed = True
        config.post_process.remove_completed = True
        config.post_process.library_path = "/lib/{title}"

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = (
            [
                {
                    "torrent_tag": "gamarr-test",
                    "torrent_hash": "abc",
                    "torrent_name": "Test Game",
                    "torrent_save_path": "/dl/Test Game",
                    "torrent_state": "pausedUP",
                    "torrent_file_list": [],
                }
            ],
            1,
        )

        row = MagicMock(spec=HistoryRow)
        row.post_process_state = None
        row.post_process_copied_at = None
        row.game_title = "Test Game"
        db = MagicMock()
        db.find_by_tag.return_value = row

        with patch("gamarr.post_processor._run_copy_phase", return_value=False):
            run_post_processing(config, qbt, db)

        qbt.delete_torrent.assert_not_called()
        db.set_post_process_state.assert_not_called()

    def test_delete_phase_paused_state(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.remove_completed = True

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = (
            [
                {
                    "torrent_tag": "gamarr-test",
                    "torrent_hash": "abc",
                    "torrent_name": "Elden Ring",
                    "torrent_save_path": "/dl/Elden Ring",
                    "torrent_state": "pausedUP",  # seeding goal met
                    "torrent_file_list": [],
                }
            ],
            1,
        )

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.post_process_state = "copied"
        fake_row.post_process_copied_at = "2025-01-01T00:00:00"
        db.find_by_tag.return_value = fake_row

        run_post_processing(config, qbt, db)
        qbt.delete_torrent.assert_called_once_with("abc", delete_data=True)
        db.set_post_process_state.assert_called_once_with("gamarr-test", "deleted")

    def test_delete_phase_stays_if_still_seeding(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.remove_completed = True

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = (
            [
                {
                    "torrent_tag": "gamarr-test",
                    "torrent_hash": "abc",
                    "torrent_name": "Elden Ring",
                    "torrent_save_path": "/dl/Elden Ring",
                    "torrent_state": "uploading",  # still seeding
                    "torrent_file_list": [],
                }
            ],
            1,
        )

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        from datetime import timedelta

        fake_row.post_process_state = "copied"
        fake_row.post_process_copied_at = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
        db.find_by_tag.return_value = fake_row

        run_post_processing(config, qbt, db)

        qbt.delete_torrent.assert_not_called()  # still seeding, not old enough
        assert fake_row.post_process_state == "copied"  # unchanged

    def test_already_deleted_skipped(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = (
            [
                {
                    "torrent_tag": "gamarr-done",
                    "torrent_hash": "abc",
                    "torrent_name": "Done Game",
                    "torrent_save_path": "/dl",
                    "torrent_state": "pausedUP",
                    "torrent_file_list": [],
                }
            ],
            1,
        )

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.post_process_state = "deleted"
        db.find_by_tag.return_value = fake_row

        run_post_processing(config, qbt, db)
        qbt.delete_torrent.assert_not_called()  # already deleted


class TestCopiedAgeHours:
    """Tests for _copied_age_hours helper."""

    def test_none_returns_zero(self) -> None:
        from gamarr.post_processor import _copied_age_hours

        assert _copied_age_hours(None) == 0.0

    def test_empty_string_returns_zero(self) -> None:
        from gamarr.post_processor import _copied_age_hours

        assert _copied_age_hours("") == 0.0

    def test_valid_timestamp_returns_positive(self) -> None:
        from datetime import timedelta

        from gamarr.post_processor import _copied_age_hours

        past = (datetime.now(tz=UTC) - timedelta(hours=3)).isoformat()
        age = _copied_age_hours(past)
        assert 2.9 < age < 3.1

    def test_naive_timestamp_is_interpreted_as_utc(self) -> None:
        from datetime import timedelta

        from gamarr.post_processor import _copied_age_hours

        past = (datetime.now(tz=UTC) - timedelta(hours=3)).replace(tzinfo=None).isoformat()
        age = _copied_age_hours(past)
        assert 2.9 < age < 3.1

    def test_invalid_timestamp_returns_zero(self) -> None:
        from gamarr.post_processor import _copied_age_hours

        assert _copied_age_hours("not-a-timestamp") == 0.0


class TestSafeRelativePath:
    """Tests for torrent path containment validation."""

    def test_preserves_safe_nested_path(self) -> None:
        from gamarr.post_processor import _safe_relative_path

        assert _safe_relative_path("DLC/bonus.exe") == "DLC/bonus.exe"
        assert _safe_relative_path("...") == "..."

    def test_rejects_absolute_drive_and_traversal_paths(self) -> None:
        from gamarr.post_processor import _safe_relative_path

        for path in (
            "/absolute.exe",
            r"C:\absolute.exe",
            "C:",
            "../outside.exe",
            "foo/bar/../../../baz",
            "bad\x00.exe",
        ):
            assert _safe_relative_path(path) is None


class TestEdgeCases:
    """Tests for error paths in post-processor."""

    def test_copy_phase_empty_library_path(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_copy_phase

        config = Config()
        config.post_process.library_path = ""
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.source = "fitgirl"
        fake_row.platform = "pc"
        fake_row.genres = "Action"
        fake_row.game_title = "Test Game"
        torrent = {"torrent_tag": "t", "torrent_save_path": "/dl"}
        db_mock = MagicMock()
        _run_copy_phase(torrent, config, fake_row, db_mock)
        # Should not call set_post_process_state (library_path is empty)

    def test_build_copy_list_empty_save_path(self) -> None:
        from gamarr.post_processor import _build_copy_list

        class FakePP:
            exclude_file_min_kb: int = 0
            exclude_file_regex_list: list[str] = []
            exclude_folder_regex_list: list[str] = []

        torrent = {"torrent_save_path": "", "torrent_file_list": []}
        result = _build_copy_list(torrent, FakePP())
        assert result == []

    def test_build_copy_list_missing_file_name(self) -> None:
        from gamarr.post_processor import _build_copy_list

        class FakePP:
            exclude_file_min_kb: int = 0
            exclude_file_regex_list: list[str] = []
            exclude_folder_regex_list: list[str] = []

        torrent = {
            "torrent_save_path": "/dl",
            "torrent_file_list": [{"file_size": 100}, {"file_name": "good.iso", "file_size": 200}],
        }
        result = _build_copy_list(torrent, FakePP())
        assert len(result) == 1
        assert result[0] == "/dl/good.iso"

    def test_build_copy_list_invalid_file_size(self) -> None:
        from gamarr.post_processor import _build_copy_list

        class FakePP:
            exclude_file_min_kb: int = 0
            exclude_file_regex_list: list[str] = []
            exclude_folder_regex_list: list[str] = []

        torrent = {
            "torrent_save_path": "/dl",
            "torrent_file_list": [{"file_name": "game.iso", "file_size": "not-a-number"}],
        }
        result = _build_copy_list(torrent, FakePP())
        assert result == ["/dl/game.iso"]

    def test_build_copy_list_rejects_unsafe_paths(self) -> None:
        from gamarr.post_processor import _build_copy_list

        class FakePP:
            exclude_file_min_kb: int = 0
            exclude_file_regex_list: list[str] = []
            exclude_folder_regex_list: list[str] = []

        torrent = {
            "torrent_save_path": "/dl",
            "torrent_file_list": [
                {"file_name": "../outside.exe", "file_size": 100},
                {"file_name": "/absolute.exe", "file_size": 100},
                {"file_name": r"C:\\absolute.exe", "file_size": 100},
                {"file_name": "DLC/good.exe", "file_size": 100},
            ],
        }
        result = _build_copy_list(torrent, FakePP())
        assert result == ["/dl/DLC/good.exe"]

    def test_build_copy_list_rejects_symlink_outside_save_path(self, tmp_path: Path) -> None:
        from gamarr.post_processor import _build_copy_list

        class FakePP:
            exclude_file_min_kb: int = 0
            exclude_file_regex_list: list[str] = []
            exclude_folder_regex_list: list[str] = []

        save_path = tmp_path / "download"
        save_path.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("sensitive")
        (save_path / "linked.txt").symlink_to(outside)

        torrent = {
            "torrent_save_path": str(save_path),
            "torrent_file_list": [{"file_name": "linked.txt", "file_size": 100}],
        }

        assert _build_copy_list(torrent, FakePP()) == []

    def test_compile_exclusion_regexes_invalid_skipped(self) -> None:
        from gamarr.post_processor import _compile_exclusion_regexes

        result = _compile_exclusion_regexes(["valid", "[invalid"], "test")
        assert len(result) == 1  # Only valid regex compiled

    def test_copy_all_files_rejects_source_outside_save_path(self, tmp_path: Path) -> None:
        from gamarr.post_processor import _copy_all_files

        outside = tmp_path / "outside.exe"
        outside.write_text("outside")
        destination = tmp_path / "library"
        save_path = tmp_path / "download"

        result = _copy_all_files([str(outside)], str(destination), str(save_path))

        assert result is False
        assert not destination.exists()

    def test_delete_phase_age_timeout_triggers(self) -> None:
        from datetime import timedelta

        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_delete_phase

        config = Config()
        config.post_process.max_seed_wait_hours = 1
        qbt = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        torrent = {"torrent_tag": "abc", "torrent_hash": "abc", "torrent_state": "uploading"}
        # Set copied_at to 2 hours ago to exceed max_seed_wait_hours=1
        old = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
        fake_row.post_process_copied_at = old
        fake_row.post_process_state = "copied"
        db_mock = MagicMock()
        _run_delete_phase(torrent, config, qbt, fake_row, db_mock)
        qbt.delete_torrent.assert_called_once_with("abc", delete_data=True)
        db_mock.set_post_process_state.assert_called_once_with("abc", "deleted")

    def test_delete_phase_timeout_uses_processed_at_when_copy_skipped(self) -> None:
        from datetime import timedelta

        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_delete_phase

        config = Config()
        config.post_process.max_seed_wait_hours = 1
        qbt = MagicMock()
        row = MagicMock(spec=HistoryRow)
        row.post_process_state = None
        row.post_process_copied_at = None
        row.processed_at = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
        row.game_title = "Test Game"
        db = MagicMock()
        torrent = {"torrent_tag": "abc", "torrent_hash": "abc", "torrent_state": "uploading"}

        _run_delete_phase(torrent, config, qbt, row, db)

        qbt.delete_torrent.assert_called_once_with("abc", delete_data=True)
        db.set_post_process_state.assert_called_once_with("abc", "deleted")

    def test_run_post_processing_handles_torrent_exception(self) -> None:
        from gamarr.config import Config
        from gamarr.post_processor import run_post_processing

        config = Config()
        config.post_process.post_process_enabled = True
        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = (
            [
                {
                    "torrent_tag": "gamarr-bad",
                    "torrent_hash": "abc",
                    "torrent_name": "Bad",
                    "torrent_save_path": "/dl",
                    "torrent_state": "uploading",
                    "torrent_file_list": [],
                }
            ],
            1,
        )
        db = MagicMock()
        db.find_by_tag.side_effect = RuntimeError("DB crash")
        # Should not raise — exception is caught and logged
        run_post_processing(config, qbt, db)
        # If we get here without exception, the guard works

    def test_copy_phase_make_directory_failure(self) -> None:
        from gamarr import post_processor as pp_mod
        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_copy_phase

        config = Config()
        config.post_process.library_path = "/lib/{title}"
        config.post_process.exclude_file_min_kb = 0
        config.post_process.exclude_file_regex_list = []
        config.post_process.exclude_folder_regex_list = []
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.source = "fg"
        fake_row.platform = "pc"
        fake_row.genres = "Action"
        fake_row.game_title = "Test"
        torrent = {
            "torrent_tag": "t",
            "torrent_save_path": "/dl",
            "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
        }
        db_mock = MagicMock()
        with (
            patch.object(pp_mod.os.path, "isdir", return_value=False),
            patch.object(pp_mod, "make_directory", return_value=False),
        ):
            _run_copy_phase(torrent, config, fake_row, db_mock)
        # make_directory failed — should NOT set post_process_state

    def test_copy_phase_copy_with_verify_failure(self) -> None:
        from gamarr import post_processor as pp_mod
        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_copy_phase

        config = Config()
        config.post_process.library_path = "/lib/{title}"
        config.post_process.exclude_file_min_kb = 0
        config.post_process.exclude_file_regex_list = []
        config.post_process.exclude_folder_regex_list = []
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.source = "fg"
        fake_row.platform = "pc"
        fake_row.genres = "Action"
        fake_row.game_title = "Test"
        db_mock = MagicMock()
        torrent = {
            "torrent_tag": "t",
            "torrent_save_path": "/dl",
            "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
        }
        with (
            patch.object(pp_mod.os.path, "isdir", return_value=False),
            patch.object(pp_mod, "make_directory", return_value=True),
            patch.object(pp_mod, "copy_with_verify", return_value=False),
        ):
            _run_copy_phase(torrent, config, fake_row, db_mock)
        # copy_with_verify failed — should NOT set post_process_state

    def test_copy_failure_preserves_preexisting_destination_files(self, tmp_path: Path) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_copy_phase

        source = tmp_path / "download"
        source.mkdir()
        (source / "game.iso").write_text("game")
        destination = tmp_path / "library" / "Test Game"
        destination.mkdir(parents=True)
        keep = destination / "user-file.txt"
        keep.write_text("keep")

        config = Config()
        config.post_process.library_path = str(tmp_path / "library" / "{title}")
        row = MagicMock(spec=HistoryRow)
        row.source = "fitgirl"
        row.platform = "pc"
        row.genres = "Action"
        row.game_title = "Test Game"
        row.post_process_state = None
        db = MagicMock()
        torrent = {
            "torrent_tag": "gamarr-test",
            "torrent_save_path": str(source),
            "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
        }

        with patch("gamarr.post_processor.copy_with_verify", return_value=False):
            result = _run_copy_phase(torrent, config, row, db)

        assert result is False
        assert keep.read_text() == "keep"
        db.set_post_process_state.assert_not_called()


class TestDownloadingCount:
    """Post-processing summary must acknowledge in-progress downloads."""

    def test_summary_includes_downloading_count(self) -> None:
        """When there are in-progress gamarr torrents, the summary must show 'X downloading'."""
        from loguru import logger as loguru_logger

        from gamarr.config import Config
        from gamarr.post_processor import run_post_processing

        captured: list[str] = []
        sink_id = loguru_logger.add(
            lambda msg: captured.append(f"{msg.record['level'].name}: {msg}"),
            level="DEBUG",
            format="{message}",
        )
        try:
            config = Config()
            config.post_process.post_process_enabled = True
            config.post_process.library_path = "/lib/{title}"

            qbt = MagicMock()
            qbt.is_connected.return_value = True
            # list_completed returns (completed_list, total_gamarr_count)
            # 1 completed + 1 downloading = 2 total
            qbt.list_completed.return_value = (
                [
                    {
                        "torrent_tag": "gamarr-done",
                        "torrent_hash": "abc",
                        "torrent_name": "Done Game",
                        "torrent_save_path": "/dl",
                        "torrent_state": "pausedUP",
                        "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
                    },
                ],
                2,
            )
            db = MagicMock()
            db.find_by_tag.return_value = None

            run_post_processing(config, qbt, db)

            # Should mention the downloading count
            assert any(m.startswith("INFO:") and "downloading" in m.lower() for m in captured), (
                "Summary must mention 'downloading' count"
            )
            # Should show total: 1 completed + 1 downloading = 2 total
            assert any(m.startswith("INFO:") and "1 downloading" in m for m in captured), (
                "Summary must show '1 downloading' when there's 1 in-progress torrent"
            )
        finally:
            loguru_logger.remove(sink_id)


class TestPathCaseFormatting:
    """Tests for path_case formatting in _build_destination_path."""

    def test_pretty_source_name_is_display_name(self) -> None:
        """fitgirl becomes FitGirl, freegog becomes FreeGOG."""
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="/lib/{site}",
            source="fitgirl",
            platform="pc",
            genres="Action",
            game_title="Test",
            path_case="pretty",
        )
        assert result == "/lib/FitGirl"

        result = _build_destination_path(
            template="/lib/{site}",
            source="freegog",
            platform="pc",
            genres="Action",
            game_title="Test",
            path_case="pretty",
        )
        assert result == "/lib/FreeGOG"

    def test_pretty_unknown_source_is_pass_through(self) -> None:
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="/lib/{site}",
            source="unknown-source",
            platform="pc",
            genres="Action",
            game_title="Test",
            path_case="pretty",
        )
        assert result == "/lib/unknown-source"

    def test_pretty_platform_genre_title_are_pass_through(self) -> None:
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="/lib/{platform}/{genre}/{title}",
            source="fitgirl",
            platform="Nintendo Switch",
            genres="Action, RPG",
            game_title="Zelda",
            path_case="pretty",
        )
        assert result == "/lib/Nintendo Switch/Action/Zelda"

    def test_lowercase_downs_everything(self) -> None:
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="/lib/{site}/{platform}/{genre}/{title}",
            source="fitgirl",
            platform="PC",
            genres="Action,RPG",
            game_title="Elden Ring",
            path_case="lowercase",
        )
        assert result == "/lib/fitgirl/pc/action/elden ring"

    def test_default_is_pretty(self) -> None:
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="/lib/{site}",
            source="fitgirl",
            platform="pc",
            genres="Action",
            game_title="Test",
        )
        assert result == "/lib/FitGirl"

    def test_empty_template_returns_empty(self) -> None:
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="",
            source="fitgirl",
            platform="pc",
            genres="Action",
            game_title="Test",
            path_case="lowercase",
        )
        assert result == ""


class TestRunCopyPhase:
    """Tests for copy-phase retry and failure paths."""

    def test_empty_src_files_returns_false(self) -> None:
        """When no files match (all excluded), return False without copying."""
        from unittest.mock import MagicMock, patch

        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_copy_phase

        config = Config()
        config.post_process.library_path = "/lib/{title}"
        config.post_process.exclude_file_min_kb = 999999  # exclude everything

        row = MagicMock(spec=HistoryRow)
        row.source = "fitgirl"
        row.platform = "pc"
        row.genres = "Action"
        row.game_title = "Test Game"
        row.post_process_state = None

        db = MagicMock()
        torrent = {
            "torrent_tag": "gamarr-test",
            "torrent_hash": "abc",
            "torrent_save_path": "/dl/Test Game",
            "torrent_file_list": [{"file_name": "game.iso", "file_size": 1}],
        }

        with (
            patch("gamarr.post_processor.os.path.isdir", return_value=False),
        ):
            result = _run_copy_phase(torrent, config, row, db)

        assert result is False
        db.set_post_process_state.assert_not_called()

    def test_make_directory_fails_returns_false(self) -> None:
        """When destination directory cannot be created, return False."""
        from unittest.mock import MagicMock, patch

        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_copy_phase

        config = Config()
        config.post_process.library_path = "/lib/{title}"

        row = MagicMock(spec=HistoryRow)
        row.source = "fitgirl"
        row.platform = "pc"
        row.genres = "Action"
        row.game_title = "Test Game"
        row.post_process_state = None

        db = MagicMock()
        torrent = {
            "torrent_tag": "gamarr-test",
            "torrent_hash": "abc",
            "torrent_save_path": "/dl/Test Game",
            "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
        }

        with (
            patch("gamarr.post_processor.os.path.isdir", return_value=False),
            patch("gamarr.post_processor.make_directory", return_value=False),
        ):
            result = _run_copy_phase(torrent, config, row, db)

        assert result is False
        db.set_post_process_state.assert_not_called()


class TestRunDeletePhaseFailure:
    """Tests for delete_torrent returning False."""

    def test_state_not_set_when_delete_fails(self) -> None:
        """When qbt.delete_torrent returns False, post_process_state is NOT set to deleted."""
        from unittest.mock import MagicMock

        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_delete_phase

        config = Config()
        config.post_process.remove_completed = True

        qbt = MagicMock()
        qbt.delete_torrent.return_value = False

        row = MagicMock(spec=HistoryRow)
        row.game_title = "Test Game"
        row.post_process_copied_at = "2025-01-01T00:00:00"

        db = MagicMock()
        torrent = {
            "torrent_tag": "gamarr-test",
            "torrent_hash": "abc",
            "torrent_state": "pausedUP",
        }

        result = _run_delete_phase(torrent, config, qbt, row, db)

        assert result is False
        db.set_post_process_state.assert_not_called()


class TestCopyPhasePreservesStructure:
    """Copy must preserve the downloaded directory structure."""

    def test_nested_files_keep_relative_paths(self, tmp_path: Path) -> None:
        """Files in subdirectories must be copied into matching subdirectories."""
        from unittest.mock import MagicMock, patch

        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_copy_phase

        src = tmp_path / "download"
        (src / "DLC").mkdir(parents=True)
        (src / "bin" / "win64").mkdir(parents=True)
        (src / "game.exe").write_text("root")
        (src / "DLC" / "bonus.exe").write_text("dlc")
        (src / "bin" / "win64" / "game.exe").write_text("nested")

        config = Config()
        config.post_process.library_path = str(tmp_path / "library" / "{title}")
        config.post_process.exclude_file_min_kb = 0
        config.post_process.exclude_file_regex_list = []
        config.post_process.exclude_folder_regex_list = []

        row = MagicMock(spec=HistoryRow)
        row.source = "fitgirl"
        row.platform = "pc"
        row.genres = "Action"
        row.game_title = "Test Game"
        row.post_process_state = None
        row.post_process_copied_at = None

        db = MagicMock()
        torrent = {
            "torrent_tag": "gamarr-test",
            "torrent_save_path": str(src),
            "torrent_file_list": [
                {"file_name": "game.exe", "file_size": 100},
                {"file_name": "DLC/bonus.exe", "file_size": 100},
                {"file_name": "bin/win64/game.exe", "file_size": 100},
            ],
        }

        with patch("gamarr.post_processor.os.path.isdir", return_value=False):
            result = _run_copy_phase(torrent, config, row, db)

        assert result is True
        lib = tmp_path / "library" / "Test Game"
        assert (lib / "game.exe").read_text() == "root"
        assert (lib / "DLC" / "bonus.exe").read_text() == "dlc"
        assert (lib / "bin" / "win64" / "game.exe").read_text() == "nested"


class TestPostProcessingDatabaseLifecycle:
    """Tests for post-processing with real history persistence."""

    def test_copy_then_delete_round_trip_updates_history(self, tmp_path: Path) -> None:
        from gamarr.config import Config
        from gamarr.database import Database
        from gamarr.post_processor import run_post_processing

        source = tmp_path / "download"
        source.mkdir()
        game_file = source / "game.bin"
        game_file.write_bytes(b"game data")

        db = Database(tmp_path / "db")
        try:
            db.record_processed(
                source="fitgirl",
                source_title="Test Game",
                game_title="Test Game",
                platform="pc",
                result="Passed",
                torrent_tag="gamarr-test",
                genres="Action",
            )

            config = Config()
            config.post_process.library_path = str(tmp_path / "library" / "{title}")

            qbt = MagicMock()
            qbt.is_connected.return_value = True
            torrent = {
                "torrent_tag": "gamarr-test",
                "torrent_hash": "abc",
                "torrent_save_path": str(source),
                "torrent_state": "uploading",
                "torrent_file_list": [{"file_name": "game.bin", "file_size": game_file.stat().st_size}],
            }
            qbt.list_completed.return_value = ([torrent], 1)

            run_post_processing(config, qbt, db)

            destination = tmp_path / "library" / "Test Game" / "game.bin"
            assert destination.read_bytes() == b"game data"
            copied_row = db.find_by_tag("gamarr-test")
            assert copied_row is not None
            assert copied_row.post_process_state == "copied"
            assert copied_row.post_process_copied_at is not None

            qbt.delete_torrent.return_value = True
            torrent["torrent_state"] = "pausedUP"
            run_post_processing(config, qbt, db)

            deleted_row = db.find_by_tag("gamarr-test")
            assert deleted_row is not None
            assert deleted_row.post_process_state == "deleted"
            qbt.delete_torrent.assert_called_once_with("abc", delete_data=True)
        finally:
            db.close()
