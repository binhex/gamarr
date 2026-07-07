# max_weeks → max_pages Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename `max_weeks` config key to `max_pages` (default 500), with auto-migration and removal of related date-based filtering.

**Architecture:** Same pattern as `max_cycle_weeks → max_cycle_pages` — rename in config, add `_rename_config_key` migration, thread through pipeline/scheduler, remove age filter and hard_cutoff computation, clean up dead DB methods.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy, pytest

---

### Task 1: Config — field rename + migration (config.py)

**Files:**
- Modify: `src/gamarr/config.py:111`

- [ ] **Step 1: Replace the field definition**

Replace line 111:

```python
max_weeks: int | None = Field(default=13, ge=0)
```

With:

```python
max_pages: int = Field(default=500, ge=1)
```

- [ ] **Step 2: Add migration rename**

Add the rename line to the migration chain (find the for-loop that iterates platform overrides and adds `_rename_config_key` calls around line 250-265). Add after the `_drop_max_verify_attempts` call:

```python
changed |= _rename_config_key(mc_pc, "max_weeks", "max_pages", platform_key)
```

Also update `_migrate_days_since_release` (line ~396) to write to `max_pages`:

```python
mc_pc["max_pages"] = max(1, round(days / 7))
```

And update `_migrate_cutoff_weeks_to_max_weeks` (line ~500) to write to `max_pages`:

```python
mc_pc["max_pages"] = mc_pc.pop("cutoff_weeks")
```

- [ ] **Step 3: Update migration comment strings**

In the `_drop_metacritic_max_games` and related functions, update comment strings mentioning "use max_weeks" to "use max_pages". These are `logger.info` messages at lines ~271, ~285, ~295, ~399, ~502, ~521.

- [ ] **Step 4: Run config tests**

Run: `pytest tests/unit/test_config.py -q`
Expected: all tests pass (some may need updating in later tasks)

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py
git commit -m "feat: rename max_weeks to max_pages in config with migration"
```

---

### Task 2: Pipeline — AcquisitionConfig and run_acquisition (pipeline.py)

**Files:**
- Modify: `src/gamarr/pipeline.py:115,124-126,147,181,257,273-277,280,291,334,412,640`

- [ ] **Step 1: Rename in AcquisitionConfig**

Replace line 115:

```python
max_weeks: int | None = None
```

With:

```python
max_pages: int | None = None
```

Remove lines 124-126 (the `_age_days` method):

```python
def _age_days(self) -> int:
    """Return the age filter in days, derived from max_weeks."""
    return (self.max_weeks or 0) * 7
```

- [ ] **Step 2: Rename in run_acquisition signature**

Replace line 147:

```python
max_weeks: int | None = None,
```

With:

```python
max_pages: int | None = None,
```

Replace line 181:

```python
max_weeks=max_weeks,
```

With:

```python
max_pages=max_pages,
```

Remove the `days_since_release` parameter from `run_acquisition` (if present in the signature near line 147). Check the existing signature and remove if present.

- [ ] **Step 3: Remove hard_cutoff computation in _run_discovery_phases**

Replace lines 253-277 (the `# Compute the cutoff` through `cutoff_date = hard_cutoff.isoformat()`):

```python
            # Compute the cutoff from max_weeks.  max_cycle_pages controls
            # how many browse pages are scanned per cycle (the scan_recent_games
            # function stops after that many pages).
            cutoff_date: str | None = None
            # Apply hard cutoff: clamp the cutoff to never go past
            # max_weeks.
            if cfg.max_weeks is not None and cfg.max_weeks > 0:
                hard_cutoff = datetime.datetime.now(tz=datetime.UTC).date() - datetime.timedelta(weeks=cfg.max_weeks)
                if cutoff_date is None or cutoff_date < hard_cutoff.isoformat():
                    cutoff_date = hard_cutoff.isoformat()
```

With:

```python
            # cutoff_date is always None — page-count based limit is handled
            # by max_cycle_pages passed to scan_recent_games.
            cutoff_date: str | None = None
```

- [ ] **Step 4: Remove cutoff_date log and year scan logic**

Remove lines 280-291 (the `if cutoff_date is not None: logger.info("Scan window: ...")` block) and remove lines 291-300 (the `if cutoff_date is not None:` year computation block). Replace with just the year computation:

```python
            scan_year = datetime.datetime.now(tz=datetime.UTC).year
```

- [ ] **Step 5: Remove days_since_release from _process_browse_games call**

Find the call to `_process_browse_games` in `_run_discovery_phases` near line 334. Remove the `days_since_release=cfg._age_days(),` line:

```python
                new_pending = _process_browse_games(
                    browse_games,
                    platform,
                    db,
                    thresholds,
                    max_queue_days=cfg.max_queue_days,
                    # days_since_release=cfg._age_days(),  ← REMOVED
                    reject_title=cfg.reject_title,
                )
```

