from __future__ import annotations

import itertools
import re
import threading
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable

# Roman numeral → Arabic numeral substitution patterns.
# Each pattern matches standalone word-bounded Roman numerals only,
# so "vs" is not affected (the "v" is followed by "s", a word char).
# Where one pattern contains another (e.g. "viii" contains "iii"),
# the longer pattern must appear first to avoid partial replacement.
# The single-letter "i", "v", and "x" are intentionally included —
# they can convert standalone characters in titles ("I Am Bread" →
# "1ambread", "V Rising" → "5rising"), but this is vanishingly rare
# in game catalogues compared to Roman numeral use.
_ROMAN_TO_ARABIC: Final[list[tuple[re.Pattern[str], str]]] = [
    (re.compile(r"\bxviii\b"), "18"),
    (re.compile(r"\bxvii\b"), "17"),
    (re.compile(r"\bxiv\b"), "14"),
    (re.compile(r"\bxiii\b"), "13"),
    (re.compile(r"\bxii\b"), "12"),
    (re.compile(r"\bxi\b"), "11"),
    (re.compile(r"\bix\b"), "9"),
    (re.compile(r"\bviii\b"), "8"),
    (re.compile(r"\bvii\b"), "7"),
    (re.compile(r"\bvi\b"), "6"),
    (re.compile(r"\biv\b"), "4"),
    (re.compile(r"\biii\b"), "3"),
    (re.compile(r"\bii\b"), "2"),
    (re.compile(r"\bxix\b"), "19"),
    (re.compile(r"\bxv\b"), "15"),
    (re.compile(r"\bxvi\b"), "16"),
    (re.compile(r"\bx\b"), "10"),
    (re.compile(r"\bv\b"), "5"),
    (re.compile(r"\bi\b"), "1"),
]


class CancelSignal(Protocol):
    """Structural type for any cancel signal with ``is_set()``.

    Covers :class:`threading.Event` as well as composite signals (e.g.
    the scheduler's per-run watchdog + shutdown event wrapper).
    """

    def is_set(self) -> bool: ...


class TimeoutExceededError(RuntimeError):
    """Raised when a callable run under a watchdog exceeds its time budget."""


_WATCHDOG_THREAD_IDS = itertools.count(1)


def run_with_timeout[T](
    func: Callable[[], T],
    timeout_seconds: float,
    *,
    on_timeout: Callable[[], None] | None = None,
) -> T:
    """Run *func* to completion or abort it after *timeout_seconds*.

    Executes func on a daemon worker thread and waits up to
    *timeout_seconds*.  If the worker is still running when the budget
    expires, *on_timeout* is invoked (best effort) and
    :class:`TimeoutExceededError` is raised in the caller.  The worker
    thread keeps running until it finishes; callers that cannot tolerate
    a detached worker must arrange cleanup via *on_timeout*.

    Any exception raised by func is re-raised in the caller; nothing is
    silently swallowed.

    Raises:
        TimeoutExceededError: When the time budget is exceeded.
    """
    outcome_ok: bool | None = None
    outcome_value: Any = None
    outcome_error: BaseException | None = None

    def _run() -> None:
        nonlocal outcome_ok, outcome_value, outcome_error
        try:
            outcome_value = func()
            # Only mark complete AFTER func returns — while func runs the
            # outcome must stay unset so the watchdog can detect a hang.
            outcome_ok = True
        except BaseException as exc:
            # Deliberately forward worker failures (incl. SystemExit/KeyboardInterrupt).
            outcome_ok = False
            outcome_error = exc

    worker = threading.Thread(
        target=_run,
        name=f"watchdog-worker-{next(_WATCHDOG_THREAD_IDS)}",
        daemon=True,
    )
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive() and outcome_ok is None and outcome_error is None:
        # The worker may have returned from func() without recording its
        # outcome yet (a few bytecodes before the assignment). Give it a
        # brief grace window before declaring a timeout.
        worker.join(0.01)
    if worker.is_alive() and outcome_ok is None and outcome_error is None:
        if on_timeout is not None:
            # Best effort: a failing callback must not mask TimeoutExceededError.
            with suppress(Exception):
                on_timeout()
        raise TimeoutExceededError(f"function did not complete within {timeout_seconds:g}s and was aborted")

    if outcome_ok:
        return cast("T", outcome_value)
    if outcome_error is not None:
        raise outcome_error
    raise RuntimeError("watchdog worker terminated without a result")  # pragma: no cover


def get_project_root() -> Path:
    return Path(__file__).parent.parent.parent


def normalise_for_compare(text: str) -> str:
    """Normalise a title string for case-insensitive fuzzy comparison.

    Lowercases the text, converts Roman numerals to Arabic equivalents
    (e.g. ``"III"`` → ``"3"``), and removes everything that isn't
    alphanumeric (a-z, 0-9). Both sides get the same treatment
    so comparisons remain valid while handling abbreviation
    discrepancies (e.g. ``"P.I."`` vs ``"P I"`` from URL slugs).
    """
    text = text.lower()
    for pattern, replacement in _ROMAN_TO_ARABIC:
        text = pattern.sub(replacement, text)
    return re.sub(r"[^a-z0-9]+", "", text)


def is_cancelled(cancel_event: CancelSignal | None) -> bool:
    """Return True if *cancel_event* is not None and is set."""
    return cancel_event is not None and cancel_event.is_set()
