"""SQLite cache for Metacritic browse pages and game details.

Delegates to Database (gamarr.db) via SQLAlchemy models.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gamarr.database import Database


class MetacriticCache:
    """Cache for Metacritic scraped data, backed by the main gamarr DB.

    Args:
        db: A :class:`Database` instance connected to ``gamarr.db``.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def close(self) -> None:
        """No-op: the Database engine is managed by the pipeline."""
        pass

    def get_game_detail(self, slug: str, ttl_days: int = 7) -> dict[str, Any] | None:
        """Retrieve cached game detail for *slug* if within *ttl_days*."""
        return self._db.get_game_detail_cache(slug, ttl_days)

    def set_game_detail(
        self,
        slug: str,
        metascore: float | None,
        metascore_reviews: int | None,
        user_score: float | None,
        user_reviews: int | None,
    ) -> None:
        """Insert or update a cached game detail entry."""
        self._db.set_game_detail_cache(slug, metascore, metascore_reviews, user_score, user_reviews)

    def _set_cached_at(self, slug: str, cached_at: str) -> None:
        """Override the cached_at timestamp for testing."""
        with self._db._session() as session:
            from gamarr.database import GameDetailCache

            row = session.get(GameDetailCache, slug)
            if row is not None:
                row.cached_at = cached_at
                session.commit()

    def get_browse_page(self, platform: str, page_number: int, ttl_hours: int = 4) -> list[dict[str, Any]] | None:
        """Retrieve cached browse page data if within *ttl_hours*."""
        return self._db.get_browse_page_cache(platform, page_number, ttl_hours)

    def set_browse_page(self, platform: str, page_number: int, games: list[dict[str, Any]]) -> None:
        """Insert or replace a cached browse page entry."""
        self._db.set_browse_page_cache(platform, page_number, games)
