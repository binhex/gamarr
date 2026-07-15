# DEBUG-Level Match Detail Logging — Design Spec

## Problem

When gamarr incorrectly matches a pending Metacritic game to the wrong FitGirl
or FreeGOG source title (e.g. matching "Neverwinter Nights 2: Mask of The
Betrayer" to "Atelier Sophie 2" via token overlap), the user has no way to
quickly see which source title was actually matched.  The existing INFO log
shows both titles on the success path, but there is no DEBUG-level detail about
match type, candidate count, or source title context.

## Design

### Scope

Add `logger.debug(...)` calls alongside the existing INFO-level match logging
in `_process_single_pending_match` in `src/gamarr/pipeline.py`.  The existing
INFO log stays unchanged.  No new functions, no new config, no new files.

### What Changes

Two `logger.debug(...)` calls are added to `_process_single_pending_match`:

**1. After deep search finds candidates:**
When `_deep_search_article_body` returns candidates, log the count so the user
can see whether the correct repack was even in the candidate pool.

```python
matches = _deep_search_article_body(db, source_name, normalized, pending_title=game_title)
if matches:
    logger.debug(
        "Deep search found {} candidate(s) for '{}'",
        len(matches),
        game_title,
    )
```

**2. After a successful match (alongside existing INFO):**
When ``_find_first_non_rejected_match`` returns a match, log the source title
and URL at DEBUG level.  The existing INFO-level match log is preserved
unchanged.

```python
if best is None:
    return None

# Existing INFO log (unchanged):
logger.info(
    "{} match: '{}' \u2192 '{}' ({})",
    _source_display(source_name),
    game_title,
    best["title"],
    best["url"],
)

# New DEBUG detail:
logger.debug(
    "Matched pending '{}' to {} source '{}' ({})",
    game_title,
    _source_display(source_name),
    best["title"],
    best["url"],
)
```

### What Does NOT Change

- No changes to the existing INFO log
- No changes to reject-keyword logging, no-match logging, or delivery logging
- No new config options or CLI flags
- No new functions or imports

### Files Changed

| File | Change |
|------|--------|
| `src/gamarr/pipeline.py` | ~6 lines: 2 ``logger.debug(...)`` calls in ``_process_single_pending_match`` |

### Testing

Debug-level logging at DEBUG level is silent by default (loguru defaults to
INFO).  No behavioral change.  Existing 728 tests continue to pass unchanged.
