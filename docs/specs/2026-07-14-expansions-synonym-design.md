# "All Expansions" DLC Synonym Support — Design Spec

## Problem

FitGirl repacks use "expansions" and "DLCs" interchangeably to indicate that a
repack includes all bonus content for a game.  For example, a repack titled
"Game Name – v1.0 + All Expansions" covers the same content as one saying
"+ All DLCs", but `_PAGE_TITLE_DLC_PATTERNS` and `_ALL_DLCS_PATTERNS` only
detect the `dlc` variant.

A pending Metacritic game that is an expansion of a base game (e.g. "Neverwinter
Nights 2: Mask of The Betrayer") will not be matched against a repack whose
page title says "+ All Expansions" because neither the page title check nor the
article body check triggers for the word "expansions".

## Design

### Scope

- Add `expansions?` as an alternation to every existing DLC keyword pattern in
  `_PAGE_TITLE_DLC_PATTERNS` and `_ALL_DLCS_PATTERNS`.
- No new patterns, no new helpers, no DB schema changes, no config options.

### What Changes

**File:** `src/gamarr/pipeline.py`

Each `dlcs?` in the existing regex patterns is replaced with `(?:dlcs?|expansions?)`.

This change applies to these pattern lists:

| List | Existing pattern | New pattern |
|------|-----------------|-------------|
| `_PAGE_TITLE_DLC_PATTERNS[0]` | `\+ All DLCs` | `\+ All (DLCs\|Expansions)` |
| `_PAGE_TITLE_DLC_PATTERNS[1]` | `\+ N DLCs` | `\+ N (DLCs\|Expansions)` |
| `_ALL_DLCS_PATTERNS[0]` | `all DLCs` | `all (DLCs\|Expansions)` |
| `_ALL_DLCS_PATTERNS[1]` | `all available DLCs` | `all available (DLCs\|Expansions)` |
| `_ALL_DLCS_PATTERNS[2]` | `all existing DLCs` | `all existing (DLCs\|Expansions)` |

### Architecture

```
_deep_search_article_body()
  └─ candidates (unchanged)                             ← no change
  └─ for each candidate (max 3):
       ├─ Page <title> keywords                          ← patterns now match
       │  └─ "+ All Expansions", "+ 3 Expansions"       │  "expansions"
       ├─ Named DLC in article body                     ← no change
       └─ Article body keywords                          ← patterns now match
          └─ "all expansions", "all available expansions"  "expansions"
```

### Testing

New test cases in `tests/unit/test_pipeline.py`:

1. **Page title "+ All DLCs"** — existing test, unchanged. ✅
2. **Page title "+ All Expansions"** — new test: should return True.
3. **Page title "+ 3 DLCs"** — existing test, unchanged. ✅  
4. **Page title "+ 3 Expansions"** — new test: should return True.
5. **Article body "all expansions"** — new test: should return True.
6. **Article body "all existing expansions"** — new test: should return True.
7. **Article body "all available expansions"** — new test: should return True.

Existing tests for DLC variants continue to pass unchanged.
