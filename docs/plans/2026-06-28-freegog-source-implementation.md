# FreeGOGPCGames Download Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add FreeGOGPCGames.com as a second download source alongside FitGirl, with configurable priority, same caching mechanism, and incremental indexing.

**Architecture:** New `FreeGOGSource` class in `src/gamarr/sources/freegog.py` implementing the existing `BaseSource` protocol. Uses the shared `source_titles` and `sitemap_cache` database tables. Registered in `_source_factories` alongside `FitGirlSource`. No pipeline changes beyond registration — the existing per-source loop handles everything.

**Tech Stack:** Python 3.12, requests, BeautifulSoup (already a dependency), SQLAlchemy (shared Database), pytest, unittest.mock

---

### Task 1: FreeGOGSource class skeleton + test file

**Files:**
- Create: `src/gamarr/sources/freegog.py`
- Create: `tests/unit/test_freegog.py`

- [ ] **Step 1: Write the protocol conformance test**

```python
"""Tests for gamarr FreeGOGPCGames download source."""

from __future__ import annotations

from gamarr.database import Database
from gamarr.sources import BaseSource
from gamarr.sources.freegog import FreeGOGSource


class TestFreeGOGSource:
    """FreeGOGSource construction and protocol conformance."""

    def test_implements_base_source(self) -> None:
        source = FreeGOGSource(db_path=":memory:")
        assert isinstance(source, BaseSource)

    def test_source_name(self) -> None:
        source = FreeGOGSource(db_path=":memory:")
        assert source.source_name == "freegog"

    def test_platform(self) -> None:
        source = FreeGOGSource(platform="pc", db_path=":memory:")
        assert source.platform == "pc"

    def test_accepts_shared_database(self) -> None:
        shared_db = Database(":memory:")
        source = FreeGOGSource(platform="pc", db=shared_db)
        assert source._db is shared_db
        source.close()
        shared_db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_freegog.py::TestFreeGOGSource -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gamarr.sources.freegog'`

- [ ] **Step 3: Write minimal class skeleton**

```python
"""FreeGOGPCGames download source for gamarr.

Indexes games from the FreeGOGPCGames A-Z game list, pre-fetches
base64-encoded magnet links from individual game pages, and stores
results in the shared source_titles database table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gamarr.database import Database

if TYPE_CHECKING:
    import threading


class FreeGOGSource:
    """FreeGOGPCGames download source.

    Args:
        platform: Platform identifier (default ``"pc"``).
        db_path: Path for the deduplication database.
            ``":memory:"`` uses an in-memory SQLite DB.
        db: Existing Database instance to share.
        cache_pages_hours: Cache TTL in hours for the A-Z index page.
    """

    def __init__(
        self,
        platform: str = "pc",
        db_path: str = ":memory:",
        db: Database | None = None,
        cache_pages_hours: int = 6,
    ) -> None:
        self._platform = platform
        self._cache_pages_hours = cache_pages_hours

        if db is not None:
            self._db = db
        else:
            self._db = Database(db_path)

    @property
    def source_name(self) -> str:
        """Return ``"freegog"`` as the source identifier."""
        return "freegog"

    @property
    def platform(self) -> str:
        """Return the platform this source targets."""
        return self._platform

    def fetch_sitemap(
        self, db: Database, cancel_event: threading.Event | None = None
    ) -> None:
        """Fetch the FreeGOG A-Z game list and rebuild the source_titles index.

        Results are cached in ``sitemap_cache`` for ``cache_pages_hours``.
        Only visits individual game pages for newly discovered entries
        (incremental indexing).  Checks *cancel_event* at entry.

        Args:
            db: The database instance to store results in.
            cancel_event: Optional event to signal cancellation.
        """
        pass

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_freegog.py::TestFreeGOGSource -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/sources/freegog.py tests/unit/test_freegog.py
git commit -m "feat: add FreeGOGSource class skeleton"
```

---

### Task 2: Title cleaning function

**Files:**
- Modify: `src/gamarr/sources/freegog.py` (add `_clean_freegog_title`)
- Modify: `tests/unit/test_freegog.py` (add test class)

- [ ] **Step 1: Write the title cleaning tests**

Add this test class to `tests/unit/test_freegog.py`:

