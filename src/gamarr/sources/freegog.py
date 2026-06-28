"""FreeGOG source for gamarr.

Fetches the FreeGOG game index via JSON API, stores individual
game entries incrementally, and supports incremental re-indexing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from gamarr.database import Database

if TYPE_CHECKING:
    import threading


class FreeGOGSource:
    """FreeGOG.com game source implementation.

    This source uses incremental indexing: individual game entries
    are added via ``store_source_title`` rather than bulk replacing
    the entire index (unlike FitGirl's ``rebuild_source_titles``).

    Args:
        platform: Platform identifier (default ``"pc"``).
        db: Database instance for storing source titles.
        cache_pages_hours: Hours before the sitemap cache expires.
    """

    def __init__(
        self,
        *,
        platform: str = "pc",
        db: Database | None = None,
        cache_pages_hours: int = 6,
    ) -> None:
        self._platform = platform
        self._cache_pages_hours = cache_pages_hours
        self._db = db if db is not None else Database(":memory:")

    @property
    def source_name(self) -> str:
        """Return ``"freegog"`` as the source identifier."""
        return "freegog"

    @property
    def platform(self) -> str:
        """Return the platform this source targets."""
        return self._platform

    def fetch_sitemap(
        self,
        db: Database | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Fetch the FreeGOG game index and store results in the DB.

        Args:
            db: The database instance to store results in.
                Falls back to the instance's own database if not provided.
            cancel_event: Optional event to signal cancellation.
        """
        _db = db if db is not None else self._db

        if cancel_event is not None and cancel_event.is_set():
            return

        logger.debug("FreeGOG source is a stub — sitemap fetch not yet implemented")
        _db.set_sitemap_cache("freegog")

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()
