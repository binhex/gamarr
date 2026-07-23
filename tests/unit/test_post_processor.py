"""Tests for gamarr post-processor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from gamarr.post_processor import (
    _build_destination_path,
    _compile_exclusion_regexes,
    _file_excluded,
    _safe_path_component,
    run_post_processing,
)


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
        assert result == "/lib/fitgirl/pc/Action/Elden Ring"

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
        qbt.list_completed.return_value = []
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
        qbt.list_completed.return_value = [
            {
                "torrent_tag": "gamarr-unknown",
                "torrent_hash": "abc",
                "torrent_name": "Unknown Game",
                "torrent_save_path": "/dl",
                "torrent_state": "uploading",
                "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
            }
        ]
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
        qbt.list_completed.return_value = [
            {
                "torrent_tag": "gamarr-test",
                "torrent_hash": "abc",
                "torrent_name": "Elden Ring",
                "torrent_save_path": "/dl/Elden Ring",
                "torrent_state": "uploading",
                "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
            }
        ]

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

        assert fake_row.post_process_state == "copied"
        assert fake_row.post_process_copied_at is not None

    def test_skip_when_dest_exists(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.library_path = "/lib/{title}"

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [
            {
                "torrent_tag": "gamarr-test",
                "torrent_hash": "abc",
                "torrent_name": "Elden Ring",
                "torrent_save_path": "/dl/Elden Ring",
                "torrent_state": "uploading",
                "torrent_file_list": [{"file_name": "game.iso", "file_size": 999999}],
            }
        ]

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
        ):
            run_post_processing(config, qbt, db)

        assert fake_row.post_process_state is None  # unchanged — dest existed

    def test_delete_phase_paused_state(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.remove_completed = True

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [
            {
                "torrent_tag": "gamarr-test",
                "torrent_hash": "abc",
                "torrent_name": "Elden Ring",
                "torrent_save_path": "/dl/Elden Ring",
                "torrent_state": "pausedUP",  # seeding goal met
                "torrent_file_list": [],
            }
        ]

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.post_process_state = "copied"
        fake_row.post_process_copied_at = "2025-01-01T00:00:00"
        db.find_by_tag.return_value = fake_row

        run_post_processing(config, qbt, db)
        qbt.delete_torrent.assert_called_once_with("abc", delete_data=True)
        assert fake_row.post_process_state == "deleted"

    def test_delete_phase_stays_if_still_seeding(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow

        config = Config()
        config.post_process.post_process_enabled = True
        config.post_process.remove_completed = True

        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [
            {
                "torrent_tag": "gamarr-test",
                "torrent_hash": "abc",
                "torrent_name": "Elden Ring",
                "torrent_save_path": "/dl/Elden Ring",
                "torrent_state": "uploading",  # still seeding
                "torrent_file_list": [],
            }
        ]

        db = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        fake_row.post_process_state = "copied"
        fake_row.post_process_copied_at = "2099-01-01T00:00:00"  # far future — won't time out
        db.find_by_tag.return_value = fake_row

        with patch("gamarr.post_processor._copied_age_hours", return_value=1):
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
        qbt.list_completed.return_value = [
            {
                "torrent_tag": "gamarr-done",
                "torrent_hash": "abc",
                "torrent_name": "Done Game",
                "torrent_save_path": "/dl",
                "torrent_state": "pausedUP",
                "torrent_file_list": [],
            }
        ]

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
        from datetime import datetime, timezone, timedelta

        from gamarr.post_processor import _copied_age_hours

        past = (datetime.now(tz=timezone.utc) - timedelta(hours=3)).isoformat()
        age = _copied_age_hours(past)
        assert 2.9 < age < 3.1

    def test_invalid_timestamp_returns_zero(self) -> None:
        from gamarr.post_processor import _copied_age_hours

        assert _copied_age_hours("not-a-timestamp") == 0.0


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
        _run_copy_phase(torrent, config, fake_row)
        # Should return without setting post_process_state (library_path is empty)

    def test_build_copy_list_empty_save_path(self) -> None:
        from gamarr.post_processor import _build_copy_list

        class FakePP:
            exclude_file_min_kb = 0
            exclude_file_regex_list = []
            exclude_folder_regex_list = []

        torrent = {"torrent_save_path": "", "torrent_file_list": []}
        result = _build_copy_list(torrent, FakePP())
        assert result == []

    def test_build_copy_list_missing_file_name(self) -> None:
        from gamarr.post_processor import _build_copy_list

        class FakePP:
            exclude_file_min_kb = 0
            exclude_file_regex_list = []
            exclude_folder_regex_list = []

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
            exclude_file_min_kb = 0
            exclude_file_regex_list = []
            exclude_folder_regex_list = []

        torrent = {
            "torrent_save_path": "/dl",
            "torrent_file_list": [{"file_name": "game.iso", "file_size": "not-a-number"}],
        }
        result = _build_copy_list(torrent, FakePP())
        assert result == ["/dl/game.iso"]

    def test_compile_exclusion_regexes_invalid_skipped(self) -> None:
        from gamarr.post_processor import _compile_exclusion_regexes

        result = _compile_exclusion_regexes(["valid", "[invalid"], "test")
        assert len(result) == 1  # Only valid regex compiled

    def test_delete_phase_age_timeout_triggers(self) -> None:
        from datetime import datetime, timezone, timedelta

        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_delete_phase

        config = Config()
        config.post_process.max_seed_wait_hours = 1
        qbt = MagicMock()
        fake_row = MagicMock(spec=HistoryRow)
        torrent = {"torrent_hash": "abc", "torrent_state": "uploading"}
        # Set copied_at to 2 hours ago to exceed max_seed_wait_hours=1
        old = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
        fake_row.post_process_copied_at = old
        fake_row.post_process_state = "copied"
        _run_delete_phase(torrent, config, qbt, fake_row)
        qbt.delete_torrent.assert_called_once_with("abc", delete_data=True)
        assert fake_row.post_process_state == "deleted"

    def test_run_post_processing_handles_torrent_exception(self) -> None:
        from gamarr.config import Config
        from gamarr.post_processor import run_post_processing

        config = Config()
        config.post_process.post_process_enabled = True
        qbt = MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [{
            "torrent_tag": "gamarr-bad",
            "torrent_hash": "abc",
            "torrent_name": "Bad",
            "torrent_save_path": "/dl",
            "torrent_state": "uploading",
            "torrent_file_list": [],
        }]
        db = MagicMock()
        db.find_by_tag.side_effect = RuntimeError("DB crash")
        # Should not raise — exception is caught and logged
        run_post_processing(config, qbt, db)
        # If we get here without exception, the guard works

    def test_copy_phase_make_directory_failure(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_copy_phase
        from gamarr import post_processor as pp_mod

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
        with (
            patch.object(pp_mod.os.path, "isdir", return_value=False),
            patch.object(pp_mod, "make_directory", return_value=False),
        ):
            _run_copy_phase(torrent, config, fake_row)
        # make_directory failed — should NOT set post_process_state

    def test_copy_phase_copy_with_verify_failure(self) -> None:
        from gamarr.config import Config
        from gamarr.database import HistoryRow
        from gamarr.post_processor import _run_copy_phase
        from gamarr import post_processor as pp_mod

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
        with (
            patch.object(pp_mod.os.path, "isdir", return_value=False),
            patch.object(pp_mod, "make_directory", return_value=True),
            patch.object(pp_mod, "copy_with_verify", return_value=False),
        ):
            _run_copy_phase(torrent, config, fake_row)
        # copy_with_verify failed — should NOT set post_process_state
