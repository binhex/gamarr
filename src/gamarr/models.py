"""Shared data types for gamarr."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

__all__ = ["GameEntry", "HistoryRecord"]


@dataclass(frozen=True)
class GameEntry:
    """A single game discovered by a source (e.g. FitGirl RSS entry)."""

    title: str
    source_title: str
    source: str
    platform: str
    magnet_url: str
    source_url: str


class HistoryRecord(TypedDict, total=False):
    """Shape of a history database row for pipeline results."""

    id: int
    source: str
    source_title: str
    game_title: str | None
    platform: str
    metascore: float | None
    user_score: float | None
    result: str
    result_details: str
    magnet_url: str | None
    torrent_tag: str | None
    processed_at: str
