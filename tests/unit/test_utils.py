"""Tests for gamarr.utils."""

from pathlib import Path

import pytest

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


class TestRunWithTimeout:
    """Watchdog helper that bounds a callable so it cannot block forever.

    Regression: a hung SeleniumBase WebDriver fetch blocked the gamarr
    acquisition thread indefinitely (no client-side timeout), and since
    the scheduler runs at most one acquisition job at a time, the whole
    pipeline was frozen for 13 days.
    """

    def test_returns_result_when_function_completes(self) -> None:
        from gamarr.utils import run_with_timeout

        result = run_with_timeout(lambda: "done", timeout_seconds=5)
        assert result == "done"

    def test_raises_when_function_hangs(self) -> None:
        import time

        from gamarr.utils import TimeoutExceededError, run_with_timeout

        def hang() -> None:
            time.sleep(30)  # simulates a wedged browser fetch

        start = time.monotonic()
        with pytest.raises(TimeoutExceededError):
            run_with_timeout(hang, timeout_seconds=0.3)
        elapsed = time.monotonic() - start
        assert elapsed < 5, f"watchdog did not abort promptly, took {elapsed:.1f}s"

    def test_propagates_function_exception(self) -> None:
        from gamarr.utils import run_with_timeout

        def boom() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_with_timeout(boom, timeout_seconds=5)

    def test_invokes_on_timeout_callback(self) -> None:
        import threading
        import time

        from gamarr.utils import TimeoutExceededError, run_with_timeout

        fired = threading.Event()

        def hang() -> None:
            time.sleep(30)

        with pytest.raises(TimeoutExceededError):
            run_with_timeout(hang, timeout_seconds=0.3, on_timeout=fired.set)
        assert fired.is_set(), "on_timeout callback was not invoked"

    def test_returns_none_result_correctly(self) -> None:
        from gamarr.utils import run_with_timeout

        result = run_with_timeout(lambda: None, timeout_seconds=5)
        assert result is None


class TestRunWithTimeoutGraceWindow:
    """A function that finishes just after its budget must still succeed."""

    def test_function_finishing_just_after_budget_returns_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import time

        import gamarr.utils as utils_module
        from gamarr.utils import run_with_timeout

        # Widen the grace window so this test is deterministic under CI load:
        # the point is the grace path itself, which has precise unit tests.
        monkeypatch.setattr(utils_module, "_GRACE_WINDOW_SECONDS", 2.0)

        def slow_but_finishes() -> str:
            time.sleep(0.31)  # past the 0.3s budget, well inside the widened grace window
            return "done"

        result = run_with_timeout(slow_but_finishes, timeout_seconds=0.3)
        assert result == "done", "a function completing within the grace window must not be timed out"

    def test_function_finishing_past_grace_window_is_timed_out(self) -> None:
        import time

        import pytest

        from gamarr.utils import TimeoutExceededError, run_with_timeout

        def too_slow() -> str:
            time.sleep(5)  # far beyond budget + grace: deterministic timeout
            return "late"  # pragma: no cover - unreachable, satisfies the return type

        with pytest.raises(TimeoutExceededError):
            run_with_timeout(too_slow, timeout_seconds=0.3)


class TestGraceWaitForOutcome:
    """Direct coverage for the extracted watchdog grace-wait helper."""

    def test_returns_false_when_worker_finished(self) -> None:
        import threading

        from gamarr.utils import _grace_wait_for_outcome

        worker = threading.Thread(target=lambda: None, daemon=True)
        worker.start()
        worker.join()
        result = _grace_wait_for_outcome(worker, outcome_pending=lambda: True)
        assert result is False, "a finished worker must never be treated as timed out"

    def test_returns_true_when_outcome_still_pending(self) -> None:
        import threading
        import time

        from gamarr.utils import _grace_wait_for_outcome

        def hang() -> None:
            time.sleep(5)

        worker = threading.Thread(target=hang, daemon=True)
        worker.start()
        start = time.monotonic()
        result = _grace_wait_for_outcome(worker, outcome_pending=lambda: True)
        elapsed = time.monotonic() - start
        assert result is True, "a pending outcome past the grace window must report a timeout"
        assert elapsed < 1, f"grace wait should be bounded, took {elapsed:.2f}s"

    def test_returns_false_when_outcome_recorded_during_window(self) -> None:
        import threading
        import time

        from gamarr.utils import _grace_wait_for_outcome

        done: list[bool] = []

        def finish_quickly() -> None:
            time.sleep(0.03)  # inside the 50ms grace window
            done.append(True)

        worker = threading.Thread(target=finish_quickly, daemon=True)
        worker.start()
        result = _grace_wait_for_outcome(worker, outcome_pending=lambda: not done)
        assert result is False, "an outcome recorded during the grace window must not time out"
