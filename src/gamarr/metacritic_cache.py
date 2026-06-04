"""SQLite cache for Metacritic browse pages and game details.

Adapted from gamecritic's caching layer.  All cache entries have
TTLs and can be safely purged without affecting the history DB.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from typing import Any, cast


class MetacriticCache:
    """SQLite-backed cache for Metacritic scraped data.

    Args:
        cache_path: Path to the cache SQLite database. ``":memory:"``
            creates an in-memory database (useful for testing).
    """

    def __init__(self, cache_path: str) -> None:
        self._conn = sqlite3.connect(cache_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS game_detail_cache (
                slug              TEXT PRIMARY KEY,
                metascore         REAL,
                metascore_reviews INTEGER,
                user_score        REAL,
                user_reviews      INTEGER,
                cached_at         TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS browse_page_cache (
                platform    TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                games_json  TEXT NOT NULL,
                cached_at   TEXT NOT NULL,
                PRIMARY KEY (platform, page_number)
            );
        """)
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def get_game_detail(self, slug: str, ttl_days: int = 7) -> dict[str, Any] | None:
        """Retrieve cached game detail for *slug* if it exists and is within *ttl_days*.

        Args:
            slug: The Metacritic slug for the game.
            ttl_days: Number of days before the cache entry is considered stale.

        Returns:
            A dict with keys ``metascore``, ``metascore_reviews``,
            ``user_score``, ``user_reviews``, or ``None`` if not found or expired.
        """
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=ttl_days)).isoformat()
        row = self._conn.execute(
            "SELECT * FROM game_detail_cache WHERE slug = ? AND cached_at > ?",
            (slug, cutoff),
        ).fetchone()
        if row is None:
            return None
        return {
            "metascore": row["metascore"],
            "metascore_reviews": row["metascore_reviews"],
            "user_score": row["user_score"],
            "user_reviews": row["user_reviews"],
        }

    def set_game_detail(
        self,
        slug: str,
        metascore: float | None,
        metascore_reviews: int | None,
        user_score: float | None,
        user_reviews: int | None,
    ) -> None:
        """Insert or update a cached game detail entry.

        Args:
            slug: The Metacritic slug for the game.
            metascore: The critic metascore value.
            metascore_reviews: Number of critic reviews.
            user_score: The user score value.
            user_reviews: Number of user reviews.
        """
        self._conn.execute(
            """INSERT OR REPLACE INTO game_detail_cache
               (slug, metascore, metascore_reviews, user_score, user_reviews, cached_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (slug, metascore, metascore_reviews, user_score, user_reviews, datetime.datetime.now().isoformat()),
        )
        self._conn.commit()

    def _set_cached_at(self, slug: str, cached_at: str) -> None:
        """Override the cached_at timestamp for a game detail entry (testing helper).

        Args:
            slug: The Metacritic slug for the game.
            cached_at: ISO-format datetime string to set as the cached time.
        """
        self._conn.execute(
            "UPDATE game_detail_cache SET cached_at = ? WHERE slug = ?",
            (cached_at, slug),
        )
        self._conn.commit()

    def get_browse_page(self, platform: str, page_number: int, ttl_hours: int = 4) -> list[dict[str, Any]] | None:
        """Retrieve cached browse page data if it exists and is within *ttl_hours*.

        Args:
            platform: The platform identifier (e.g. ``"pc"``).
            page_number: The browse page number.
            ttl_hours: Number of hours before the cache entry is considered stale.

        Returns:
            A list of game dicts, or ``None`` if not found or expired.
        """
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=ttl_hours)).isoformat()
        row = self._conn.execute(
            "SELECT games_json FROM browse_page_cache WHERE platform = ? AND page_number = ? AND cached_at > ?",
            (platform, page_number, cutoff),
        ).fetchone()
        if row is None:
            return None
        return cast("list[dict[str, Any]]", json.loads(row["games_json"]))

    def set_browse_page(self, platform: str, page_number: int, games: list[dict[str, Any]]) -> None:
        """Insert or replace a cached browse page entry.

        Args:
            platform: The platform identifier (e.g. ``"pc"``).
            page_number: The browse page number.
            games: List of game dicts to cache.
        """
        self._conn.execute(
            """INSERT OR REPLACE INTO browse_page_cache (platform, page_number, games_json, cached_at)
               VALUES (?, ?, ?, ?)""",
            (platform, page_number, json.dumps(games), datetime.datetime.now().isoformat()),
        )
        self._conn.commit()
