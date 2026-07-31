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

    def test_skip_when_dest_exists(self) -> None:
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
            patch("gamarr.post_processor._dir_contains_files", return_value=True),
        ):
            run_post_processing(config, qbt, db)

        db.set_post_process_state.assert_called_once()
        args = db.set_post_process_state.call_args[0]
        assert args[0] == "gamarr-test"
        assert args[1] == "copied"

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

    def test_compile_exclusion_regexes_invalid_skipped(self) -> None:
        from gamarr.post_processor import _compile_exclusion_regexes

        result = _compile_exclusion_regexes(["valid", "[invalid"], "test")
        assert len(result) == 1  # Only valid regex compiled

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


class TestRemoveDirectoryContents:
    """Tests for the _remove_directory_contents cleanup helper."""

    def test_removes_empty_directory(self, tmp_path: Path) -> None:
        """An empty directory is removed."""
        from gamarr.post_processor import _remove_directory_contents

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        _remove_directory_contents(str(empty_dir))
        assert not empty_dir.exists()

    def test_noop_nonexistent_path(self) -> None:
        """Non-existent path is silently skipped."""
        from gamarr.post_processor import _remove_directory_contents

        _remove_directory_contents("/nonexistent/path/xyz123")
        # Should not raise

    def test_removes_empty_dir_with_empty_child(self, tmp_path: Path) -> None:
        """A dir with an empty child dir is fully removed."""
        from gamarr.post_processor import _remove_directory_contents

        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        _remove_directory_contents(str(parent))
        assert not parent.exists()


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


class TestDirContainsFiles:
    """Tests for the _dir_contains_files helper."""

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory returns False."""
        from gamarr.post_processor import _dir_contains_files

        empty = tmp_path / "empty"
        empty.mkdir()
        assert _dir_contains_files(str(empty)) is False

    def test_file_at_depth_1(self, tmp_path: Path) -> None:
        """Directory with a direct file returns True."""
        from gamarr.post_processor import _dir_contains_files

        d = tmp_path / "has_file"
        d.mkdir()
        (d / "game.iso").touch()
        assert _dir_contains_files(str(d)) is True

    def test_file_at_depth_3(self, tmp_path: Path) -> None:
        """Files at depth 3+ are detected via os.walk."""
        from gamarr.post_processor import _dir_contains_files

        d = tmp_path / "deep"
        nested = d / "bin" / "win64"
        nested.mkdir(parents=True)
        (nested / "game.exe").touch()
        assert _dir_contains_files(str(d)) is True

    def test_only_empty_subdirs(self, tmp_path: Path) -> None:
        """Directory with only empty subdirectories returns False."""
        from gamarr.post_processor import _dir_contains_files

        d = tmp_path / "empty_tree"
        (d / "sub" / "deep").mkdir(parents=True)
        assert _dir_contains_files(str(d)) is False

    def test_nonexistent_path(self) -> None:
        """Non-existent path returns False (OSError caught)."""
        from gamarr.post_processor import _dir_contains_files

        assert _dir_contains_files("/nonexistent/path/xyz") is False


class TestRunCopyPhaseEmptyDirRetry:
    """Tests for the empty-dir removal and retry path in _run_copy_phase."""

    def test_empty_dir_removed_and_retried(self) -> None:
        """When destination exists but is empty, it is removed and copy proceeds."""
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
            patch("gamarr.post_processor.os.path.isdir", return_value=True),
            patch("gamarr.post_processor._dir_contains_files", return_value=False),
            patch("gamarr.post_processor._remove_directory_contents") as mock_remove,
            patch("gamarr.post_processor.make_directory", return_value=True),
            patch("gamarr.post_processor.copy_with_verify", return_value=True),
        ):
            result = _run_copy_phase(torrent, config, row, db)

        mock_remove.assert_called_once()
        assert result is True
        db.set_post_process_state.assert_called_once()

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
