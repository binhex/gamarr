"""Shared data types for gamarr."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GameEntry", "SOURCE_DISPLAY"]

# Maps internal source identifiers to display-friendly names.
# Shared across pipeline (log display) and post-processor (path templates).
SOURCE_DISPLAY: dict[str, str] = {"fitgirl": "FitGirl", "freegog": "FreeGOG"}


@dataclass(frozen=True)
class GameEntry:
    """A single game discovered by a source."""

    title: str
    source_title: str
    source: str
    platform: str
    magnet_url: str
    source_url: str