```python
class TestCleanFreeGOGTitle:
    """FreeGOG title cleaning."""

    def test_simple_title_no_suffix(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        assert _clean_freegog_title("Elden Ring") == "Elden Ring"

    def test_strips_version_suffix(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        assert _clean_freegog_title("Gothic 1 Remake v1.0.2a") == "Gothic 1 Remake"

    def test_strips_version_and_dlc(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Elden Ring v1.12 + DLCs")
        assert result == "Elden Ring"

    def test_strips_edition_version_and_dlc(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Sea of Stars: Sunset Edition v3.0.60151 +3DLC")
        assert result == "Sea of Stars"

    def test_strips_simple_version(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        assert _clean_freegog_title("Blades of Fire v2.0.0.10") == "Blades of Fire"

    def test_strips_year_tag(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Kena: Bridge of Spirits 2022(rc3)")
        assert result == "Kena: Bridge of Spirits"

    def test_preserves_colon_in_name(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Banishers: Ghosts of New Eden v1.5.0.0 +DLC")
        assert result == "Banishers: Ghosts of New Eden"

    def test_edition_with_en_dash(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        result = _clean_freegog_title("Cyberpunk 2077 \u2013 Phantom Liberty Edition v2.1 + DLC")
        assert result == "Cyberpunk 2077"

    def test_already_clean_title_unchanged(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        assert _clean_freegog_title("Hades II") == "Hades II"

    def test_empty_string(self) -> None:
        from gamarr.sources.freegog import _clean_freegog_title

        assert _clean_freegog_title("") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_freegog.py::TestCleanFreeGOGTitle -v`
Expected: FAIL — `ImportError: cannot import name '_clean_freegog_title'`

- [ ] **Step 3: Write the title cleaning function**

Add to `src/gamarr/sources/freegog.py` (before the `FreeGOGSource` class):

```python
import re

# Strip edition suffixes after en-dash, colon, or comma.
# Reuses the pattern structure from fitgirl.py's _EDITION_PATTERN.
_EDITION_PATTERN = re.compile(
    r"(?:\s*[-–—]\s*|\s*:\s*|,\s*)(?:"
    r"(?:Digital\s+)?Deluxe\s+Edition|"
    r"Complete\s+Edition|Enhanced\s+Edition|Essence\s+Edition|"
    r"Definitive\s+Edition|Anniversary\s+Edition|Legendary\s+Edition|"
    r"Game\s+of\s+the\s+Year\s+Edition|"
    r"Gold\s+Edition|Platinum\s+Edition|Ultimate\s+Edition|"
    r"Premium\s+Edition|Collectors?(?:'s)?\s+Edition|"
    r"Limited\s+Edition|Special\s+Edition|Standard\s+Edition|"
    r"Phantom\s+Liberty\s+Edition|Sunset\s+Edition|"
    r"GOTY(?:\s+Edition)?|Game\s+of\s+the\s+Year(?:\s+Edition)?)"
    r"\b(?=\s*[,\d–—-]|\s*$)",
    re.IGNORECASE,
)

# Strip version numbers: v1.0, v1.2.3a, v3.0.60151
_VERSION_PATTERN = re.compile(r"\s+v\d[\d.]*[a-z]?\b", re.IGNORECASE)

# Strip DLC counts: +3DLC, + DLCs, +2DLC
_DLC_PATTERN = re.compile(r"\s*\+\s*\d*\s*DLCs?\b", re.IGNORECASE)

# Strip year tags: 2022(rc3), 2026
_YEAR_PATTERN = re.compile(r"\s+\d{4}(?:\(rc\d+\))?\b")


def _clean_freegog_title(raw_title: str) -> str:
    """Strip version, DLC, edition, and year metadata from a FreeGOG title.

    Args:
        raw_title: Raw title from the A-Z list, e.g.
            ``"Gothic 1 Remake v1.0.2a"`` or
            ``"Sea of Stars: Sunset Edition v3.0.60151 +3DLC"``.

    Returns:
        Cleaned game name, e.g. ``"Gothic 1 Remake"``.
    """
    title = raw_title.strip()
    title = _EDITION_PATTERN.sub("", title).strip()
    title = _DLC_PATTERN.sub("", title).strip()
    title = _VERSION_PATTERN.sub("", title).strip()
    title = _YEAR_PATTERN.sub("", title).strip()
    return title
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_freegog.py::TestCleanFreeGOGTitle -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/sources/freegog.py tests/unit/test_freegog.py
git commit -m "feat: add _clean_freegog_title with version/DLC/edition stripping"
```

---

### Task 3: A-Z page HTML parsing

