"""Tests for gamarr.utils."""

from pathlib import Path

from gamarr.utils import get_project_root


class TestGetProjectRoot:
    """Tests for get_project_root()."""

    def test_returns_path_object(self) -> None:
        """Should return a Path instance."""
        result = get_project_root()
        assert isinstance(result, Path)

    def test_points_to_project_root(self) -> None:
        """Should return the project root directory (parent of src/)."""
        result = get_project_root()
        # The project root is three levels up from utils.py:
        # utils.py -> gamarr/ -> src/ -> project root
        assert (result / "src" / "gamarr" / "utils.py").resolve() == Path(
            __file__
        ).resolve().parent.parent.parent / "src" / "gamarr" / "utils.py"

    def test_pyproject_toml_present(self) -> None:
        """The project root should contain pyproject.toml."""
        root = get_project_root()
        assert (root / "pyproject.toml").exists()
