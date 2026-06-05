"""Metacritic score lookup for gamarr.

Adapted from gamecritic's Nuxt JSON scraping approach.  Looks up a game
title by first trying a direct slug URL, then falling back to browse
page scanning.
"""

from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup
from loguru import logger

from gamarr.metacritic_cache import MetacriticCache

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_CONNECT_TIMEOUT = 30.0
_READ_TIMEOUT = 90.0

_BROWSE_GAME_KEY_PATTERN = re.compile(r'"(browse-game-[^"]*)":\s*(\d+)')


@dataclass
class ScoreResult:
    """Result of a Metacritic score lookup for a single game."""

    title: str
    slug: str
    metascore: float | None
    metascore_review_count: int | None
    user_score: float | None
    user_review_count: int | None
    passed: bool
    genres: list[str] | None = None
    must_play: bool | None = None
    release_date: str | None = None
    description: str | None = None


def _make_slug(title: str) -> str:
    """Convert a game title into a Metacritic URL slug."""
    slug = title.lower()
    slug = slug.replace("'", "")
    slug = slug.replace("&", "and")
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _nuxt_val(data: list[Any], ref: Any) -> Any:
    if isinstance(ref, int) and not isinstance(ref, bool) and ref < len(data):
        return data[ref]
    return ref


def _extract_critic_score(
    page_data: list[Any], item: dict[str, Any], current: float | None
) -> tuple[float | None, int | None]:
    """Extract metascore and review count from a Nuxt data item if present."""
    if current is not None or "score" not in item or "reviewCount" not in item:
        return (current, None)
    score_val = _nuxt_val(page_data, item["score"])
    if isinstance(score_val, (int, float)):
        return (float(score_val), _nuxt_val(page_data, item.get("reviewCount")))
    return (current, None)


def _extract_user_score(
    page_data: list[Any], item: dict[str, Any], current: float | None
) -> tuple[float | None, int | None]:
    """Extract user score and review count from a Nuxt data item if present."""
    if current is not None or "userScore" not in item:
        return (current, None)
    us = _nuxt_val(page_data, item.get("userScore"))
    if isinstance(us, dict) and "score" in us:
        us_score = _nuxt_val(page_data, us.get("score"))
        if isinstance(us_score, (int, float)):
            return (float(us_score), _nuxt_val(page_data, us.get("reviewCount")))
    return (current, None)


def _find_game_details_in_nuxt_data(page_data: list[Any]) -> dict[str, Any] | None:
    """Extract critic scores, user scores, and game details from Nuxt JSON."""
    metascore = metascore_reviews = user_score = user_reviews = None
    genres = must_play = release_date = description = None

    for item in page_data:
        if not isinstance(item, dict):
            continue

        ms, msv = _extract_critic_score(page_data, item, metascore)
        if ms != metascore:
            metascore, metascore_reviews = ms, msv

        us, usv = _extract_user_score(page_data, item, user_score)
        if us != user_score:
            user_score, user_reviews = us, usv

        if genres is None and "mustPlay" in item and "genres" in item:
            must_play = _nuxt_val(page_data, item.get("mustPlay"))
            genres_list = _nuxt_val(page_data, item.get("genres"))
            if isinstance(genres_list, list):
                genres = []
                for g in genres_list:
                    gd = _nuxt_val(page_data, g)
                    if isinstance(gd, dict):
                        name = _nuxt_val(page_data, gd.get("name"))
                        if name:
                            genres.append(str(name))
            release_date = _nuxt_val(page_data, item.get("releaseDate"))
            description = _nuxt_val(page_data, item.get("description"))

    if metascore is not None or user_score is not None:
        return {
            "metascore": metascore,
            "metascore_reviews": metascore_reviews,
            "user_score": user_score,
            "user_reviews": user_reviews,
            "genres": genres,
            "must_play": must_play,
            "release_date": str(release_date) if release_date else None,
            "description": str(description)[:200] if description else None,
        }
    return None


def _find_nuxt_scores_in_page(page_content: bytes) -> dict[str, Any] | None:
    """Find and parse Nuxt JSON scores from a Metacritic game page."""
    try:
        soup = BeautifulSoup(page_content, features="html.parser")
    except TypeError:
        return None
    for script in soup.find_all("script"):
        stext = script.string or ""
        if len(stext) < 1000:
            continue
        try:
            page_data = json.loads(stext)
        except (json.JSONDecodeError, TypeError):
            continue
        result = _find_game_details_in_nuxt_data(page_data)
        if result is not None:
            return result
    return None