**Files:**
- Modify: `src/gamarr/sources/freegog.py` (add `_parse_freegog_az_page`)
- Modify: `tests/unit/test_freegog.py` (add test class)

- [ ] **Step 1: Write the parsing tests**

```python
class TestParseFreeGOGAZPage:
    """A-Z game list HTML parsing."""

    def test_parses_multiple_entries(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_page

        html = """<html><body>
        <div class="gd-az-letter-section">
          <a href="https://freegogpcgames.com/33511/gothic-1-remake/">Gothic 1 Remake v1.0.2a</a>
          <a href="https://freegogpcgames.com/34067/the-necromancers-tale/">The Necromancer's Tale v1.235</a>
          <a href="https://freegogpcgames.com/2588/a-hat-in-time/">A Hat in Time v1.0</a>
        </div>
        </body></html>"""

        results = _parse_freegog_az_page(html)
        assert len(results) == 3
        assert results[0] == {
            "title": "Gothic 1 Remake",
            "url": "https://freegogpcgames.com/33511/gothic-1-remake/",
        }
        assert results[1]["title"] == "The Necromancer's Tale"
        assert results[2]["title"] == "A Hat in Time"

    def test_parses_empty_page(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_page

        results = _parse_freegog_az_page("<html></html>")
        assert results == []

    def test_uses_cleaned_title(self) -> None:
        from gamarr.sources.freegog import _parse_freegog_az_page

        html = '<a href="https://freegogpcgames.com/1/game/">Game Name v1.0 +3DLC</a>'
        results = _parse_freegog_az_page(html)
        assert results[0]["title"] == "Game Name"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_freegog.py::TestParseFreeGOGAZPage -v`
Expected: FAIL — `ImportError: cannot import name '_parse_freegog_az_page'`

- [ ] **Step 3: Write the parser**

Add to `src/gamarr/sources/freegog.py` (after the cleaning function, before the class):

```python
import re  # already imported — ensure at top of file

# Base URL for constructing absolute URLs if needed
_FREEGOG_BASE = "https://freegogpcgames.com"

# Pattern to extract game links from the A-Z page.
# Matches <a href="/NNNNN/slug/">Title vX.Y</a> within gd-az-letter-section divs.
_AZ_LINK_PATTERN = re.compile(
    r'<a\s+href="(https://freegogpcgames\.com/\d+/[^"]+/)">([^<]+)</a>',
)


def _parse_freegog_az_page(html: str) -> list[dict[str, str]]:
    """Parse the FreeGOG A-Z game list HTML into title/URL pairs.

    Extracts all game links, cleans each title with
    ``_clean_freegog_title``, and returns deduplicated results.

    Args:
        html: Raw HTML of the /game-list/ page.

    Returns:
        List of ``{"title": ..., "url": ...}`` dicts.
    """
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    for match in _AZ_LINK_PATTERN.finditer(html):
        url = match.group(1)
        if url in seen:
            continue
        seen.add(url)
        raw_title = match.group(2).strip()
        clean = _clean_freegog_title(raw_title)
        if clean:
            results.append({"title": clean, "url": url})

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_freegog.py::TestParseFreeGOGAZPage -v`
Expected: 3 passed

- [ ] **Step 5: Run all freegog tests to confirm no regressions**

