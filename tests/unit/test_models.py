"""Tests for gamarr models and source protocol."""

from __future__ import annotations

from gamarr.models import GameEntry
from gamarr.sources import BaseSource


class TestGameEntry:
    """GameEntry dataclass construction and defaults."""

    def test_minimal_construction(self) -> None:
        """A GameEntry can be built with just the required fields."""
        entry = GameEntry(
            title="Elden Ring",
            source_title="Elden Ring (v1.12) [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:abc",
            source_url="https://fitgirl-repacks.site/elden-ring/",
        )
        assert entry.title == "Elden Ring"
        assert entry.source == "fitgirl"
        assert entry.platform == "pc"

    def test_all_fields(self) -> None:
        """All GameEntry fields are accessible."""
        entry = GameEntry(
            title="Test Game",
            source_title="Test Game [Repack]",
            source="fitgirl",
            platform="pc",
            magnet_url="magnet:?xt=urn:btih:xyz",
            source_url="https://example.com/test-game/",
        )
        assert entry.title == "Test Game"
        assert entry.source_title == "Test Game [Repack]"


class TestBaseSource:
    """BaseSource protocol contract."""

    def test_protocol_has_source_name(self) -> None:
        """BaseSource requires a source_name property."""
        assert hasattr(BaseSource, "source_name")
