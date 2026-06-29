"""Shared data types for gamarr."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GameEntry"]


@dataclass(frozen=True)
class GameEntry:
    """A single game discovered by a source."""

    title: str
    source_title: str
    source: str
    platform: str
    magnet_url: str
    source_url: str
