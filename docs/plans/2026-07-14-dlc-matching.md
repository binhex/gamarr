# DLC-Aware FitGirl Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance `_deep_search_article_body` to detect FitGirl repacks that cover DLCs
via "All DLCs" keywords in the page title or article body, so DLC games on Metacritic
match their parent-game repacks.

**Architecture:** Two new private helpers (`_page_title_has_dlc_keywords`, `_article_contains_all_dlcs`)
check the HTML `<title>` tag and article body for DLC-inclusion patterns. Both are called from
`_deep_search_article_body` after the page is fetched but before/instead of the full
article body normalisation when a quick text check suffices.

**Tech Stack:** Python, pytest, already-imported `re` module. No new dependencies.

---

### Task 1: Write `_page_title_has_dlc_keywords` tests

**Files:**
- Modify: `tests/unit/test_pipeline.py` (add 2 tests)
- Create: nothing

- [ ] **Step 1: Write the failing tests**

Add after the existing `_deep_search_article_body` test block.
Insert before the line that starts `class TestMatchPendingGames:`.

```python
class TestPageTitleHasDlcKeywords:
    """Tests for _page_title_has_dlc_keywords helper."""

    def test_has_dlc_keywords_all_dlcs(self) -> None:
        """Page title containing '+ All DLCs' returns True."""
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords(
            "Total War: WARHAMMER 2 – v1.9.2 + All DLCs [FitGirl Repack]"
        ) is True

    def test_has_dlc_keywords_counted_dlcs(self) -> None:
        """Page title containing '+ 15 DLCs' returns True."""
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords(
            "Game Name v1.0 + 15 DLCs + Bonuses [FitGirl Repack]"
        ) is True

    def test_has_dlc_keywords_no_dlcs(self) -> None:
        """Page title with no DLC mention returns False."""
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords(
            "Game Name v1.0 [FitGirl Repack]"
        ) is False

    def test_has_dlc_keywords_none_title(self) -> None:
        """None page title returns False."""
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords(None) is False

    def test_has_dlc_keywords_empty_title(self) -> None:
        """Empty page title returns False."""
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords("") is False

    def test_has_dlc_keywords_partial_match_avoided(self) -> None:
        """'+ All DLCsPECIAL' should NOT match (word boundary)."""
        from gamarr.pipeline import _page_title_has_dlc_keywords

        assert _page_title_has_dlc_keywords(
            "Game + All DLCsPECIAL Edition [FitGirl Repack]"
        ) is False
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
uv run pytest tests/unit/test_pipeline.py::TestPageTitleHasDlcKeywords -v
```

Expected: 6 failures with `ImportError: cannot import name '_page_title_has_dlc_keywords'`.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add failing tests for _page_title_has_dlc_keywords"
```

---

### Task 2: Implement `_page_title_has_dlc_keywords`

**Files:**
- Modify: `src/gamarr/pipeline.py` (add constant + function)

- [ ] **Step 1: Insert the module-level pattern constant**

Insert after the existing `_SITEMAP_TIMEOUT = 30.0` line.

```python
# Regex patterns for detecting DLC-inclusion keywords in the
# FitGirl repack page HTML <title> tag.
_PAGE_TITLE_DLC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\+\s*all\s+dlcs?\b", re.IGNORECASE),
    re.compile(r"\+\s*\d+\s+dlcs?\b", re.IGNORECASE),
]
```

- [ ] **Step 2: Insert the function**

Insert before the `_fetch_fitgirl_page_content` function.

```python
def _page_title_has_dlc_keywords(page_title: str | None) -> bool:
    """Check if the HTML <title> tag contains DLC-inclusion patterns.

    Scans the page title (already extracted by
    ``_fetch_fitgirl_page_content``) for patterns like ``"+ All DLCs"``
    or ``"+ 15 DLCs"``. Case-insensitive.

    Args:
        page_title: The unescaped HTML ``<title>`` tag content, or None.

    Returns:
        True if a DLC-inclusion pattern was found.
    """
    if not page_title:
        return False
    return any(pattern.search(page_title) for pattern in _PAGE_TITLE_DLC_PATTERNS)
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/unit/test_pipeline.py::TestPageTitleHasDlcKeywords -v
```

Expected: 6/6 pass.

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: add _page_title_has_dlc_keywords helper for DLC repack detection"
```

---

### Task 3: Write `_article_contains_all_dlcs` tests

**Files:**
- Modify: `tests/unit/test_pipeline.py` (add test class)

- [ ] **Step 1: Write the failing tests**

Add after `TestPageTitleHasDlcKeywords`, before `class TestMatchPendingGames:`.