def _parse_game_details(page_content: bytes) -> dict[str, Any] | None:
    return _find_nuxt_scores_in_page(page_content)


def _try_resolve_script(parsed: list[Any], stext: str) -> tuple[list[Any], list[int]] | None:
    """Attempt to resolve browse-game items from a single Nuxt script."""
    m = _BROWSE_GAME_KEY_PATTERN.search(stext)
    if not m:
        return None
    root_idx = int(m.group(2))
    if root_idx >= len(parsed):
        return None
    root = parsed[root_idx]
    items_ref = root.get("items")
    if not isinstance(items_ref, int) or items_ref >= len(parsed):
        return None
    game_items = parsed[items_ref]
    if not isinstance(game_items, list):
        return None
    return (parsed, game_items)


def _resolve_browse_nuxt_data(soup: Any) -> tuple[list[Any], list[int]] | None:
    """Find and resolve browse-game Nuxt data from a Metacritic browse page."""
    for script in soup.find_all("script"):
        stext = script.string or ""
        if "browse-game" not in stext:
            continue
        try:
            parsed = json.loads(stext)
            result = _try_resolve_script(parsed, stext)
            if result is not None:
                return result
        except (json.JSONDecodeError, TypeError, KeyError, IndexError):
            pass
    return None


def _resolve_browse_game_list(nuxt_data: list[Any], game_items: list[int]) -> list[dict[str, Any]]:
    """Resolve game dicts from Nuxt data by following item indices."""
    resolved: list[dict[str, Any]] = []
    for game_idx in game_items:
        try:
            game = nuxt_data[game_idx]
            cs = _nuxt_val(nuxt_data, game.get("criticScoreSummary"))
            us = _nuxt_val(nuxt_data, game.get("userScore"))
            resolved.append(
                {
                    "title": _nuxt_val(nuxt_data, game.get("title")),
                    "slug": _nuxt_val(nuxt_data, game.get("slug")),
                    "score": cs.get("score") if isinstance(cs, dict) else None,
                    "critic_review_count": cs.get("reviewCount") if isinstance(cs, dict) else None,
                    "user_rating": us.get("score") if isinstance(us, dict) else None,
                    "user_review_count": us.get("reviewCount") if isinstance(us, dict) else None,
                }
            )
        except (TypeError, KeyError, IndexError):
            pass
    return resolved


def _parse_browse_page(content: bytes) -> list[dict[str, Any]] | None:
    try:
        soup = BeautifulSoup(content, features="html.parser")
    except TypeError:
        return None

    result = _resolve_browse_nuxt_data(soup)
    if result is None:
        return None

    nuxt_data, game_items = result
    return _resolve_browse_game_list(nuxt_data, game_items)


