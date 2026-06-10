from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading


def get_project_root() -> Path:
    return Path(__file__).parent.parent.parent


def normalise_for_compare(text: str) -> str:
    """Normalise a title string for case-insensitive fuzzy comparison.

    Lowercases the text and removes everything that isn't
    alphanumeric (a-z, 0-9). Both sides get the same treatment
    so comparisons remain valid while handling abbreviation
    discrepancies (e.g. ``"P.I."`` vs ``"P I"`` from URL slugs).
    """
    text = text.lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def is_cancelled(cancel_event: threading.Event | None) -> bool:
    """Return True if *cancel_event* is not None and is set."""
    return cancel_event is not None and cancel_event.is_set()
