# Token Overlap Candidate Filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the strict substring containment check in `_deep_search_article_body` with
word token overlap matching, so DLC titles with extra franchise prefixes (e.g. "Dungeons &
Dragons Neverwinter Nights 2") still become candidates for DLC matching.

**Architecture:** Add one new helper `_titles_share_enough_tokens` that tokenizes two titles
and returns True if they share ≥ 3 word tokens. Replace `entry_norm in normalized` with a
call to this helper in `_deep_search_article_body`.  Add a `pending_title` parameter so the
original title text (before normalisation) is available for tokenization.

**Tech Stack:** Python, re (already imported), `normalise_for_compare` from `gamarr.utils`.

---

### Task 1: Write `_titles_share_enough_tokens` unit tests

**Files:**
- Modify: `tests/unit/test_pipeline.py` (add test class before `TestMatchPendingGames`)

- [ ] **Step 1: Add test class**

Insert after `TestDeepSearchDlcMatching`, before the line starting with `class TestMatchPendingGames:`.

```python
class TestTitlesShareEnoughTokens:
    """Tests for _titles_share_enough_tokens helper."""

    def test_match_three_tokens(self) -> None:
        """Two titles sharing 3 word tokens returns True."""
        from gamarr.pipeline import _titles_share_enough_tokens
        assert _titles_share_enough_tokens(
            "Dungeons & Dragons Neverwinter Nights 2 Enhanced Edition",
            "Neverwinter Nights 2: Mask of The Betrayer",
        ) is True

    def test_match_four_tokens(self) -> None:
        """Two titles sharing 4 word tokens returns True."""
        from gamarr.pipeline import _titles_share_enough_tokens
        assert _titles_share_enough_tokens(
            "Total War WARHAMMER II",
            "Total War: WARHAMMER II - Curse of the Vampire Coast",
        ) is True

    def test_no_match_two_tokens(self) -> None:
        """Two titles sharing only 2 tokens returns False."""
        from gamarr.pipeline import _titles_share_enough_tokens
        assert _titles_share_enough_tokens(
            "Star Wars Battlefront II",
            "Star Wars Jedi: Fallen Order",
        ) is False

    def test_no_match_one_token(self) -> None:
        """Two titles sharing only 1 token (different games) returns False."""
        from gamarr.pipeline import _titles_share_enough_tokens
        assert _titles_share_enough_tokens(
            "Diablo 3",
            "Diablo 4: Vessel of Hatred",
        ) is False

    def test_match_exact_same_game(self) -> None:
        """Two titles referring to the same game share many tokens."""
        from gamarr.pipeline import _titles_share_enough_tokens
        assert _titles_share_enough_tokens(
            "Dark Souls III",
            "Dark Souls III: The Ringed City",
        ) is True

    def test_roman_numeral_token_matching(self) -> None:
        """Roman numeral 'III' and '3' are treated as the same token."""
        from gamarr.pipeline import _titles_share_enough_tokens
        assert _titles_share_enough_tokens(
            "Dark Souls III",
            "Dark Souls 3: The Ringed City",
        ) is True

    def test_no_match_completely_different(self) -> None:
        """Completely unrelated titles share zero tokens."""
        from gamarr.pipeline import _titles_share_enough_tokens
        assert _titles_share_enough_tokens(
            "Minecraft",
            "The Witcher 3: Wild Hunt",
        ) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_pipeline.py::TestTitlesShareEnoughTokens -v
```

Expected: 7 failures with `ImportError: cannot import name '_titles_share_enough_tokens'`.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: add failing tests for _titles_share_enough_tokens"
```

---

### Task 2: Implement `_titles_share_enough_tokens` helper

**Files:**
- Modify: `src/gamarr/pipeline.py` (add function before `_check_candidate_for_dlc_match`)

- [ ] **Step 1: Add the tokenizer and helper function**

Insert after `_truncate_at_backwards_compat` and before `_check_candidate_for_dlc_match`:

```python
def _tokenize_title(title: str) -> set[str]:
    """Tokenize a game title into lowercased word tokens, converting Roman numerals.

    Applies the same Roman numeral → Arabic conversion as
    ``normalise_for_compare``, then splits on non-alphanumeric boundaries.
    Returns a set of lowercased word tokens (empty tokens excluded).
    """
    from gamarr.utils import _ROMAN_TO_ARABIC

    text = title.lower()
    for pattern, replacement in _ROMAN_TO_ARABIC:
        text = pattern.sub(replacement, text)
    return {token.strip() for token in re.split(r"[^a-z0-9]+", text) if token.strip()}


def _titles_share_enough_tokens(
    title_a: str,
    title_b: str,
    min_tokens: int = 3,
) -> bool:
    """Return True if *title_a* and *title_b* share at least *min_tokens* word tokens.

    Tokenizes each title via ``_tokenize_title`` and counts the set
    intersection.  Default threshold is 3 tokens — high enough to avoid
    single-word collisions (``"Diablo 3"`` vs ``"Diablo 4"``) while low
    enough to match DLC titles (``"Neverwinter Nights 2: Mask of The Betrayer"``
    shares 3 tokens with ``"Dungeons & Dragons Neverwinter Nights 2"``).
    """
    tokens_a = _tokenize_title(title_a)
    tokens_b = _tokenize_title(title_b)
    return len(tokens_a & tokens_b) >= min_tokens
```

Note: `_ROMAN_TO_ARABIC` is already defined in `gamarr.utils` but is currently prefixed with `_`.  If it is not importable (Python convention treats `_`-prefixed names as module-private), inline the Roman numeral conversion logic directly in `_tokenize_title`:

```python
def _tokenize_title(title: str) -> set[str]:
    import re

    _ROMAN_PATTERNS = [
        (re.compile(r"\bxii\b"), "12"),  # must precede \bxi\b
        (re.compile(r"\bxi\b"), "11"),
        (re.compile(r"\bix\b"), "9"),
        (re.compile(r"\bviii\b"), "8"),
        (re.compile(r"\bvii\b"), "7"),
        (re.compile(r"\bvi\b"), "6"),
        (re.compile(r"\biv\b"), "4"),
        (re.compile(r"\biii\b"), "3"),
        (re.compile(r"\bii\b"), "2"),
        (re.compile(r"\bx\b"), "10"),
        (re.compile(r"\bv\b"), "5"),
        (re.compile(r"\bi\b"), "1"),
    ]

    text = title.lower()
    for pattern, replacement in _ROMAN_PATTERNS:
        text = pattern.sub(replacement, text)
    return {token.strip() for token in re.split(r"[^a-z0-9]+", text) if token.strip()}
```

Prefer importing `_ROMAN_TO_ARABIC` if it is accessible; use the inline version as fallback.

- [ ] **Step 2: Run the token overlap tests**

```bash
uv run pytest tests/unit/test_pipeline.py::TestTitlesShareEnoughTokens -v
```

Expected: 7/7 pass.

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: add _titles_share_enough_tokens helper for DLC candidate matching"
```

---

### Task 3: Wire token overlap into `_deep_search_article_body`

**Files:**
- Modify: `src/gamarr/pipeline.py` (change function signature and candidate filter)

- [ ] **Step 1: Add `pending_title` parameter to function signature**

Find the function at its current location. Change:

```python
def _deep_search_article_body(
    db: Database,
    source_name: str,
    normalized: str,
) -> list[dict[str, str | None]]:
```

To:

```python
def _deep_search_article_body(
    db: Database,
    source_name: str,
    normalized: str,
    pending_title: str = "",
) -> list[dict[str, str | None]]:
```

- [ ] **Step 2: Replace the candidate filter**

Change the condition in the candidate-finding loop. Find:

```python
        if entry_norm and entry_norm in normalized and normalized != entry_norm:
```

Replace with:

```python
        if entry_norm and normalized != entry_norm and _titles_share_enough_tokens(
            entry.get("title", ""),
            pending_title,
        ):
```

- [ ] **Step 3: Update the call site in `_process_single_pending_match`**

Find the call to `_deep_search_article_body` (approx line 2204). Change:

```python
        matches = _deep_search_article_body(db, source_name, normalized)
```

To:

```python
        matches = _deep_search_article_body(db, source_name, normalized, pending_title=game_title)
```

- [ ] **Step 4: Run all token overlap and deep search tests**

```bash
uv run pytest tests/unit/test_pipeline.py::TestTitlesShareEnoughTokens tests/unit/test_pipeline.py::TestDeepSearchDlcMatching -v
```

Expected: All tests pass. The xfail integration test `test_page_title_all_expansions` may now PASS (since token overlap should find the candidate). If it passes, remove the `@pytest.mark.xfail` decorator from it.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest -x -q --tb=short
```

Expected: All tests pass. No regressions. If `test_page_title_all_expansions` now passes, total becomes 721 passed, 0 xfailed.

- [ ] **Step 6: Run lint and type checks**

```bash
uv run ruff check --fix src/gamarr/pipeline.py tests/unit/test_pipeline.py
uv run ruff format src/gamarr/pipeline.py tests/unit/test_pipeline.py
uv run mypy src/gamarr/pipeline.py
```

Expected: All checks pass.

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: use token overlap for DLC candidate filter in _deep_search_article_body

Replace strict substring containment (entry_norm in normalized) with
_titles_share_enough_tokens(), which tokenizes both titles and checks
for >= 3 shared word tokens. This handles franchise-prefix cases
(e.g. 'Dungeons & Dragons' before 'Neverwinter Nights 2') where the
sitemap title has extra words the Metacritic DLC title does not."
```
