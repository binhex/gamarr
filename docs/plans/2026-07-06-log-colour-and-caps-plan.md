# Log Phase Colour & Source Name Capitalisation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add colour-highlighted phase banners and standardise FreeGOG/FitGirl capitalisation in all log output.

**Architecture:** Inline Loguru `<color>` markup on 5 phase banner `logger.info()` calls in `pipeline.py`, plus a small `_source_display()` lookup function that maps internal lowercase source names to their display-cased equivalents (`"fitgirl"` → `"FitGirl"`, `"freegog"` → `"FreeGOG"`), applied at 4 log sites.

**Tech Stack:** Python 3.12+, loguru, pytest

---

### Task 1: Write `_source_display()` helper + test (RED → GREEN)

**Files:**
- Create: `_source_display()` in `src/gamarr/pipeline.py:630` (after `_title_matches_reject`)
- Test: `tests/unit/test_pipeline.py` — insert after `TestEscapeOr` class (before `TestMetacriticBrowse`)

- [ ] **Step 1: Write the failing tests for `_source_display()`**

Insert after the `TestEscapeOr` class (before `TestMetacriticBrowse`) in `tests/unit/test_pipeline.py`:

```python
class TestSourceDisplay:
    """_source_display helper for readable source names in log output."""

    def test_source_display_fitgirl(self) -> None:
        from gamarr.pipeline import _source_display

        assert _source_display("fitgirl") == "FitGirl"

    def test_source_display_freegog(self) -> None:
        from gamarr.pipeline import _source_display

        assert _source_display("freegog") == "FreeGOG"

    def test_source_display_unknown_falls_back_to_title(self) -> None:
        from gamarr.pipeline import _source_display

        assert _source_display("steam") == "Steam"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_pipeline.py::TestSourceDisplay -v
```

Expected: 3 FAIL (ImportError — `_source_display` not defined)

- [ ] **Step 3: Implement `_source_display()`**

Insert after `_title_matches_reject` (between lines 634 and 637) of `src/gamarr/pipeline.py`:

```python
_SOURCE_DISPLAY: dict[str, str] = {"fitgirl": "FitGirl", "freegog": "FreeGOG"}


def _source_display(name: str) -> str:
    """Return the display-cased form of a source name."""
    return _SOURCE_DISPLAY.get(name, name.title())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_pipeline.py::TestSourceDisplay -v
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: add _source_display() helper for log-ready source names"
```

---

### Task 2: Apply `_source_display()` at 4 log sites

**Files:**
- Modify: `src/gamarr/pipeline.py` — lines 548, 1841-1843, 1848-1850, 1993

- [ ] **Step 1: Replace Site 1 — no-match message (line 1841-1843)**

Change from:
```python
        logger.info(
            "'{}' passed Metacritic checks but has no {} match \u2014 staying in queue",
            game_title,
            source_name,
        )
```

To:
```python
        logger.info(
            "'{}' passed Metacritic checks but has no {} match \u2014 staying in queue",
            game_title,
            _source_display(source_name),
        )
```

- [ ] **Step 2: Replace Site 2 — match message (line 1848-1850)**

Change from:
```python
    logger.info(
        "{} match: '{}' \u2192 '{}' ({})",
        source_name.title(),
        game_title,
        best["title"],
        best["url"],
    )
```

To:
```python
    logger.info(
        "{} match: '{}' \u2192 '{}' ({})",
        _source_display(source_name),
        game_title,
        best["title"],
        best["url"],
    )
```

- [ ] **Step 3: Replace Site 3 — per-source match count (line 548)**

Change from:
```python
                    logger.info("{} queued games found on {}", len(source_matched), source_entry.name)
```

To:
```python
                    logger.info("{} queued games found on {}", len(source_matched), _source_display(source_entry.name))
```

- [ ] **Step 4: Replace Site 4 — result_details for no-downloader mode (line 1993)**

Change from:
```python
        result_details=f"Matched on {source_name}: {best['url']}",
```

To:
```python
        result_details=f"Matched on {_source_display(source_name)}: {best['url']}",
```

- [ ] **Step 5: Run the full test suite**

```bash
uv run pytest tests/unit/test_pipeline.py -v
```

Expected: All tests pass (capitalisation changes don't break any assertions — no tests check exact source name casing in log output).

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "fix: use _source_display() for proper FreeGOG/FitGirl caps in log output"
```

---

### Task 3: Colourise the 5 phase banners

**Files:**
- Modify: `src/gamarr/pipeline.py` — lines 526, 528, 533, 550, 565

- [ ] **Step 1: Run current tests to establish baseline**

```bash
uv run pytest tests/unit/test_pipeline.py -x -q
```

Expected: All pass.

- [ ] **Step 2: Replace Phase 2 banner (line 526)**

Change from:
```python
                    logger.info("--- Phase 2/5: Indexing FreeGOG ---")
```

To:
```python
                    logger.info("<cyan>━━━ Phase 2/5: Indexing FreeGOG ━━━</>")
```

- [ ] **Step 3: Replace Phase 3 banner (line 528)**

Change from:
```python
                    logger.info("--- Phase 3/5: Indexing FitGirl ---")
```

To:
```python
                    logger.info("<light-red>━━━ Phase 3/5: Indexing FitGirl ━━━</>")
```

- [ ] **Step 4: Replace Phase 4 banner (line 533)**

Change from:
```python
            logger.info("--- Phase 4/5: Matching games to download sources ---")
```

To:
```python
            logger.info("<magenta>━━━ Phase 4/5: Matching games to download sources ━━━</>")
```

- [ ] **Step 5: Replace Phase 5 banner (line 550)**

Change from:
```python
        logger.info("--- Phase 5/5: Delivering to qBittorrent ---")
```

To:
```python
        logger.info("<blue>━━━ Phase 5/5: Delivering to qBittorrent ━━━</>")
```

- [ ] **Step 6: Replace Phase 1 banner (line 565)**

Change from:
```python
    logger.info("--- Phase 1/5: Discovering games on Metacritic ---")
```

To:
```python
    logger.info("<yellow>━━━ Phase 1/5: Discovering games on Metacritic ━━━</>")
```

- [ ] **Step 7: Run the full test suite to verify nothing broke**

```bash
uv run pytest tests/unit/test_pipeline.py -v
```

Expected: All pass (no tests assert on colour markup text).

- [ ] **Step 8: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: colour-highlighted phase banners in log output"
```

---

### Task 4: Final verification

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest -v
```

Expected: 587+ tests pass (all unchanged).

- [ ] **Step 2: Quick manual check — confirm phase banners render with colour**

```bash
cd /data/gamarr && python -c "
from loguru import logger
import sys
logger.remove()
logger.add(sys.stderr, colorize=True, format='{message}')
logger.info('<yellow>━━━ Phase 1/5: Discovering games on Metacritic ━━━</>')
logger.info('<cyan>━━━ Phase 2/5: Indexing FreeGOG ━━━</>')
logger.info('<light-red>━━━ Phase 3/5: Indexing FitGirl ━━━</>')
logger.info('<magenta>━━━ Phase 4/5: Matching games to download sources ━━━</>')
logger.info('<blue>━━━ Phase 5/5: Delivering to qBittorrent ━━━</>')
print('If you see coloured banners above, Loguru markup is working.')
"
```

Expected: Five colour-highlighted phase banners render in the terminal.

- [ ] **Step 3: Commit (if anything remaining)**

```bash
git status
```
