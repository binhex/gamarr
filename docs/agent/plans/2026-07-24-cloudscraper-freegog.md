# cloudscraper FreeGOG Cloudflare Bypass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `requests` with `cloudscraper` in the FreeGOG source so it bypasses Cloudflare Turnstile protection on `freegogpcgames.com`.

**Architecture:** cloudscraper is a drop-in replacement for `requests` with the same API. A single session is created at `FreeGOGSource.__init__` and reused across all HTTP calls. The scraper handles browser TLS fingerprint impersonation and JS challenge solving automatically.

**Tech Stack:** cloudscraper (PyPI), Python 3.12+, uv

---

### Task 1: Add cloudscraper Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add cloudscraper to dependencies**

Locate the `dependencies` list in `pyproject.toml`. Add `"cloudscraper"` at the end:

```diff
 dependencies = [
     ...
     "apprise",
+    "cloudscraper",
 ]
```

- [ ] **Step 2: Install and verify**

Run: `uv sync`
Expected: cloudscraper installs without errors. Verify with `uv run python -c "import cloudscraper; print(cloudscraper.__version__)"`

- [ ] **Step 3: Run existing tests to confirm no breakage from dependency alone**

Run: `uv run pytest tests/unit/test_freegog.py -v`
Expected: Same pass/fail as before (may still pass if nothing imports cloudscraper at test time; the mock target change happens in Task 2).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add cloudscraper for Cloudflare bypass"
```

---

### Task 2: Replace requests with cloudscraper in freegog.py

**Files:**
- Modify: `src/gamarr/sources/freegog.py`
- Modify: `tests/unit/test_freegog.py` (mock target updates)

- [ ] **Step 1: Replace import**

In `src/gamarr/sources/freegog.py`, change:

```python
import requests
```

to:

```python
import cloudscraper
```

Remove the `_USER_AGENT` constant (no longer needed — cloudscraper handles browser impersonation).

- [ ] **Step 2: Add scraper session to FreeGOGSource.__init__**

Add after the existing init assignments:

```python
self._scraper = cloudscraper.create_scraper()
```

- [ ] **Step 3: Replace requests.get in _index_az_page**

Change:

```python
resp = requests.get(url, timeout=30, headers={"User-Agent": _USER_AGENT})
```

to:

```python
resp = self._scraper.get(url, timeout=30)
```

- [ ] **Step 4: Replace requests.get in _fetch_and_store_game**

Change:

```python
game_resp = requests.get(
    entry["url"],
    timeout=30,
    headers={"User-Agent": _USER_AGENT},
)
```

to:

```python
game_resp = self._scraper.get(entry["url"], timeout=30)
```

- [ ] **Step 5: Update test mocks in test_freegog.py**

All tests currently mock `gamarr.sources.freegog.requests.get`. They must now mock the scraper's `.get()` instead. The pattern changes from:

```python
# Before:
with patch("gamarr.sources.freegog.requests.get") as mock_get:
    source = FreeGOGSource(db_path=":memory:", db=db)
    source.fetch_sitemap(db)
    mock_get.assert_called_with(...)
```

to:

```python
# After:
source = FreeGOGSource(db_path=":memory:", db=db)
source._scraper.get = MagicMock()
source.fetch_sitemap(db)
source._scraper.get.assert_called_with(...)
```

Replace every occurrence of `patch("gamarr.sources.freegog.requests.get")` with the pattern above. There are 7 test functions that use this mock across the `TestFreeGOGFetchSitemap` and `TestFreeGOGFetchGame` classes. Each one needs to:
1. Remove the `with patch(...)` context manager
2. Create the source first (or ensure `source` is in scope)
3. Replace `mock_get` references with `source._scraper.get`

For `MagicMock`, import it at the top:
```python
from unittest.mock import MagicMock, patch
```

- [ ] **Step 6: Remove _USER_AGENT from test imports/properties if referenced**

Check if any test references `_USER_AGENT`. If so, remove those references.

Run: `grep -n "_USER_AGENT" tests/unit/test_freegog.py`
If no results, no action needed.

- [ ] **Step 7: Run tests to verify**

Run: `uv run pytest tests/unit/test_freegog.py -v`
Expected: All tests pass.

- [ ] **Step 8: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass, no regressions.

- [ ] **Step 9: Commit**

```bash
git add src/gamarr/sources/freegog.py tests/unit/test_freegog.py
git commit -m "feat: use cloudscraper for FreeGOG Cloudflare bypass"
```

---

### Task 3: Edge case — RequestException import

**Files:**
- Modify: `src/gamarr/sources/freegog.py`

- [ ] **Step 1: Verify exception handling**

cloudscraper raises the same `requests.RequestException` hierarchy as `requests`. The `_fetch_and_store_game` method has:

```python
except requests.RequestException as exc:
```

Since we removed `import requests`, we need a way to reference `RequestException`. Import it from cloudscraper instead:

```python
from cloudscraper import RequestException as CloudscraperRequestException
```

Then update the exception handler:

```python
except CloudscraperRequestException as exc:
```

Or, simplify: cloudscraper re-exports `requests` exceptions. We can just do:

```python
import requests  # keep for exception types only
```

But that defeats the purpose. Let's check: `cloudscraper.exceptions` might have them, or we can import from the underlying `requests` that cloudscraper bundles.

Run: `uv run python -c "from requests import RequestException; print('available')"`

If `requests` is already a transitive dependency of cloudscraper, we can import from it directly:

```python
from requests import RequestException
```

If not, use:
```python
import cloudscraper
RequestException = cloudscraper.exceptions.CloudflareException
```

No — that's different. Let's just keep the import simple:

```python
from requests import RequestException  # requests is a dependency of cloudscraper
```

- [ ] **Step 2: Run tests to verify exception handler works**

Run: `uv run pytest tests/unit/test_freegog.py -v`
Expected: All tests pass (the ConnectionError test at line 423-433 verifies exception handling).

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/sources/freegog.py
git commit -m "fix: import RequestException for cloudscraper error handling"
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
