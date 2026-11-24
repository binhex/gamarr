# DLC-Aware FitGirl Matching — Design Spec

## Problem

FitGirl repacks often include all DLCs for a game in a single repack (e.g.
`"Total War: WARHAMMER 2 – v1.9.2 + All DLCs"`). When gamarr has a pending
Metacritic game that is a DLC of that base game (e.g. `"Total War: WARHAMMER II
- Curse of the Vampire Coast"`), the current matching logic cannot connect them
and the DLC stays in the pending queue forever.

The gap exists because:

1. `match_source_title()` compares normalised titles — the DLC title is longer
   than the base-game title, and the reverse-substring direction is blocked by
   design to prevent false positives.
2. `_deep_search_article_body()` already handles some DLC/expansion cases
   (where the DLC name appears literally in the article body), but cannot match
   repacks that say "All DLCs included" without listing individual DLC names.

## Design

### Scope

- **Match page ``<title>`` DLC keywords** — check the FitGirl repack page's
  HTML ``<title>`` tag for DLC-inclusion patterns (e.g. `"... + All DLCs"`,
  `"... + 15 DLCs"`).  The page title is already extracted by
  ``_fetch_fitgirl_page_content`` alongside the article body — no additional
  HTTP request needed.
- **Match article body keywords** — check the FitGirl repack article body
  text for "All DLCs" keyword patterns when the page title check is
  inconclusive.
- **Preserve existing named-DLC matching** — the current article body
  substring check still runs and takes priority.

**Why not check the DB ``source_titles.title``?** FitGirl sitemap titles are
derived from URL slugs (via ``_title_from_url``), not from the XML title
element.  A URL like ``/total-war-warhammer-2/`` produces title ``"Total War
Warhammer 2"`` — no version or DLC metadata survives.  The HTML ``<title>``
tag of the actual repack page is the first place where full repack information
(`"… + All DLCs"`, `"… + 15 DLCs"`) becomes available.

### Architecture

```
_process_single_pending_match()
  └─ match_source_title()             → direct title match
  └─ _deep_search_article_body()      → DLC / expansion match (ENHANCED)
       ├─ candidates = {sitemap title where title ⊂ pending title}
       └─ for each candidate (max 3):
            ├─ Fetch article page                              ← existing
            ├─ Check page <title> for DLC keywords             ← NEW
            │  └─ "+ All DLCs" OR "+ <N> DLCs"?
            │     → return match
            ├─ Check normalised DLC name in body               ← existing
            ├─ Check "All DLCs" keyword patterns in body       ← NEW
            └─ If any check passes → return match
```

All changes are in `src/gamarr/pipeline.py`. No new files, no DB schema changes,
no config options, no CLI flags.

### New Helpers

#### `_page_title_has_dlc_keywords(page_title: str | None) -> bool`

Scans the HTML ``<title>`` tag text (extracted by
``_fetch_fitgirl_page_content``) for DLC-inclusion patterns.  Case-insensitive.

| Pattern | Example match |
|---------|---------------|
| `\+ All DLCs?\b` (case-insensitive) | `"… + All DLCs …"` |
| `\+ \d+ DLCs?\b` (case-insensitive) | `"… + 15 DLCs …"` |

The page title check runs AFTER the page is fetched (the title is already in
memory from ``_fetch_fitgirl_page_content``) but BEFORE the article body is
scanned — avoiding the cost of normalising and regexing the full article text
when the title alone is sufficient.

#### `_article_contains_all_dlcs(article_text: str | None) -> bool`

Scans the article body text for "All DLCs" keyword patterns.

| Pattern | Example match |
|---------|---------------|
| `\ball\s+dlcs?\b` | `"... all DLCs included ..."` |
| `\ball\s+available\s+dlcs?\b` | `"... all available DLCs ..."` |
| `\ball\s+existing\s+dlcs?\b` | `"... all existing DLCs ..."` |
| `\bincludes?\s+all\s+dlcs?\b` | `"... Includes all DLCs ..."` |

### Modified Function

#### `_deep_search_article_body`

```python
def _deep_search_article_body(
    db: Database,
    source_name: str,
    normalized: str,
) -> list[dict[str, str | None]]:
    candidates: list[dict[str, str | None]] = []
    for entry in db.get_all_source_titles(source_name):
        entry_title = str(entry.get("title", ""))
        entry_norm = normalise_for_compare(entry_title)
        if entry_norm and entry_norm in normalized and normalized != entry_norm:
            candidates.append(entry)

    for candidate in candidates[:3]:
        page_title, article_text = _fetch_fitgirl_page_content(str(candidate["url"]))
        # Page <title> check (no article scan needed for clear DLC keywords)
        if _page_title_has_dlc_keywords(page_title):
            return [candidate]
        if article_text:
            article_norm = normalise_for_compare(article_text)
            # Named DLC match (existing)
            if normalized in article_norm:
                return [candidate]
            # All-DLCs match (new)
            if _article_contains_all_dlcs(article_text):
                return [candidate]
    return []
```

Keyword-only params and type annotations are maintained from the original.

### Error Handling

- **Page fetch failure:** ``_fetch_fitgirl_page_content`` returns ``(None,
  None)``.  Both helper functions return ``False``.  The candidate is skipped
  and the loop continues to the next.
- **Empty article body:** ``_article_contains_all_dlcs(None)`` returns
  ``False``.
- **Missing page title:** ``_page_title_has_dlc_keywords(None)`` returns
  ``False``.
- **No candidates found:** The function returns ``[]`` as before — pending
  game stays in queue.
- **Normalisation edge cases:** Both helpers handle ``None``/empty input
  gracefully by returning ``False``.

### Files Changed

| File | Change |
|------|--------|
| `src/gamarr/pipeline.py` | ~25 new lines: 2 helpers + ``_deep_search_article_body`` modifications |
| `tests/unit/test_pipeline.py` | ~80 new lines: 5 new test cases |

### Testing

New test cases in `tests/unit/test_pipeline.py`:

1. **`test_deep_search_dlc_page_title_all_dlcs`** — Page ``<title>`` tag
   contains `"... + All DLCs ..."` → match returned.
2. **`test_deep_search_dlc_page_title_counted_dlcs`** — Page title contains
   `"... + 15 DLCs ..."` → match returned.
3. **`test_deep_search_dlc_article_body_all_dlcs`** — Page title has no DLC
   keywords, but article body contains "all existing DLCs" → match returned.
4. **`test_deep_search_dlc_article_body_variant`** — Article body contains
   "Includes ALL DLCs" (different casing) → match returned.
5. **`test_deep_search_dlc_no_match`** — Page has no DLC mention and no DLC
   name → empty list returned (no false positive regression).

Also verify existing tests continue to pass (694 tests, no regressions).
