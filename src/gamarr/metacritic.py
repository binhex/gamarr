"""Metacritic score lookup for gamarr.

Adapted from gamecritic's Nuxt JSON scraping approach.  Looks up a game
title by first trying a direct slug URL, then falling back to browse
page scanning.
"""

from __future__ import annotations

import datetime
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


def _extract_user_review_count_from_summary(page_data: list[Any], item: dict[str, Any]) -> int | None:
    """Extract user review count from a user-review-summary Nuxt item.

    The user review count is NOT inside the ``userScore`` sub-dict
    (which is just ``{"score": <index>}``). It lives in a separate
    user review summary item that has ``"score"`` + ``"reviewCount"``
    at the top level, where the resolved score is a **float**
    (user rating), distinguishing it from critic score summaries
    whose resolved scores are integers.

    Returns the review count, or ``None`` if this item is not a
    user review summary.
    """
    if "score" not in item or "reviewCount" not in item:
        return None
    sv = _nuxt_val(page_data, item["score"])
    if isinstance(sv, (int, float)):
        # Verify this is a user review summary, not a critic summary.
        # User summaries have a URL containing "/user-reviews/".
        url = _nuxt_val(page_data, item.get("url"))
        if isinstance(url, str) and "/user-reviews/" in url:
            count = _nuxt_val(page_data, item["reviewCount"])
            return int(count) if isinstance(count, (int, float)) else None
    return None


def _extract_metadata_from_item(
    page_data: list[Any], item: dict[str, Any]
) -> tuple[list[str] | None, bool | None, str | None, str | None]:
    """Extract genres, must_play, release_date, description from a Nuxt item.

    Returns a 4-tuple ``(genres, must_play, release_date, description)``.
    If the item does not contain ``"mustPlay"`` or ``"genres"``, all four
    elements are ``None``.
    """
    if "mustPlay" not in item or "genres" not in item:
        return (None, None, None, None)
    must_play = _nuxt_val(page_data, item.get("mustPlay"))
    genres_list = _nuxt_val(page_data, item.get("genres"))
    genres = None
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
    return (genres, must_play, release_date, description)


def _format_game_detail_result(
    metascore: float | None,
    metascore_reviews: int | None,
    user_score: float | None,
    user_reviews: int | None,
    genres: list[str] | None,
    must_play: bool | None,
    release_date: str | None,
    description: str | None,
) -> dict[str, Any] | None:
    """Format extracted fields into the result dict, or return None if nothing found."""
    if metascore is None and user_score is None:
        return None
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


def _scan_nuxt_items(
    page_data: list[Any],
) -> tuple[
    float | None,
    int | None,
    float | None,
    int | None,
    list[str] | None,
    bool | None,
    str | None,
    str | None,
]:
    """Scan all Nuxt items to extract critic scores, user scores, and metadata.

    Used as the fallback path when no slug is provided or slug-specific
    lookup doesn't find a matching game item.
    """
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
            user_score = us
            if usv is not None:
                user_reviews = usv

        if user_reviews is None:
            user_reviews = _extract_user_review_count_from_summary(page_data, item)

        if genres is None:
            genres, must_play, release_date, description = _extract_metadata_from_item(page_data, item)

    return metascore, metascore_reviews, user_score, user_reviews, genres, must_play, release_date, description


def _find_game_details_in_nuxt_data(page_data: list[Any], slug: str | None = None) -> dict[str, Any] | None:
    """Extract critic scores, user scores, and game details from Nuxt JSON.

    Args:
        page_data: The Nuxt JSON data array from a Metacritic game page.
        slug: When provided, only extract scores from the game item
            matching this slug.  This prevents picking up scores from
            unrelated games in the "similar games" carousel section
            that modern Metacritic pages embed.
    """
    # When a slug is known, try the slug-specific lookup first.
    if slug is not None:
        result = _find_game_details_by_slug(page_data, slug)
        if result is not None:
            return result
        # Fall through to scanning if slug lookup fails (e.g.
        # test data without a game-item structure).
        logger.debug("Slug '{}' lookup failed, falling back to unfiltered scan", slug)

    fields = _scan_nuxt_items(page_data)
    metascore, metascore_reviews, user_score, user_reviews = fields[:4]
    if metascore is not None or user_score is not None:
        return _format_game_detail_result(*fields)
    return None


