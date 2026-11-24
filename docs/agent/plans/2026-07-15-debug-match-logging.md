# DEBUG-Level Match Detail Logging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two `logger.debug(...)` calls in `_process_single_pending_match` to log
candidate count and matched source title at DEBUG level, alongside the existing
INFO-level match log.

**Architecture:** Insert two log statements into `_process_single_pending_match` in
`src/gamarr/pipeline.py` — one after `_deep_search_article_body` returns and one
after `_find_first_non_rejected_match` finds a match. No new functions, no config
changes, no imports needed.

**Tech Stack:** Python, loguru (already imported).

---

### Task 1: Add DEBUG log after deep search candidates

**Files:**
- Modify: `src/gamarr/pipeline.py` (around line 2279)

- [ ] **Step 1: Find the code location**

The current code at this location shows:

```python
    if not matches and source_name.casefold() == "fitgirl":
        matches = _deep_search_article_body(db, source_name, normalized, pending_title=game_title)

    if not matches:
        _touch_pending_by_mode(db, game_slug, search_mode)
        logger.info(
```

Insert a DEBUG log between the deep search call and the `if not matches` check:

- [ ] **Step 2: Insert the log statement**

Change:
```python
        matches = _deep_search_article_body(db, source_name, normalized, pending_title=game_title)

    if not matches:
```

To:
```python
        matches = _deep_search_article_body(db, source_name, normalized, pending_title=game_title)
        if matches:
            logger.debug(
                "Deep search found {} candidate(s) for '{}'",
                len(matches),
                game_title,
            )

    if not matches:
```

- [ ] **Step 3: Run the test suite**

```bash
uv run pytest -x -q --tb=short
```

Expected: 728 passed (no behavioral change — debug logging is silent by default).

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: add DEBUG log for deep search candidate count"
```

---

### Task 2: Add DEBUG log alongside match confirmation

**Files:**
- Modify: `src/gamarr/pipeline.py` (around line 2350)

- [ ] **Step 1: Find the code location**

The current code at this location shows:

```python
    best = _find_first_non_rejected_match(db, source_name, matches, game_title, game_slug, reject_keywords, search_mode)

    if best is None:
        return None

    logger.info(
        "{} match: '{}' \u2192 '{}' ({})",
        _source_display(source_name),
        game_title,
        best["title"],
        best["url"],
    )
```

- [ ] **Step 2: Insert the log statement**

Change to:

```python
    best = _find_first_non_rejected_match(db, source_name, matches, game_title, game_slug, reject_keywords, search_mode)

    if best is None:
        return None

    logger.info(
        "{} match: '{}' \u2192 '{}' ({})",
        _source_display(source_name),
        game_title,
        best["title"],
        best["url"],
    )

    logger.debug(
        "Matched pending '{}' to {} source '{}' ({})",
        game_title,
        _source_display(source_name),
        best["title"],
        best["url"],
    )
```

- [ ] **Step 3: Run the test suite**

```bash
uv run pytest -x -q --tb=short
```

Expected: 728 passed (no behavioral change — debug logging is silent by default).

- [ ] **Step 4: Run lint checks**

```bash
uv run ruff check src/gamarr/pipeline.py
uv run ruff format --check src/gamarr/pipeline.py
```

Expected: All checks pass.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: add DEBUG log for matched source title in _process_single_pending_match"
```