class MetacriticClient:
    """Client for looking up game scores on Metacritic.

    Uses a two-step strategy:
    1. Direct slug guess (fast path for common titles)
    2. Browse page scan (fallback for non-standard slugs)

    Results are cached in a local SQLite database.
    """

    def __init__(
        self,
        cache_path: str = "db/gamarr-cache.db",
        user_agent: str = _USER_AGENT,
    ) -> None:
        """Initialise the client.

        Args:
            cache_path: Path to the SQLite cache database.
            user_agent: User-Agent header to use for HTTP requests.
        """
        self.user_agent = user_agent
        self._cache = MetacriticCache(cache_path)

    def close(self) -> None:
        """Close the underlying cache database connection."""
        self._cache.close()

    def lookup_game(
        self,
        title: str,
        platform: str = "pc",
        cache_ttl_days: int = 7,
        browse_cache_ttl_hours: int = 4,
    ) -> ScoreResult | None:
        """Look up Metacritic scores for a game by title.

        Args:
            title: The game title to search for.
            platform: The platform identifier (default ``"pc"``).
            cache_ttl_days: TTL in days for game detail cache.
            browse_cache_ttl_hours: TTL in hours for browse page cache.

        Returns:
            A :class:`ScoreResult` if found, or ``None`` if the game
            could not be located on Metacritic.
        """
        slug = _make_slug(title)

        result = self._try_direct_slug(slug, cache_ttl_days)
        if result is not None:
            return result

        logger.debug("Direct slug '{}' failed for '{}', scanning browse pages...", slug, title)
        result = self._scan_browse_pages(title, platform, browse_cache_ttl_hours, cache_ttl_days)
        return result

    def _try_direct_slug(self, slug: str, cache_ttl_days: int) -> ScoreResult | None:
        cached = self._cache.get_game_detail(slug, ttl_days=cache_ttl_days)
        if cached is not None:
            logger.debug("Cache hit for slug '{}'", slug)
            return ScoreResult(
                title=slug.replace("-", " ").title(),
                slug=slug,
                metascore=cached["metascore"],
                metascore_review_count=cached["metascore_reviews"],
                user_score=cached["user_score"],
                user_review_count=cached["user_reviews"],
                passed=False,
                genres=None,
                must_play=None,
                release_date=None,
            )

        url = f"https://www.metacritic.com/game/{slug}/"
        logger.debug("Fetching game page '{}'", url)

        try:
            resp = requests.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                allow_redirects=True,
            )
            if resp.status_code != 200:
                logger.debug("Game page '{}' returned status {}", url, resp.status_code)
                return None

            parsed = _parse_game_details(resp.content)
            if parsed is None:
                return None

            self._cache.set_game_detail(
                slug=slug,
                metascore=parsed.get("metascore"),
                metascore_reviews=parsed.get("metascore_reviews"),
                user_score=parsed.get("user_score"),
                user_reviews=parsed.get("user_reviews"),
            )

            return ScoreResult(
                title=slug.replace("-", " ").title(),
                slug=slug,
                metascore=parsed.get("metascore"),
                metascore_review_count=parsed.get("metascore_reviews"),
                user_score=parsed.get("user_score"),
                user_review_count=parsed.get("user_reviews"),
                passed=False,
                genres=parsed.get("genres"),
                must_play=parsed.get("must_play"),
                release_date=parsed.get("release_date"),
            )

        except requests.RequestException as exc:
            logger.warning("Failed to fetch game page '{}': {}", url, exc)
            return None

    def _scan_browse_pages(
        self,
        title: str,
        platform: str,
        browse_cache_ttl_hours: int,
        cache_ttl_days: int,
    ) -> ScoreResult | None:
        normalized_title = _normalise_for_compare(title)

        for page_number in range(1, 11):
            logger.debug("Scanning browse page {} for '{}'", page_number, title)
            games = self._fetch_browse_page(platform, page_number, browse_cache_ttl_hours)
            if not games:
                break
            slug = self._match_game_on_page(games, normalized_title)
            if slug is not None:
                logger.info("Found matching game on browse page {}", page_number)
                return self._try_direct_slug(slug, cache_ttl_days)

        logger.info("Game '{}' not found on Metacritic browse pages", title)
        return None

    def _fetch_browse_page(self, platform: str, page_number: int, browse_cache_ttl_hours: int) -> list[dict] | None:
        """Return game listings for a browse page from cache or HTTP."""
        cached = self._cache.get_browse_page(platform, page_number, ttl_hours=browse_cache_ttl_hours)
        if cached is not None:
            return cached
        url = (
            f"https://www.metacritic.com/browse/game/{platform}/all/all-time/new/"
            f"?releaseYearMin=1958&releaseYearMax=2035"
            f"&platform={platform}&page={page_number}"
        )
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                allow_redirects=True,
            )
            if resp.status_code != 200:
                return None
            parsed = _parse_browse_page(resp.content)
            if parsed is not None:
                self._cache.set_browse_page(platform, page_number, parsed)
            return parsed
        except requests.RequestException as exc:
            logger.warning("Failed to fetch browse page '{}': {}", url, exc)
            return None

    def _match_game_on_page(self, games: list[dict], normalized_title: str) -> str | None:
        """Search browse page for matching title, returning slug or None."""
        for game in games:
            game_title = game.get("title")
            if game_title and _normalise_for_compare(str(game_title)) == normalized_title:
                return str(game.get("slug", ""))
        return None


def _normalise_for_compare(text: str) -> str:
    text = text.lower().strip()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()