Run: `uv run pytest tests/unit/test_freegog.py -v`
Expected: 17 passed (4 source + 10 cleaning + 3 parsing)

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/sources/freegog.py tests/unit/test_freegog.py
git commit -m "feat: add FreeGOG A-Z page HTML parser"
```

---

### Task 4: Base64 magnet decoding

**Files:**
- Modify: `src/gamarr/sources/freegog.py` (add `_extract_magnet_from_freegog_page`)
- Modify: `tests/unit/test_freegog.py` (add test class)

- [ ] **Step 1: Write the magnet extraction tests**

```python
class TestExtractMagnetFromFreeGOGPage:
    """Base64 magnet link extraction from FreeGOG game pages."""

    def test_extracts_and_decodes_magnet(self) -> None:
        from gamarr.sources.freegog import _extract_magnet_from_freegog_page

        # Real magnet from Gothic 1 Remake (base64):
        # magnet:?xt=urn:btih:42788AFB107143488CB576085BD5A5DEF165FC37&tr=...
        encoded = (
            "bWFnbmV0Oj94dD11cm46YnRpaDo0Mjc4OEFGQjEwNzE0MzQ4OENCNTc2MDg1QkQ1"
            "QTVERUYxNjVGQzM3JnRyPWh0dHAlM0ElMkYlMkZidDMudC1ydS5vcmclMkZhbm4l"
            "M0ZtYWduZXQmZG49JTVCREwlNUQlMjBHb3RoaWMlMjAxJTIwUmVtYWtl"
        )
        html = (
            '<a href="https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.'
            + encoded
            + '.signature" data-type="magnet">Gothic 1 Remake v1.0 [GOG]</a>'
        )

        result = _extract_magnet_from_freegog_page(html)
        assert result is not None
        assert result.startswith("magnet:?xt=urn:btih:")
        assert "42788AFB107143488CB576085BD5A5DEF165FC37" in result

    def test_returns_none_when_no_magnet(self) -> None:
        from gamarr.sources.freegog import _extract_magnet_from_freegog_page

        result = _extract_magnet_from_freegog_page("<html>no magnet here</html>")
        assert result is None

    def test_returns_none_for_empty_html(self) -> None:
        from gamarr.sources.freegog import _extract_magnet_from_freegog_page

        result = _extract_magnet_from_freegog_page("")
        assert result is None

    def test_returns_none_when_base64_decode_fails(self) -> None:
        from gamarr.sources.freegog import _extract_magnet_from_freegog_page

        html = (
            '<a href="https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.not-valid-base64!!!'
            '.sig" data-type="magnet">Bad</a>'
        )
        result = _extract_magnet_from_freegog_page(html)
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_freegog.py::TestExtractMagnetFromFreeGOGPage -v`
Expected: FAIL — `ImportError: cannot import name '_extract_magnet_from_freegog_page'`

- [ ] **Step 3: Write the magnet extractor**

Add to `src/gamarr/sources/freegog.py` (after the parser, before the class):

```python
import base64  # add to top imports
import re

# Pattern to extract the v1.<base64> portion from gateway URLs.
_MAGNET_URL_PATTERN = re.compile(
    r'<a\b[^>]*\bdata-type="magnet"[^>]*\bhref="[^"]*\burl=v1\.([A-Za-z0-9+/=_-]+)'
)


def _extract_magnet_from_freegog_page(html: str) -> str | None:
    """Extract and decode the first magnet link from a FreeGOG game page.

    Magnet links are base64-encoded inside a gateway URL in the
    ``<noscript>`` block, marked with ``data-type="magnet"``.

    Args:
        html: Raw HTML of a FreeGOG game page.

    Returns:
        The decoded magnet URI, or ``None`` if no valid magnet found.
    """
    match = _MAGNET_URL_PATTERN.search(html)
    if not match:
        return None

    encoded = match.group(1)
    # The v1 prefix uses standard base64 with URL-safe variants.
    # Pad to multiple of 4 for base64 decode.
    try:
        encoded = encoded.replace("-", "+").replace("_", "/")
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding
        decoded = base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception:
        return None

    if decoded.startswith("magnet:"):
        return decoded
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_freegog.py::TestExtractMagnetFromFreeGOGPage -v`
Expected: 4 passed

- [ ] **Step 5: Run all freegog tests**

Run: `uv run pytest tests/unit/test_freegog.py -v`
Expected: 21 passed

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/sources/freegog.py tests/unit/test_freegog.py
git commit -m "feat: add FreeGOG base64 magnet decoder"
```

---

### Task 5: fetch_sitemap — incremental indexing with cache

**Files:**
- Modify: `src/gamarr/sources/freegog.py` (implement `fetch_sitemap` + `_index_az_page`)
- Modify: `tests/unit/test_freegog.py` (add integration tests)

- [ ] **Step 1: Write the fetch_sitemap integration tests**

