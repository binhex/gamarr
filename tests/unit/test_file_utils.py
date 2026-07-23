"""Tests for gamarr file utilities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from gamarr.file_utils import copy_with_verify, make_directory


class TestMakeDirectory:
    """Tests for make_directory."""

    def test_creates_directory_and_parents(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c"
        assert make_directory(str(target)) is True
        assert target.is_dir()

    def test_existing_directory_returns_true(self, tmp_path: Path) -> None:
        target = tmp_path / "exists"
        target.mkdir()
        assert make_directory(str(target)) is True


class TestCopyWithVerify:
    """Tests for copy_with_verify."""

    def test_fresh_copy_succeeds(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst" / "src.bin"
        src.write_bytes(b"hello world test data")
        assert copy_with_verify(str(src), str(dst)) is True
        assert dst.is_file()
        assert dst.read_bytes() == b"hello world test data"

    def test_skip_when_dest_exists_and_matches(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        dst = dst_dir / "src.bin"
        data = b"skip match test data here"
        src.write_bytes(data)
        dst.write_bytes(data)
        with patch("gamarr.file_utils._sha256", return_value="abc123") as mock_sha:
            assert copy_with_verify(str(src), str(dst)) is True
        # _sha256 should have been called twice (src + dst), but _do_copy should NOT have been called
        assert mock_sha.call_count >= 2

    def test_re_copy_when_dest_exists_and_mismatches(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        dst = dst_dir / "src.bin"
        src.write_bytes(b"new correct data goes here")
        dst.write_bytes(b"old stale data")
        assert copy_with_verify(str(src), str(dst)) is True
        assert dst.read_bytes() == b"new correct data goes here"

    def test_returns_false_when_src_missing(self, tmp_path: Path) -> None:
        src = tmp_path / "nonexistent.bin"
        dst = tmp_path / "dst" / "nonexistent.bin"
        assert copy_with_verify(str(src), str(dst)) is False

    def test_creates_dst_parent_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "deep" / "nested" / "dst.bin"
        assert copy_with_verify(str(src), str(dst)) is True
        assert dst.is_file()

    def test_copy_permission_error_returns_false(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        dst = dst_dir / "src.bin"
        src.write_bytes(b"test data")
        from gamarr import file_utils
        with patch.object(file_utils, "_do_copy", side_effect=PermissionError("denied")):
            assert copy_with_verify(str(src), str(dst)) is False

    def test_copy_verification_source_disappeared(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        dst = dst_dir / "src.bin"
        src.write_bytes(b"test data")
        from gamarr import file_utils
        # After _do_copy succeeds, _sha256 of src raises OSError
        with (
            patch.object(file_utils, "_do_copy"),
            patch.object(file_utils, "_sha256", side_effect=OSError("gone")),
        ):
            assert copy_with_verify(str(src), str(dst)) is False

    def test_copy_verification_checksum_mismatch(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        dst = dst_dir / "src.bin"
        src.write_bytes(b"test data")
        from gamarr import file_utils
        with (
            patch.object(file_utils, "_do_copy"),
            patch.object(file_utils, "_sha256", side_effect=["abc123", "def456"]),
        ):
            assert copy_with_verify(str(src), str(dst)) is False

    def test_copy_src_not_found_returns_false(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "dst" / "src.bin"
        # Remove src before copy
        with patch("pathlib.Path.open") as mock_open:
            mock_open.side_effect = FileNotFoundError("gone")
            assert copy_with_verify(str(src), str(dst)) is False

    def test_copy_os_error_returns_false(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "dst" / "src.bin"
        with patch("pathlib.Path.open", side_effect=OSError("disk full")):
            assert copy_with_verify(str(src), str(dst)) is False

    def test_make_directory_permission_error(self, tmp_path: Path) -> None:
        with patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
            assert make_directory(str(tmp_path / "nope")) is False