```python
class TestArticleContainsAllDlcs:
    """Tests for _article_contains_all_dlcs helper."""

    def test_contains_all_dlcs_basic(self) -> None:
        """Article body with 'all DLCs' returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs(
            "This repack includes all DLCs released so far."
        ) is True

    def test_contains_all_dlcs_available(self) -> None:
        """Article body with 'all available DLCs' returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs(
            "All available DLCs are bundled in this release."
        ) is True

    def test_contains_all_dlcs_existing(self) -> None:
        """Article body with 'all existing DLCs' returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs(
            "Includes all existing DLCs and updates."
        ) is True

    def test_contains_all_dlcs_includes_all(self) -> None:
        """Article body with 'Includes all DLCs' returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs(
            "Includes all DLCs: Curse of the Vampire Coast, Rise of the Tomb Kings..."
        ) is True

    def test_contains_all_dlcs_casing(self) -> None:
        """Different casing ('ALL DLCS') returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs(
            "This pack includes ALL DLCS ever released."
        ) is True

    def test_contains_all_dlcs_no_match(self) -> None:
        """Article body with no DLC mention returns False."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs(
            "This repack is based on the latest Steam release v1.9.2."
        ) is False

    def test_contains_all_dlcs_none(self) -> None:
        """None article text returns False."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs(None) is False

    def test_contains_all_dlcs_empty(self) -> None:
        """Empty article text returns False."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs("") is False

    def test_contains_all_dlcs_partial_match_avoided(self) -> None:
        """'all dlcschemas' should NOT match (word boundary)."""
        from gamarr.pipeline import _article_contains_all_dlcs

        assert _article_contains_all_dlcs(
            "The installer validates all dlcschemas before extracting."
        ) is False
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
uv run pytest tests/unit/test_pipeline.py::TestArticleContainsAllDlcs -v
```

Expected: 9 failures with `ImportError: cannot import name '_article_contains_all_dlcs'`.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add failing tests for _article_contains_all_dlcs"
```

---

### Task 4: Implement `_article_contains_all_dlcs`

**Files:**
- Modify: `src/gamarr/pipeline.py` (add constant + function)

- [ ] **Step 1: Insert the module-level pattern constant**

Insert after the `_PAGE_TITLE_DLC_PATTERNS` constant added in Task 2.

```python
# Regex patterns for detecting "All DLCs" keyword variants in the
# FitGirl repack article body text.
_ALL_DLCS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\ball\s+dlcs?\b", re.IGNORECASE),
    re.compile(r"\ball\s+available\s+dlcs?\b", re.IGNORECASE),
    re.compile(r"\ball\s+existing\s+dlcs?\b", re.IGNORECASE),
    re.compile(r"\bincludes?\s+all\s+dlcs?\b", re.IGNORECASE),
]
```

- [ ] **Step 2: Insert the function**

Insert after the `_page_title_has_dlc_keywords` function, before `_fetch_fitgirl_page_content`.

```python
def _article_contains_all_dlcs(article_text: str | None) -> bool:
    """Check if the article body text contains "All DLCs" keyword patterns.

    Scans the article text (extracted from the ``<article>`` HTML element
    by ``_fetch_fitgirl_page_content``) for patterns like
    ``"all DLCs"``, ``"all available DLCs"``, ``"Includes all DLCs"``.
    Case-insensitive.

    Args:
        article_text: The article body text content, or None.

    Returns:
        True if an "All DLCs" keyword pattern was found.
    """
    if not article_text:
        return False
    return any(pattern.search(article_text) for pattern in _ALL_DLCS_PATTERNS)
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/unit/test_pipeline.py::TestArticleContainsAllDlcs -v
```

Expected: 9/9 pass.

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: add _article_contains_all_dlcs helper for 'All DLCs' body text detection"
```

---

### Task 5: Write integration tests for DLC-aware deep search

**Files:**
- Modify: `tests/unit/test_pipeline.py` (add test class)

- [ ] **Step 1: Write the failing integration tests**

Add after `TestArticleContainsAllDlcs`, before `class TestMatchPendingGames:`.