def _find_game_item_by_slug(page_data: list[Any], slug: str) -> dict[str, Any] | None:
    """Find the Nuxt game item whose ``slug`` field matches *slug*."""
    for item in page_data:
        if not isinstance(item, dict):
            continue
        item_slug = _nuxt_val(page_data, item.get("slug"))
        if isinstance(item_slug, str) and item_slug == slug:
            return item
    return None


def _extract_critic_from_summary(page_data: list[Any], game_item: dict[str, Any]) -> tuple[float | None, int | None]:
    """Extract (metascore, metascore_reviews) from a game item's criticScoreSummary."""
    cs = _nuxt_val(page_data, game_item.get("criticScoreSummary"))
    if not isinstance(cs, dict):
        return None, None
    ms = _nuxt_val(page_data, cs.get("score"))
    metascore = float(ms) if isinstance(ms, (int, float)) else None
    rc = _nuxt_val(page_data, cs.get("reviewCount"))
    return metascore, int(rc) if isinstance(rc, (int, float)) else None


def _extract_user_score_from_game(page_data: list[Any], game_item: dict[str, Any]) -> float | None:
    """Extract user score from a game item's userScore sub-dict."""
    us = _nuxt_val(page_data, game_item.get("userScore"))
    if not isinstance(us, dict) or "score" not in us:
        return None
    us_score = _nuxt_val(page_data, us.get("score"))
    return float(us_score) if isinstance(us_score, (int, float)) else None


def _check_user_review_item(page_data: list[Any], item: Any, slug: str) -> tuple[int | None, float] | None:
    """Check if *item* is a user review summary for *slug*.

    Returns ``(review_count, user_score)`` if the item matches, or
    ``None`` if it doesn't.
    """
    if not isinstance(item, dict) or "score" not in item or "reviewCount" not in item:
        return None
    sv = _nuxt_val(page_data, item["score"])
    if not isinstance(sv, (int, float)):
        return None
    if slug not in str(_nuxt_val(page_data, item.get("url")) or "").split("/"):
        return None
    count = _nuxt_val(page_data, item["reviewCount"])
    return int(count) if isinstance(count, (int, float)) else None, float(sv)


def _resolve_user_review_summary(page_data: list[Any], slug: str) -> tuple[int | None, float | None]:
    """Find the user review summary for *slug*.

    Returns (user_review_count, user_score_if_not_already_found).
    The user review count is not inside the userScore sub-dict; it
    lives in a separate summary item whose URL contains the slug.
    """
    for item in page_data:
        result = _check_user_review_item(page_data, item, slug)
        if result is not None:
            return result
    return None, None


def _find_game_details_by_slug(page_data: list[Any], slug: str) -> dict[str, Any] | None:
    """Extract game details for the specific game matching *slug*.

    Finds the game item whose ``slug`` field matches *slug*, then
    follows its ``criticScoreSummary`` and ``userScore`` references
    for scores.  The user review count is found by scanning for the
    user review summary item whose ``url`` contains the slug.
    """
    game_item = _find_game_item_by_slug(page_data, slug)
    if game_item is None:
        return None

    metascore, metascore_reviews = _extract_critic_from_summary(page_data, game_item)
    user_score = _extract_user_score_from_game(page_data, game_item)
    user_reviews, us_override = _resolve_user_review_summary(page_data, slug)
    if user_score is None and us_override is not None:
        user_score = us_override

    genres, must_play, release_date, description = _extract_metadata_from_item(page_data, game_item)

    return _format_game_detail_result(
        metascore,
        metascore_reviews,
        user_score,
        user_reviews,
        genres,
        must_play,
        release_date,
        description,
    )


def _find_nuxt_scores_in_page(page_content: bytes, slug: str | None = None) -> dict[str, Any] | None:
    """Find and parse Nuxt JSON scores from a Metacritic game page.

    Args:
        page_content: Raw HTML bytes of the game page.
        slug: When provided, only extract scores for the game matching
            this slug, preventing cross-game score pollution from the
            "similar games" section.
    """
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
        result = _find_game_details_in_nuxt_data(page_data, slug=slug)
        if result is not None:
            return result
    return None


def _parse_game_details(page_content: bytes, slug: str | None = None) -> dict[str, Any] | None:
    """Parse Metacritic game page, extracting scores and metadata.

    Args:
        page_content: Raw HTML bytes of the game page.
        slug: When provided, only extract scores for the game matching
            this slug.
    """
    return _find_nuxt_scores_in_page(page_content, slug=slug)


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