```python
class TestFreeGOGFetchSitemap:
    """fetch_sitemap integration tests — indexing, caching, skipping."""

    def test_fetch_sitemap_indexes_new_games(self, tmp_path: Path) -> None:
        """fetch_sitemap should fetch A-Z page, parse titles, and store them."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=6)

        # Mock the A-Z page with two game links.
        az_html = (
            '<div class="gd-az-letter-section">'
            '<a href="https://freegogpcgames.com/1/elden-ring/">Elden Ring v1.12 + DLCs</a>'
            '<a href="https://freegogpcgames.com/2/game-two/">Game Two v3.0.60151 +3DLC</a>'
            "</div>"
        )
        # Mock individual game page responses (each with a magnet).
        game_html = (
            '<a href="https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.'
            "bWFnbmV0Oj94dD11cm46YnRpaDphYmMxMjM.dummy"
            '" data-type="magnet">Game v1.0 [GOG]</a>'
        )

        with patch("gamarr.sources.freegog.requests.get") as mock_get:

            def side_effect(url: str, **kwargs: object) -> MagicMock:
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                if "game-list" in url:
                    resp.text = az_html
                else:
                    resp.text = game_html
                return resp

            mock_get.side_effect = side_effect
            source.fetch_sitemap(db)

        # Both games should be indexed with cleaned titles and magnets.
        titles = db.get_all_source_titles("freegog")
        assert len(titles) == 2, f"Expected 2 titles, got {len(titles)}"
        game_titles = {t["title"] for t in titles}
        assert "Elden Ring" in game_titles
        assert "Game Two" in game_titles

        source.close()
        db.close()

    def test_fetch_sitemap_skips_known_games(self, tmp_path: Path) -> None:
        """fetch_sitemap should skip games already in source_titles (incremental)."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        # Pre-populate one known game.
        db.rebuild_source_titles(
            "freegog",
            [{"title": "Elden Ring", "url": "https://freegogpcgames.com/1/elden-ring/", "magnet": "magnet:?xt=urn:btih:abc"}],
        )

        # A-Z page with the known game + one new game.
        az_html = (
            '<div class="gd-az-letter-section">'
            '<a href="https://freegogpcgames.com/1/elden-ring/">Elden Ring v1.12</a>'
            '<a href="https://freegogpcgames.com/2/game-two/">Game Two v1.0</a>'
            "</div>"
        )
        game_html = (
            '<a href="https://gdl.freegogpcgames.xyz/download-gen.php?url=v1.'
            "bWFnbmV0Oj94dD11cm46YnRpaDphYmMxMjM.dummy"
            '" data-type="magnet">Game v1.0 [GOG]</a>'
        )

        with patch("gamarr.sources.freegog.requests.get") as mock_get:

            def side_effect(url: str, **kwargs: object) -> MagicMock:
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                if "game-list" in url:
                    resp.text = az_html
                else:
                    resp.text = game_html
                return resp

            mock_get.side_effect = side_effect
            source.fetch_sitemap(db)

        # Only the new game should be added. The known game already existed.
        titles = db.get_all_source_titles("freegog")
        assert len(titles) == 2
        # The known game should still be there (unchanged).
        known = [t for t in titles if t["title"] == "Elden Ring"]
        assert len(known) == 1

        source.close()
        db.close()

    def test_fetch_sitemap_cache_hit_skips(self, tmp_path: Path) -> None:
        """fetch_sitemap should skip entirely when cache is valid and titles exist."""
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=6)

        db.set_sitemap_cache("freegog")
        db.rebuild_source_titles(
            "freegog",
            [{"title": "Elden Ring", "url": "https://freegogpcgames.com/1/elden-ring/", "magnet": "magnet:?xt=urn:btih:abc"}],
        )

        with patch("gamarr.sources.freegog.requests.get") as mock_get:
            source.fetch_sitemap(db)
            mock_get.assert_not_called()

        source.close()
        db.close()

    def test_fetch_sitemap_handles_az_page_failure(self, tmp_path: Path) -> None:
        """fetch_sitemap should log warning and update cache on A-Z page failure."""
        from unittest.mock import patch

        import requests as requests_lib

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=6)

        # Pre-populate titles so we have something.
        db.rebuild_source_titles(
            "freegog",
            [{"title": "Elden Ring", "url": "https://freegogpcgames.com/1/elden-ring/", "magnet": None}],
        )

        with (
            patch("gamarr.sources.freegog.requests.get", side_effect=requests_lib.exceptions.ConnectionError("offline")),
            patch("gamarr.sources.freegog.logger") as mock_logger,
        ):
            source.fetch_sitemap(db)

        # Should log the failure.
        mock_logger.warning.assert_called_once()
        assert "Failed to fetch" in mock_logger.warning.call_args[0][0]
        # Cache should be refreshed (prevents retry loop).
        assert db.get_sitemap_cache("freegog", 6)

        source.close()
        db.close()

    def test_fetch_sitemap_skips_on_cancel(self, tmp_path: Path) -> None:
        """fetch_sitemap returns early when cancel_event is set."""
        import threading
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.sources.freegog import FreeGOGSource

        db = Database(str(tmp_path / "test.db"))
        source = FreeGOGSource(db=db, cache_pages_hours=0)

        cancel_event = threading.Event()
        cancel_event.set()

        with patch("gamarr.sources.freegog.requests.get") as mock_get:
            source.fetch_sitemap(db, cancel_event=cancel_event)
            mock_get.assert_not_called()

        source.close()
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_freegog.py::TestFreeGOGFetchSitemap -v`
Expected: All FAIL — `fetch_sitemap` is a no-op stub