```python
class TestDeepSearchDlcMatching:
    """Integration tests for DLC-aware deep search via _deep_search_article_body."""

    def test_page_title_all_dlcs(self, tmp_path: Path) -> None:
        """When page <title> contains '+ All DLCs', deep search matches.

        The sitemap title (from URL slug) does NOT contain DLC keywords,
        but the page title does — the helper should detect it.
        """
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body

        # Install a sitemap entry for the base game
        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles("fitgirl", [
            {"title": "Total War Warhammer 2", "url": "https://fitgirl-repacks.site/total-war-warhammer-2/",
             "magnet": None},
        ])

        # Mock _fetch_fitgirl_page_content to return a page title with DLC keywords
        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Total War: WARHAMMER 2 – v1.9.2 + All DLCs [FitGirl Repack]",
                "Some body text.",
            ),
        ):
            result = _deep_search_article_body(db, "fitgirl",
                                               normalise_for_compare("Total War: WARHAMMER II - Curse of the Vampire Coast"))
        db.close()
        assert len(result) == 1, f"Expected 1 match, got {len(result)}: {result}"

    def test_page_title_counted_dlcs(self, tmp_path: Path) -> None:
        """Page <title> with '+ 15 DLCs' should match via _page_title_has_dlc_keywords."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles("fitgirl", [
            {"title": "Game Name", "url": "https://fitgirl-repacks.site/game-name/", "magnet": None},
        ])

        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=("Game Name v1.0 + 15 DLCs + Bonuses [FitGirl Repack]", None),
        ):
            result = _deep_search_article_body(db, "fitgirl",
                                               normalise_for_compare("Game Name - The Lost Chapters"))
        db.close()
        assert len(result) == 1

    def test_article_body_all_dlcs_fallback(self, tmp_path: Path) -> None:
        """When page title has no DLC keywords, article body 'all existing DLCs' should match."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles("fitgirl", [
            {"title": "Base Game", "url": "https://fitgirl-repacks.site/base-game/", "magnet": None},
        ])

        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Base Game [FitGirl Repack]",  # page title: no DLC keywords
                "This repack includes all existing DLCs. Requires 60 GB.",
            ),
        ):
            result = _deep_search_article_body(db, "fitgirl",
                                               normalise_for_compare("Base Game - Expansion Pack"))
        db.close()
        assert len(result) == 1

    def test_original_named_dlc_still_works(self, tmp_path: Path) -> None:
        """The existing named-DLC matching (body contains normalised DLC name) still works."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles("fitgirl", [
            {"title": "Dark Souls Iii", "url": "https://fitgirl-repacks.site/dark-souls-3/", "magnet": None},
        ])

        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Dark Souls III [FitGirl Repack]",
                "Repack features: The Ringed City DLC, Ashes of Ariandel included.",
            ),
        ):
            result = _deep_search_article_body(db, "fitgirl",
                                               normalise_for_compare("Dark Souls III: The Ringed City"))
        db.close()
        assert len(result) == 1, "Existing named DLC matching should still work"

    def test_no_match_when_no_dlc_indicators(self, tmp_path: Path) -> None:
        """When nothing indicates DLC coverage, return empty list (no false positive)."""
        from unittest.mock import MagicMock, patch

        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles("fitgirl", [
            {"title": "Simple Game", "url": "https://fitgirl-repacks.site/simple-game/", "magnet": None},
        ])

        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Simple Game [FitGirl Repack]",
                "Based on Steam release v1.0. Requires 20 GB.",
            ),
        ):
            result = _deep_search_article_body(db, "fitgirl",
                                               normalise_for_compare("Simple Game - Unrelated Expansion"))
        db.close()
        assert result == []
```

- [ ] **Step 2: Run the integration tests to confirm they fail**

```bash
uv run pytest tests/unit/test_pipeline.py::TestDeepSearchDlcMatching -v
```

Expected: All 5 tests FAIL. The page-title and article-body cases fail (helpers not yet wired in), the named-DLC and no-match cases should still pass since the existing logic is unchanged.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add integration tests for DLC-aware deep search matching"
```

---

### Task 6: Wire helpers into `_deep_search_article_body`

**Files:**
- Modify: `src/gamarr/pipeline.py` (change `_deep_search_article_body`)

- [ ] **Step 1: Modify `_deep_search_article_body` to capture page title and use both helpers**

Change the function body's for-loop block.  The existing code:

```python
    # At most 3 HTTP requests to limit overhead
    for candidate in candidates[:3]:
        _, article_text = _fetch_fitgirl_page_content(str(candidate["url"]))
        if article_text:
            article_norm = normalise_for_compare(article_text)
            if normalized in article_norm:
                return [candidate]

    return []
```

Replace with:

```python
    # At most 3 HTTP requests to limit overhead
    for candidate in candidates[:3]:
        page_title, article_text = _fetch_fitgirl_page_content(str(candidate["url"]))
        # Quick check: page <title> has DLC keywords (avoids article body scanning)
        if _page_title_has_dlc_keywords(page_title):
            return [candidate]
        if article_text:
            article_norm = normalise_for_compare(article_text)
            # Named DLC match — the DLC name appears literally in the article body
            if normalized in article_norm:
                return [candidate]
            # All-DLCs match — the article body references all DLCs being included
            if _article_contains_all_dlcs(article_text):
                return [candidate]

    return []
```

- [ ] **Step 2: Run all DLC-related tests**

```bash
uv run pytest tests/unit/test_pipeline.py::TestPageTitleHasDlcKeywords \
                     tests/unit/test_pipeline.py::TestArticleContainsAllDlcs \
                     tests/unit/test_pipeline.py::TestDeepSearchDlcMatching -v
```

Expected: 20/20 pass (6 + 9 + 5).

- [ ] **Step 3: Run full test suite to check for regressions**

```bash
uv run pytest -x -q --tb=short
```

Expected: 694+ tests pass (694 existing + 20 new from this feature, no regressions).

- [ ] **Step 4: Run lint and type checks**

```bash
uv run ruff check --fix src/gamarr/pipeline.py tests/unit/test_pipeline.py
uv run ruff format src/gamarr/pipeline.py tests/unit/test_pipeline.py
uv run mypy src/gamarr/pipeline.py
```

Expected: All checks pass.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: wire DLC-aware helpers into _deep_search_article_body

- Check page <title> for '+ All DLCs' / '+ N DLCs' patterns
- Check article body for 'all DLCs' / 'Includes all DLCs' patterns
- Both checks run after page fetch, reusing existing HTTP request
- Existing named-DLC body substring matching is preserved"
```
