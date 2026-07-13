from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Final

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
    (re.compile(r"\bxii\b"), "12"),
    (re.compile(r"\bxi\b"), "11"),
    (re.compile(r"\bix\b"), "9"),
    (re.compile(r"\bviii\b"), "8"),
    (re.compile(r"\bvii\b"), "7"),
    (re.compile(r"\bvi\b"), "6"),
    (re.compile(r"\biv\b"), "4"),
    (re.compile(r"\biii\b"), "3"),
    (re.compile(r"\bii\b"), "2"),
    (re.compile(r"\bx\b"), "10"),
    (re.compile(r"\bv\b"), "5"),
    (re.compile(r"\bi\b"), "1"),
]

if TYPE_CHECKING:
    import threading


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


def is_cancelled(cancel_event: threading.Event | None) -> bool:
    """Return True if *cancel_event* is not None and is set."""
    return cancel_event is not None and cancel_event.is_set()