- [ ] **Step 3: Implement fetch_sitemap and _index_az_page**

Replace the `fetch_sitemap` stub in `FreeGOGSource` with:

```python
import requests
import urllib3
from loguru import logger

# FreeGOG uses standard HTTPS, but we disable warnings for consistency.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_AZ_LIST_URL = "https://freegogpcgames.com/game-list/"


class FreeGOGSource:
    # ... __init__, source_name, platform unchanged ...

    def fetch_sitemap(
        self, db: Database, cancel_event: threading.Event | None = None
    ) -> None:
        """Fetch the FreeGOG A-Z game list and rebuild the source_titles index."""
        if cancel_event is not None and cancel_event.is_set():
            logger.debug("FreeGOG sitemap fetch skipped — cancelled")
            return

        if self._cache_pages_hours > 0 and db.get_sitemap_cache(
            "freegog", self._cache_pages_hours
        ):
            if len(db.get_all_source_titles("freegog")) > 0:
                logger.info(
                    "FreeGOG cache is still valid (TTL: {} hours) — skipping fetch",
                    self._cache_pages_hours,
                )
                return
            logger.info(
                "FreeGOG cache is valid but no titles indexed — re-indexing",
            )

        self._index_az_page(db)

    def _index_az_page(self, db: Database) -> None:
        """Fetch the A-Z page and incrementally index new games."""
        try:
            resp = requests.get(
                _AZ_LIST_URL,
                timeout=30,
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            entries = _parse_freegog_az_page(resp.text)
        except requests.RequestException as exc:
            logger.warning("Failed to fetch FreeGOG A-Z page: {}", exc)
            db.set_sitemap_cache("freegog")
            return

        # Get currently known URLs to skip already-indexed games.
        existing_urls: set[str] = {
            t["url"] for t in db.get_all_source_titles("freegog")
        }

        new_count = 0
        skipped = 0
        for entry in entries:
            if entry["url"] in existing_urls:
                skipped += 1
                continue

            magnet: str | None = None
            try:
                resp = requests.get(
                    entry["url"],
                    timeout=30,
                    headers={"User-Agent": _USER_AGENT},
                )
                resp.raise_for_status()
                magnet = _extract_magnet_from_freegog_page(resp.text)
            except requests.RequestException as exc:
                logger.warning(
                    "Failed to fetch FreeGOG game page '{}': {}",
                    entry["url"],
                    exc,
                )
                # Don't store this entry — it'll be retried next cycle
                # since its URL won't be in source_titles yet.
                continue

            # Store the entry.  Use individual INSERT (not rebuild)
            # to preserve existing indexed games.
            db._store_source_title(
                source="freegog",
                title=entry["title"],
                url=entry["url"],
                magnet=magnet,
            )
            new_count += 1

        db.set_sitemap_cache("freegog")
        logger.info(
            "FreeGOG indexed {} new games ({} skipped, already known)",
            new_count,
            skipped,
        )
```

- [ ] **Step 4: Add _store_source_title method to Database**

The `_index_az_page` method needs `_store_source_title` on the Database class.  Add to `src/gamarr/database.py`:

```python
def _store_source_title(
    self, *, source: str, title: str, url: str, magnet: str | None
) -> None:
    """Insert a single source title entry (used for incremental indexing)."""
    with self._session() as session:
        session.add(
            SourceTitle(
                source=source,
                title=title,
                url=url,
                magnet=magnet,
            )
        )
        session.commit()
```

- [ ] **Step 5: Run fetch_sitemap tests**

Run: `uv run pytest tests/unit/test_freegog.py::TestFreeGOGFetchSitemap -v`
Expected: 5 passed (may need iteration — fix any issues)

- [ ] **Step 6: Run all freegog tests**

Run: `uv run pytest tests/unit/test_freegog.py -v`
Expected: 26 passed

