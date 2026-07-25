# SeleniumBase UC Mode FreeGOG Cloudflare Bypass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `requests.get()` in the FreeGOG source with SeleniumBase UC Mode to bypass Cloudflare Turnstile on `freegogpcgames.com`.

**Architecture:** A synchronous `_sb_uc_get()` wrapper uses SeleniumBase's SB manager (`uc=True`, `headless2=True`) which provides a virtual display on headless Linux. `uc_open_with_reconnect()` bypasses the Turnstile challenge and reconnects to the real page. `uc_gui_click_captcha()` auto-clicks any Cloudflare checkbox. Result is a plain HTML string fed to existing parsers.

**Tech Stack:** Python 3.12+, SeleniumBase (includes Chromedriver + Xvfb), pytest

---

### Task 1: Add seleniumbase dependency

**Files:**
- Modify: `/data/gamarr/pyproject.toml`

- [ ] **Step 1: Add seleniumbase to dependencies**

In the `dependencies` list, add `"seleniumbase"`:

```diff
     "apprise",
+    "seleniumbase",
     "pydantic>=2.0.0",
```

- [ ] **Step 2: Install and verify**

Run: `cd /data/gamarr && uv sync`
Expected: seleniumbase installs without errors.

Run: `uv run python -c "from seleniumbase import SB; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run existing tests to confirm no breakage**

Run: `uv run pytest tests/unit/test_freegog.py -v`
Expected: All 31 tests pass (seleniumbase not imported by freegog.py yet).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add seleniumbase for Cloudflare bypass"
```

---

### Task 2: Replace requests.get with SeleniumBase UC Mode in freegog.py

**Files:**
- Modify: `src/gamarr/sources/freegog.py`

- [ ] **Step 1: Add import and helper function**

Add import at the top of the file (after `import re`):

```python
from seleniumbase import SB
```

Add at module level (before `_EDITION_PATTERN`):

```python
def _sb_uc_get(url: str) -> str:
    """Fetch a URL through SeleniumBase UC Mode, returning HTML."""
    with SB(uc=True, headless2=True) as sb:
        sb.uc_open_with_reconnect(url, 4)
        sb.uc_gui_click_captcha()
        return sb.get_page_source()
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
            html = _sb_uc_get(url)
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
            html = _sb_uc_get(entry["url"])
            magnet = _extract_magnet_from_freegog_page(html)
```

- [ ] **Step 5: Update exception handlers**

Change `except requests.RequestException as exc:` to `except Exception as exc:` in both methods — SeleniumBase raises its own exception types.

- [ ] **Step 6: Remove unused import requests (if no longer needed)**

If `import requests` is no longer used anywhere in the file, remove it. Keep it if used elsewhere (check for `requests` in the rest of the file).

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/sources/freegog.py
git commit -m "feat: replace requests.get with SeleniumBase UC Mode for FreeGOG"
```

---

### Task 3: Update test mocks for _sb_uc_get

**Files:**
- Modify: `tests/unit/test_freegog.py`

- [ ] **Step 1: Update mock strategy**

All tests that mock `requests.get` must now mock `_sb_uc_get` — a module-level function that returns a plain HTML string.

The pattern changes from:

```python
with patch("gamarr.sources.freegog.requests.get") as mock_get:
    mock_get.return_value = mock_response
    source.fetch_sitemap(db)
    mock_get.assert_called_once()
```

to:

```python
with patch("gamarr.sources.freegog._sb_uc_get") as mock_get:
    mock_get.return_value = "<html>raw html content</html>"
    source.fetch_sitemap(db)
    mock_get.assert_called_once_with(url)
```

Key differences:
- Mock target: `gamarr.sources.freegog._sb_uc_get` instead of `gamarr.sources.freegog.requests.get`
- Return value: plain `str` (HTML) instead of a `MockResponse` object
- No `.text` or `.raise_for_status()` — parsers get the string directly

For tests returning per-URL HTML, use a `side_effect` function:

```python
def _mock_sb_get(url: str) -> str:
    if "game-list" in url:
        return az_html
    return game_html

with patch("gamarr.sources.freegog._sb_uc_get", side_effect=_mock_sb_get):
    source.fetch_sitemap(db)
```

For the failure test (`test_handles_az_failure`):
```python
mock_get.side_effect = Exception("Connection error")
```

- [ ] **Step 2: Update each mock patch**

There are ~5 test methods that mock `requests.get`. Update each one in `TestFreeGOGFetchSitemap` and `TestFreeGOGFetchSitemapResponse` classes by replacing the mock target and simplifying return values.

- [ ] **Step 3: Run all freegog tests**

Run: `uv run pytest tests/unit/test_freegog.py -v --tb=short`
Expected: All 31 tests pass.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest -v --tb=short`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_freegog.py
git commit -m "test: update FreeGOG mocks for SeleniumBase UC Mode"
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