- [ ] **Step 6: Remove _is_older_than call from _process_browse_games**

Find line 640 in `_process_browse_games` where `_is_older_than` is called with `days_since_release`. Remove the `days_since_release` parameter from the function signature and remove the `if _is_older_than(game.get("release_date"), days_since_release):` check at line 640.

Note: `_is_older_than` at lines 986 and 999 is used for `age_recheck_weeks` — do NOT remove those calls or the function definition.

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: rename max_weeks to max_pages in pipeline, remove age filter"
```

---

### Task 3: Scheduler — rename kwargs (scheduler.py)

**Files:**
- Modify: `src/gamarr/scheduler.py:170`

- [ ] **Step 1: Rename in _build_kwargs**

Replace line 170:

```python
"max_weeks": mc_cfg.max_weeks,
```

With:

```python
"max_pages": mc_cfg.max_pages,
```

- [ ] **Step 2: Run scheduler tests**

Run: `pytest tests/unit/test_scheduler.py -q`
Expected: all pass after test updates in Task 8

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/scheduler.py
git commit -m "feat: rename max_weeks to max_pages in scheduler"
```

---

### Task 4: Database — remove dead methods (database.py)

**Files:**
- Modify: `src/gamarr/database.py:312-322`

- [ ] **Step 1: Remove get_last_max_weeks and set_last_max_weeks**

Remove lines 312-322 which contain `get_last_max_weeks` and `set_last_max_weeks` methods. These are dead code — never called since the backlog retreat system was simplified.

- [ ] **Step 2: Run database tests**

Run: `pytest tests/unit/test_database.py -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/database.py
git commit -m "chore: remove dead get_last_max_weeks/set_last_max_weeks"
```

---

### Task 5: Update test_config.py

**Files:**
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Rename all max_weeks references to max_pages**

In test_config.py, replace all `max_weeks` with `max_pages`:

```bash
sed -i 's/max_weeks/max_pages/g; s/max_pages_defaults_to_13/max_pages_defaults_to_500/; s/cfg\.max_pages == 13/cfg.max_pages == 500/' tests/unit/test_config.py
```

- [ ] **Step 2: Update default assertions**

Find `TestMetacriticPlatformConfigDefaults` or equivalent tests. Update:
- `assert cfg.max_pages == 500` (was `13`)
- `test_max_pages_ge_zero` → update to test `ge=1` (minimum 1, not 0)

- [ ] **Step 3: Run config tests**

Run: `pytest tests/unit/test_config.py -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_config.py
git commit -m "test: rename max_weeks to max_pages in config tests"
```

---

### Task 6: Update test_pipeline.py

**Files:**
- Modify: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Rename all max_weeks references to max_pages**

```bash
sed -i 's/max_weeks/max_pages/g' tests/unit/test_pipeline.py
```

- [ ] **Step 2: Remove tests that test age filtering**

Find and remove/simplify tests that:
- Call `run_acquisition(..., max_pages=13, ...)` expecting age-filtering behavior
- Test `_age_days()` method
- Assert on `cutoff_date` being date-derived (now call `max_pages` instead)
- Assert on `_is_older_than` being called from `_process_browse_games`

The key tests to update:
- `TestMaxCycleWeeks` class — rename references
- `TestScanWindowAdvancing` — rename + update expected values
- Any test that passes `max_weeks` to `run_acquisition` — rename the kwarg

- [ ] **Step 3: Run pipeline tests**

Run: `pytest tests/unit/test_pipeline.py -q`
Expected: all pass after updates

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: rename max_weeks to max_pages in pipeline tests"
```

---

### Task 7: Update test_scheduler.py

**Files:**
- Modify: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Rename all max_weeks references to max_pages**

```bash
sed -i 's/max_weeks/max_pages/g' tests/unit/test_scheduler.py
```

- [ ] **Step 2: Update kwargs assertion**

Find the test for `_build_kwargs_includes_max_pages`. Update:

```python
assert kwargs["max_pages"] == 0  # default is 0 from AcquisitionConfig
```

- [ ] **Step 3: Run scheduler tests**

Run: `pytest tests/unit/test_scheduler.py -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_scheduler.py
git commit -m "test: rename max_weeks to max_pages in scheduler tests"
```

---

### Task 8: Full test suite verification

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest -q
```
Expected: all 614+ tests pass

- [ ] **Step 2: Run ruff lint**

```bash
uv run ruff check . && uv run ruff format .
```
Expected: all checks pass

- [ ] **Step 3: Run mypy**

```bash
uv run mypy src/gamarr/config.py src/gamarr/pipeline.py src/gamarr/scheduler.py src/gamarr/database.py
```
Expected: no issues found

- [ ] **Step 4: Run CRAP analysis**

```bash
uv run pytest --cov --crap --crap-threshold=9 -q
```
Expected: 0 functions above threshold

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup after max_weeks to max_pages rename"
```