def _resolve_release_date(nuxt_data: list[Any], game: dict[str, Any]) -> str | None:
    """Resolve the release date from a browse-page Nuxt game item.

    The Nuxt ``releaseDate`` may be an index reference, a date string, or
    ``None``.  Returns a ``YYYY-MM-DD`` string if the value is valid,
    or ``None`` for missing, unparseable, or non-string values.
    """
    raw = _nuxt_val(nuxt_data, game.get("releaseDate"))
    if not isinstance(raw, str):
        return None
    # Validate the date format before returning
    try:
        datetime.datetime.strptime(raw, "%Y-%m-%d")
        return raw
    except (ValueError, TypeError):
        return None


def _resolve_browse_game_list(nuxt_data: list[Any], game_items: list[int]) -> list[dict[str, Any]]:
    """Resolve game dicts from Nuxt data by following item indices.

    Note: ``reviewCount`` values in the browse-page Nuxt data are plain
    integers (not index references), so they are NOT passed through
    ``_nuxt_val``.  This differs from the detail-page path in
    ``_extract_critic_from_summary`` where the data layout is different.
    """
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
                    "release_date": _resolve_release_date(nuxt_data, game),
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
        direct_only: bool = False,
        slug: str | None = None,
    ) -> ScoreResult | None:
        """Look up Metacritic scores for a game by title.

        Args:
            title: The game title to search for (used for slug generation
                when *slug* is ``None``).
            platform: The platform identifier (default ``"pc"``).
            cache_ttl_days: TTL in days for game detail cache.
            browse_cache_ttl_hours: TTL in hours for browse page cache.
            direct_only: When ``True``, skip the slow browse-page fallback
                and only check the direct slug.  Use this when the caller
                already knows the game exists on Metacritic (e.g., from
                a prior ``scan_recent_games`` call) and only needs the
                real detail-page scores.
            slug: Optional explicit Metacritic slug.  When provided,
                *title* is only used for logging and the slug is used
                directly for the detail-page lookup.

        Returns:
            A :class:`ScoreResult` if found, or ``None`` if the game
            could not be located on Metacritic.
        """
        if slug is None:
            slug = _make_slug(title)

        result = self._try_direct_slug(slug, cache_ttl_days)
        if result is not None:
            return result

        if direct_only:
            return None

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
                description=None,
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

            parsed = _parse_game_details(resp.content, slug=slug)
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
                description=parsed.get("description"),
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
        scanned = 0

        for page_number in range(1, 11):
            logger.debug("Scanning browse page {} for '{}'", page_number, title)
            games = self._fetch_browse_page(platform, page_number, browse_cache_ttl_hours)
            if not games:
                break
            scanned += 1
            slug = self._match_game_on_page(games, normalized_title)
            if slug is not None:
                logger.info("Found matching game on browse page {}", page_number)
                return self._try_direct_slug(slug, cache_ttl_days)

        logger.info(
            "Game '{}' not on Metacritic detail page (slug resolution failed, scanned {} browse page{})",
            title,
            scanned,
            "s" if scanned != 1 else "",
        )
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

    def scan_recent_games(
        self, platform: str, *, max_pages: int = 10, browse_cache_ttl_hours: int = 4
    ) -> list[dict[str, Any]]:
        """Return all games from Metacritic browse pages.

        Stops early when a page returns fewer items than expected
        (signalling the end of the catalog).

        Returns a list of game dicts with keys ``title``, ``slug``,
        ``score``, ``critic_review_count``, ``user_rating``,
        ``user_review_count``.
        """
        all_games: list[dict[str, Any]] = []
        for page_number in range(1, max_pages + 1):
            games = self._fetch_browse_page(platform, page_number, browse_cache_ttl_hours)
            if not games:
                break
            all_games.extend(games)
        return all_games


def _normalise_for_compare(text: str) -> str:
    text = text.lower().strip()
    # Remove both ASCII punctuation and Unicode dashes that can appear
    # in FitGirl titles (e.g. en-dash U+2013, em-dash U+2014).
    remove_chars = string.punctuation + "\u2013\u2014"
    text = text.translate(str.maketrans("", "", remove_chars))
    return re.sub(r"\s+", " ", text).strip()
