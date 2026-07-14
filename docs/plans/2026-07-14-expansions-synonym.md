# "All Expansions" DLC Synonym Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `expansions?` as a synonym for `dlcs?` in all DLC keyword patterns so FitGirl
repacks saying "+ All Expansions" are detected by the DLC-aware deep search.

**Architecture:** Replace every `dlcs?` with `(?:dlcs?|expansions?)` in the two compiled
regex pattern lists (`_PAGE_TITLE_DLC_PATTERNS` and `_ALL_DLCS_PATTERNS`) in
`src/gamarr/pipeline.py`. No new helpers, no new constants, no config changes.

**Tech Stack:** Python, re (already imported). No new dependencies.

---

### Task 1: Write tests for "Expansions" keyword variants

**Files:**
- Modify: `tests/unit/test_pipeline.py` (add test methods to existing classes)

- [ ] **Step 1: Add page title "expansions" tests**

Add the following test methods to `TestPageTitleHasDlcKeywords` (after the existing test methods in that class):

```python
    def test_has_keywords_all_expansions(self) -> None:
        """Page title containing '+ All Expansions' returns True."""
        from gamarr.pipeline import _page_title_has_dlc_keywords
        assert _page_title_has_dlc_keywords(
            "Game Name v1.0 + All Expansions [FitGirl Repack]"
        ) is True

    def test_has_keywords_counted_expansions(self) -> None:
        """Page title containing '+ 4 Expansions' returns True."""
        from gamarr.pipeline import _page_title_has_dlc_keywords
        assert _page_title_has_dlc_keywords(
            "Game v2.0 + 4 Expansions [FitGirl Repack]"
        ) is True
```

- [ ] **Step 2: Add article body "expansions" tests**

Add the following test methods to `TestArticleContainsAllDlcs`:

```python
    def test_contains_all_expansions_basic(self) -> None:
        """Article body with 'all expansions' returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs
        assert _article_contains_all_dlcs(
            "This repack includes all expansions released so far."
        ) is True

    def test_contains_all_expansions_available(self) -> None:
        """Article body with 'all available expansions' returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs
        assert _article_contains_all_dlcs(
            "All available expansions are bundled here."
        ) is True

    def test_contains_all_expansions_existing(self) -> None:
        """Article body with 'all existing expansions' returns True."""
        from gamarr.pipeline import _article_contains_all_dlcs
        assert _article_contains_all_dlcs(
            "Includes all existing expansions."
        ) is True
```

- [ ] **Step 3: Add integration test for "All Expansions" in page title**

Add the following test method to `TestDeepSearchDlcMatching`:

```python
    @pytest.mark.xfail(reason="Title prefix mismatch not yet fixed — see expansions-synonym-design spec")
    def test_page_title_all_expansions(self, tmp_path: Path) -> None:
        """When page <title> contains '+ All Expansions', deep search matches.

        The sitemap title (from URL slug) does NOT contain DLC keywords,
        but the page title says '+ All Expansions' — the helper should detect it.

        Marked xfail: the candidate filter in _deep_search_article_body still
        requires the sitemap title to be fully contained in the pending title.
        The "Dungeons & Dragons" prefix prevents this.  See the design spec
        for details on the planned title-matching fix.
        """
        from unittest.mock import patch
        from gamarr.database import Database
        from gamarr.pipeline import _deep_search_article_body
        from gamarr.utils import normalise_for_compare

        db = Database(str(tmp_path / "test.db"))
        db.rebuild_source_titles("fitgirl", [
            {"title": "Dungeons And Dragons Neverwinter Nights 2 Enhanced Edition",
             "url": "https://fitgirl-repacks.site/dungeons-and-dragons-neverwinter-nights-2-enhanced-edition/",
             "magnet": None},
        ])

        with patch(
            "gamarr.pipeline._fetch_fitgirl_page_content",
            return_value=(
                "Dungeons & Dragons Neverwinter Nights 2: Enhanced Edition – v1.110 + All Expansions [FitGirl Repack]",
                "Some body text.",
            ),
        ):
            result = _deep_search_article_body(db, "fitgirl",
                                               normalise_for_compare("Neverwinter Nights 2: Mask of The Betrayer"))
        db.close()
        assert len(result) == 1, f"Expected 1 match, got {len(result)}: {result}"
```

