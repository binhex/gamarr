# Token Overlap Candidate Filter for DLC Matching — Design Spec

## Problem

The candidate filter in `_deep_search_article_body` requires the sitemap title
to be fully contained within the Metacritic title (`entry_norm in normalized`).
This fails when the FitGirl sitemap title has extra prefix words that the
Metacritic DLC title does not have.

**Example failure:**
- Sitemap title: `"Dungeons & Dragons Neverwinter Nights 2: Enhanced Edition"`
- Metacritic title: `"Neverwinter Nights 2: Mask of The Betrayer"`
- Normalised sitemap: `"dungeonsanddragonsneverwinternights2enhancededition"`
- Normalised Metacritic: `"neverwinternights2maskofthebetrayer"`
- Check: `"dungeonsanddragonsneverwinternights2enhancededition" in "neverwinternights2maskofthebetrayer"` → **False**

Zero candidates are found. The DLC matching never runs. The game stays pending
until expiry.

## Design

### Solution

Replace the strict substring containment check with **word token overlap
matching**. If the two titles share at least 3 significant word tokens, the
sitemap entry becomes a DLC candidate. The article body / page title keyword
checks (already in place) serve as the final safety net.

### Tokenization Rules

1. Lowercase the original title (preserving original word boundaries)
2. Apply Roman numeral conversion (same logic as ``normalise_for_compare`` —
   ``"II"`` → ``"2"``, ``"IV"`` → ``"4"``, etc.)
3. Split on non-alphanumeric characters
4. Filter out empty tokens
5. Compute set intersection as lowercase word tokens
6. Return ``len(intersection) >= min_tokens``

### Architecture

All changes in ``src/gamarr/pipeline.py`` — one new helper function and a
one-line change in ``_deep_search_article_body``.

```
_deep_search_article_body()
  └─ candidates = {sitemap titles with >= 3 token overlap}   ← RELAXED
  └─ for each candidate (max 3):                             ← unchanged
       ├─ Page <title> DLC keywords                          ← unchanged
       ├─ Named DLC in article body                          ← unchanged
       └─ Article body DLC keywords                          ← unchanged
```

### New Helper: ``_titles_share_enough_tokens``

```python
def _titles_share_enough_tokens(
    title_a: str,
    title_b: str,
    min_tokens: int = 3,
) -> bool:
    """Return True if *title_a* and *title_b* share at least *min_tokens*
    significant word tokens.

    Tokenizes each title: lowercases, applies Roman numeral conversion,
    splits on non-alphanumeric characters, counts set intersection.
    """
```

### Change to ``_deep_search_article_body``

Replace this condition (line ~2076):

```python
if entry_norm and entry_norm in normalized and normalized != entry_norm:
    candidates.append(entry)
```

With:

```python
if entry_norm and normalized != entry_norm and _titles_share_enough_tokens(
    entry.get("title", ""),
    game_title,
):
    candidates.append(entry)
```

Where ``pending_title`` is the original (pre-normalisation) Metacritic game title
from the pending game row — available at the call site in
``_process_single_pending_match`` where ``game_title = str(game.game_title)``.

The function signature changes to accept this parameter:

```python
def _deep_search_article_body(
    db: Database,
    source_name: str,
    normalized: str,
    pending_title: str = "",
) -> list[dict[str, str | None]]:
```

And the call site changes to pass ``pending_title=game_title``.

The existing ``entry_norm`` is still computed for the empty-guard check, but
the substring containment is replaced by token overlap.

### Safety Properties

| Scenario | Tokens shared | Result |
|----------|---------------|--------|
| Neverwinter Nights 2 → Mask of The Betrayer | 3 | ✅ matched |
| Total War WH2 → Curse of the Vampire Coast | 4 | ✅ matched |
| Dark Souls III → The Ringed City | 3 | ✅ matched |
| Diablo 3 → Diablo 4 (different game) | 1 | ❌ rejected |
| Star Wars Battlefront II → Jedi Fallen Order | 2 | ❌ rejected |
| Borderlands 3 → Borderlands 2 (different game) | 1 | ❌ rejected |
| Counter-Strike GO → Counter-Strike 2 | 2 | ❌ rejected |
| Assassin's Creed Valhalla → Odyssey DLC (different game) | 3 | ⚠️ theoretical risk |

The Assassin's Creed case is a theoretical risk where ``"assassin", "s", "creed"``
are shared franchise tokens. In practice this is extremely unlikely to produce
a false positive because:
- Both games must have FitGirl repacks with DLC keyword patterns
- The article body describes the specific game, reducing false article-body matches
- If it does occur, the "harm" is downloading an extra repack — no data loss

### Testing

New test cases in ``tests/unit/test_pipeline.py``:

1. **Token overlap — match**: 3+ shared tokens returns True
2. **Token overlap — no match**: 1-2 shared tokens returns False
3. **Token overlap — exact same title**: 5+ tokens, all matching
4. **Integration — Neverwinter Nights 2**: Full deep search with mocked
   page content produces a match (replaces current xfail)
5. **Integration — Diablo 3 vs Diablo 4**: Deep search produces no match
   (false positive guard)