- [ ] **Step 7: Run full test suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add src/gamarr/sources/freegog.py src/gamarr/database.py tests/unit/test_freegog.py
git commit -m "feat: implement FreeGOG fetch_sitemap with incremental indexing and caching"
```

---

### Task 6: Config default + factory registration

**Files:**
- Modify: `src/gamarr/config.py` (change default download_sites order)
- Modify: `src/gamarr/pipeline.py` (add freegog to _source_factories, update _build_source)

- [ ] **Step 1: Update config defaults**

In `src/gamarr/config.py`, change the `DownloadSitesConfig.root` default list to put FreeGOG first:

```python
class DownloadSitesConfig(RootModel[list[SourceConfigEntry]]):
    """Ordered list of download source configurations.

    Position in the list defines priority: earlier = higher priority.
    """

    root: list[SourceConfigEntry] = [
        SourceConfigEntry(name="freegog"),
        SourceConfigEntry(name="fitgirl", feed_url="https://fitgirl-repacks.site/feed/"),
    ]
```

- [ ] **Step 2: Register FreeGOG in the source factory**

In `src/gamarr/pipeline.py`, add the import and factory entry.  Find:

```python
from gamarr.sources.fitgirl import FitGirlSource
```

Add below:

```python
from gamarr.sources.freegog import FreeGOGSource
```

Then in `_source_factories`, add the new entry:

```python
_source_factories: dict[str, type] = {
    "fitgirl": FitGirlSource,
    "freegog": FreeGOGSource,
}
```

- [ ] **Step 3: Fix _build_source kwargs for freegog**

The `_build_source` function passes `feed_url` from the config entry.  FreeGOG doesn't have `feed_url`.  The existing code already handles this — `if entry.feed_url:` only adds the kwarg when present.  No code change needed, but verify:

```python
def _build_source(entry: Any, db: Database) -> Any:
    factory = _source_factories.get(entry.name.casefold())
    if factory is None:
        raise ValueError(f"Unknown source: {entry.name}")
    kwargs: dict[str, Any] = {
        "platform": entry.platform,
        "db": db,
        "cache_pages_hours": entry.cache_pages_hours,
    }
    if entry.feed_url:       # ← freegog has feed_url=None, so this is False — correct
        kwargs["feed_url"] = entry.feed_url
    return factory(**kwargs)
```

**Wait** — `FreeGOGSource.__init__` does NOT accept `feed_url`.  The factory would pass `feed_url=None` (since `entry.feed_url` is None and the `if` guard skips adding it), so the kwarg won't be present.  This is correct — `FreeGOGSource.__init__` only takes `platform`, `db_path`, `db`, `cache_pages_hours`.

- [ ] **Step 4: Run config tests to verify**

Run: `uv run pytest tests/unit/test_config.py -v -k "download_sites or source or default"`  
Expected: All matching tests pass.  The new default `download_sites` should have `freegog` first.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/config.py src/gamarr/pipeline.py
git commit -m "feat: register FreeGOGSource in factory, default config priority: freegog first"
```

---

### Task 7: Final verification

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest -q
```

Expected: all tests pass (should be ~530+ tests with the new ones)

- [ ] **Step 2: Run linter**

```bash
uv run ruff check --fix . && uv run ruff format . --check
```

Expected: All checks passed, all files formatted.

- [ ] **Step 3: Run type checker**

```bash
uv run mypy .
```

Expected: Success: no issues found.

- [ ] **Step 4: Run coverage check**

```bash
uv run pytest --cov=src/gamarr/sources/freegog --cov-report=term-missing
```

Expected: `freegog.py` at or above 95% line coverage.

- [ ] **Step 5: Git status check — verify only planned files changed**

```bash
git diff --name-only HEAD
```

Expected: Only files listed in this plan appear.

---

## Plan Self-Review

1. **Spec coverage:** Each spec section maps to a task — class skeleton (Task 1), title cleaning (Task 2), A-Z parsing (Task 3), base64 magnet (Task 4), incremental indexing + caching + error handling (Task 5), config + factory (Task 6), verification (Task 7). All spec requirements covered.
2. **Placeholder scan:** No TBDs, TODOs, or vague "add appropriate error handling" instructions. Every step has concrete code.
3. **Type consistency:** `FreeGOGSource.__init__` signature (`platform`, `db_path`, `db`, `cache_pages_hours`) matches factory usage in Task 6. `_store_source_title` signature (`source`, `title`, `url`, `magnet`) matches usage in Task 5.