Note: This integration test will XFAIL (expected failure) even after the pattern change because the
candidate filter still requires the sitemap title to be contained in the pending
title.  The subtitle prefix problem ("Dungeons & Dragons") is a separate issue
NOT covered by this spec.  The test is marked `@pytest.mark.xfail` and will
pass once the title matching problem is addressed in a future change.

- [ ] **Step 4: Run the new tests to verify they fail**

```bash
uv run pytest tests/unit/test_pipeline.py -k "has_keywords_all_expansions or has_keywords_counted_expansions or contains_all_expansions or test_page_title_all_expansions" -v
```

Expected: The 2 page-title and 3 article-body tests FAIL with `assert False is True`. The integration test FAILS because the existing patterns don't match "Expansions".

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add failing tests for 'All Expansions' DLC keyword detection"
```

---

### Task 2: Update regex patterns to include "expansions"

**Files:**
- Modify: `src/gamarr/pipeline.py` (update `_PAGE_TITLE_DLC_PATTERNS` and `_ALL_DLCS_PATTERNS`)

- [ ] **Step 1: Update `_PAGE_TITLE_DLC_PATTERNS`**

Find the existing constant (near line 2520):

```python
_PAGE_TITLE_DLC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\+\s*all\s+dlcs?\b", re.IGNORECASE),
    re.compile(r"\+\s*\d+\s+dlcs?\b", re.IGNORECASE),
]
```

Replace with:

```python
_PAGE_TITLE_DLC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\+\s*all\s+(?:dlcs?|expansions?)\b", re.IGNORECASE),
    re.compile(r"\+\s*\d+\s+(?:dlcs?|expansions?)\b", re.IGNORECASE),
]
```

- [ ] **Step 2: Update `_ALL_DLCS_PATTERNS`**

Find the existing constant (near line 2528):

```python
_ALL_DLCS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\ball\s+dlcs?\b", re.IGNORECASE),
    re.compile(r"\ball\s+available\s+dlcs?\b", re.IGNORECASE),
    re.compile(r"\ball\s+existing\s+dlcs?\b", re.IGNORECASE),
]
```

Replace with:

```python
_ALL_DLCS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\ball\s+(?:dlcs?|expansions?)\b", re.IGNORECASE),
    re.compile(r"\ball\s+available\s+(?:dlcs?|expansions?)\b", re.IGNORECASE),
    re.compile(r"\ball\s+existing\s+(?:dlcs?|expansions?)\b", re.IGNORECASE),
]
```

- [ ] **Step 3: Run the new expansion tests to verify they pass**

```bash
uv run pytest tests/unit/test_pipeline.py -k "has_keywords_all_expansions or has_keywords_counted_expansions or contains_all_expansions" -v
```

Expected: 5/5 pass (2 page-title, 3 article-body). The integration test `test_page_title_all_expansions` still fails because the candidate filter doesn't find the sitemap entry (the title prefix problem from the spec notes — separate issue, not this task).

- [ ] **Step 4: Run the existing DLC tests to verify no regressions**

```bash
uv run pytest tests/unit/test_pipeline.py -k "has_dlc_keywords or contains_all_dlcs" -v
```

Expected: All existing DLC tests pass (6 page-title + 9 article-body = 15/15). The patterns with `(?:dlcs?|expansions?)` correctly continue to match `dlc` variants.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
uv run pytest -x -q --tb=short
```

Expected: All previously passing tests continue to pass (714 from previous chain + 5 new passing tests + 1 xfail).

- [ ] **Step 6: Run lint and type checks**

```bash
uv run ruff check --fix src/gamarr/pipeline.py tests/unit/test_pipeline.py
uv run ruff format src/gamarr/pipeline.py tests/unit/test_pipeline.py
uv run mypy src/gamarr/pipeline.py
```

Expected: All checks pass.

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: add 'expansion(s)' as DLC synonym in keyword patterns

- _PAGE_TITLE_DLC_PATTERNS: now matches '+ All Expansions' and '+ N Expansions'
- _ALL_DLCS_PATTERNS: now matches 'all expansions', 'all available expansions',
  'all existing expansions'
- Uses (?:dlcs?|expansions?) alternation — no new patterns, just wider coverage"
```
