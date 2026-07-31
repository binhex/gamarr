# Nodriver FreeGOG Cloudflare Bypass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `requests.get()` in the FreeGOG source with nodriver (undetected Chrome browser automation) to bypass Cloudflare Turnstile protection on `freegogpcgames.com`.

**Architecture:** An `asyncio.run()`-based wrapper starts a fresh headless Chrome browser per request, waits for the Cloudflare challenge to resolve, extracts the rendered HTML, and returns it. The existing HTML parsers (`_parse_freegog_az_page`, `_extract_magnet_from_freegog_page`) receive the same HTML strings unchanged.

**Tech Stack:** Python 3.12+, nodriver (PyPI), Chromium (pacman)

---

### Task 1: Add nodriver and Chromium dependencies

**Files:**
- Modify: `/data/gamarr/pyproject.toml`
- Modify: `/data/arch-gamarr/build/root/install.sh`

- [ ] **Step 1: Add nodriver to pyproject.toml**

In the `dependencies` list, add `"nodriver"`:

```diff
     "apprise",
+    "nodriver",
     "pydantic>=2.0.0",
```

- [ ] **Step 2: Add chromium to install.sh**

In `arch-gamarr/build/root/install.sh`, add `chromium` to the `pacman_packages` variable:

```diff
-pacman_packages="git python python-pip python-uv"
+pacman_packages="git python python-pip python-uv chromium"
```

- [ ] **Step 3: Install and verify**

Run: `cd /data/gamarr && uv sync`
Expected: nodriver installs without errors.

Run: `uv run python -c "import nodriver; print(nodriver.__version__ if hasattr(nodriver, '__version__') else 'installed')"`
Expected: Prints version or "installed".

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock ../arch-gamarr/build/root/install.sh
git commit -m "deps: add nodriver and chromium for Cloudflare bypass"
```

---

### Task 2: Replace requests.get with nodriver in freegog.py

**Files:**
- Modify: `src/gamarr/sources/freegog.py`

- [ ] **Step 1: Add imports and async helper**

Add at the top of the file (after `import re`):

```python
import asyncio

import nodriver as uc
```

Add at module level (before `_EDITION_PATTERN`):

```python
def _nodriver_get(url: str) -> str:
    """Fetch a URL through nodriver (undetected Chrome), returning HTML."""
    return asyncio.run(_nodriver_get_async(url))


async def _nodriver_get_async(url: str) -> str:
    browser = await uc.start(headless=True)
    try:
        page = await browser.get(url)
        await page
        return str(await page.get_content())
    finally:
        browser.stop()
```

- [ ] **Step 2: Remove _USER_AGENT constant**

Delete the entire `_USER_AGENT = "Mozilla/5.0 ..."` block.

- [ ] **Step 3: Replace requests.get in _index_az_page**

Change from:

```python
        url = "https://freegogpcgames.com/game-list/"
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            az_entries = _parse_freegog_az_page(resp.text)
```

to:

```python
        url = "https://freegogpcgames.com/game-list/"
        try:
            html = _nodriver_get(url)
            az_entries = _parse_freegog_az_page(html)
```

- [ ] **Step 4: Replace requests.get in _fetch_and_store_game**

Change from:

```python
        try:
            game_resp = requests.get(
                entry["url"],
                timeout=30,
                headers={"User-Agent": _USER_AGENT},
            )
            game_resp.raise_for_status()
            magnet = _extract_magnet_from_freegog_page(game_resp.text)
```

to:

```python
        try:
            html = _nodriver_get(entry["url"])
            magnet = _extract_magnet_from_freegog_page(html)
```

- [ ] **Step 5: Update exception handlers**

In both `_index_az_page` and `_fetch_and_store_game`, change:

```python
        except requests.RequestException as exc:
            logger.warning(...)
```

to:

```python
        except Exception as exc:
            logger.warning(...)
```

nodriver raises its own exception types, not `requests.RequestException` subclasses.

- [ ] **Step 6: Remove unused `import requests`**

If `requests` is no longer used elsewhere in `freegog.py`, remove the import:

```diff
-import requests
```

(Keep it if still used — check `_MAGNET_URL_PATTERN` and base64 decode which don't use requests.)

- [ ] **Step 7: Run freegog tests to verify**

Run: `uv run pytest tests/unit/test_freegog.py -v --tb=short`
Expected: Tests should fail initially because mocks target `requests.get` which is no longer used. These will be fixed in Task 3.

- [ ] **Step 8: Commit**

```bash
git add src/gamarr/sources/freegog.py
git commit -m "feat: replace requests.get with nodriver for FreeGOG Cloudflare bypass"
```

---

### Task 3: Update test mocks for nodriver

**Files:**
- Modify: `tests/unit/test_freegog.py`

- [ ] **Step 1: Update mock strategy**

All tests that mock `requests.get` must now mock `_nodriver_get` instead — it's a module-level function that returns a plain HTML string (not a Response object).

The pattern changes from:

```python
with patch("gamarr.sources.freegog.requests.get") as mock_get:
    mock_get.return_value = mock_response
    source.fetch_sitemap(db)
    mock_get.assert_called_once()
```

to:

```python
with patch("gamarr.sources.freegog._nodriver_get") as mock_get:
    mock_get.return_value = raw_html_string
    source.fetch_sitemap(db)
    mock_get.assert_called_once_with(url)
```

Key differences:
- Mock target: `gamarr.sources.freegog._nodriver_get` instead of `gamarr.sources.freegog.requests.get`
- Return value: plain `str` (HTML) instead of a `MockResponse` object with `.text` and `.raise_for_status()`
- No `.text` attribute — parsers get the string directly
- No `.raise_for_status()` — the mock doesn't need it since `_nodriver_get` handles status via exceptions

The test `test_handles_az_failure` (which tests HTTP failures) needs `mock_get.side_effect = Exception("fail")` instead of `requests.exceptions.ConnectionError`.

- [ ] **Step 2: Update each mock patch**

There are ~5 test methods in `TestFreeGOGFetchSitemap` and `TestFreeGOGFetchGame` classes that mock `requests.get`. Update each one:

1. `test_indexes_new_games` — replace `requests.get` mock with `_nodriver_get`, return `az_html` directly
2. `test_skips_known_games` — replace mock, return `az_html` directly
3. `test_re_fetches_missing_magnet` — replace mock, return different HTML per URL
4. `test_cache_hit_skips` — replace mock (sitemap already cached, so no HTTP call)
5. `test_handles_az_failure` — replace `side_effect=ConnectionError` with `side_effect=Exception("fail")`

For tests that return per-URL HTML, use a `side_effect` function that checks the URL argument:

```python
def _mock_nodriver(url: str) -> str:
    if "game-list" in url:
        return az_html
    return game_html

with patch("gamarr.sources.freegog._nodriver_get", side_effect=_mock_nodriver):
    source.fetch_sitemap(db)
```

- [ ] **Step 3: Run all freegog tests**

Run: `uv run pytest tests/unit/test_freegog.py -v --tb=short`
Expected: All 31 tests pass.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest -v --tb=short`
Expected: All tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_freegog.py
git commit -m "test: update FreeGOG mocks for nodriver"
```

---

### Verification

After all tasks, run the full QA suite:

```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy .
uv run pytest -v
pre-commit run --all-files
```
