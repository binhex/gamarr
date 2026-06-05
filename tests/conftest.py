"""Shared test fixtures for gamarr tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gamarr.models import GameEntry

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sample_game_entry() -> GameEntry:
    """Return a minimal valid GameEntry for use in pipeline tests."""
    return GameEntry(
        title="Test Game",
        source_title="Test Game (v1.0) [Repack]",
        source="fitgirl",
        platform="pc",
        magnet_url="magnet:?xt=urn:btih:abc123",
        source_url="https://fitgirl-repacks.site/test-game/",
    )


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """Return a temporary database directory path."""
    db_dir = tmp_path / "db"
    db_dir.mkdir(parents=True)
    return str(db_dir)
