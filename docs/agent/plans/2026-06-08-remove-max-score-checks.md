# Remove `max_score_checks` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `max_score_checks` config field and use `max_games` in its place for capping pending-game verification.

**Architecture:** Delete the field from `MetacriticPlatformConfig` (Pydantic) and `AcquisitionConfig` (dataclass), remove from `run_acquisition()` signature, replace the one usage site in `_verify_pending_scores` call, update migration to drop old keys, remove scheduler kwarg, remove from config file, and update tests.

**Tech Stack:** Python 3.12+, Pydantic v2, `uv`, `pytest`

**Spec:** `docs/superpowers/specs/2026-06-08-remove-max-score-checks-design.md`

---

### Task 1: Remove `max_score_checks` from `MetacriticPlatformConfig` and update migration

**Files:**
- Modify: `src/gamarr/config.py` (lines 70, 175, 178)
- Test: `tests/unit/test_config.py` (line 54, migration tests around lines 56-125)

- [ ] **Step 1: Remove the field from the model**

In `src/gamarr/config.py`, remove the `max_score_checks` line from `MetacriticPlatformConfig`:

```python
# Before (lines 69-70)
    max_games: int = Field(default=1000, ge=0, le=20000)
    max_score_checks: int = Field(default=200, ge=0, le=10000)

# After
    max_games: int = Field(default=1000, ge=0, le=20000)
```

- [ ] **Step 2: Update `_migrate_config` — drop `max_score_checks`, consolidate `browse_max_pages`**

In `src/gamarr/config.py`, inside `_migrate_config()`, replace the `_rename_config_key(mc_pc, "browse_max_pages", None, platform_key)` call with a deprecation loop that also handles `max_score_checks`:

```python
# Before (line 175)
            _rename_config_key(mc_pc, "browse_max_pages", None, platform_key)

# After — replace the single line with:
            # Deprecated: browse_max_pages, max_score_checks — warn and drop
            for old_key in ("browse_max_pages", "max_score_checks"):
                if old_key in mc_pc:
                    logger.warning(
                        "Config: '{}' is deprecated for platform '{}'; "
                        "use 'max_games' instead. Ignoring value.",
                        old_key,
                        platform_key,
                    )
                    mc_pc.pop(old_key)
```

- [ ] **Step 3: Update the defaults test**

In `tests/unit/test_config.py`, remove the `max_score_checks` default assertion:

```python
# Before (lines 53-54)
        assert cfg.max_games == 1000
        assert cfg.max_score_checks == 200

# After
        assert cfg.max_games == 1000
```

- [ ] **Step 4: Run config tests**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_config.py -v
```

Expected: All config tests pass (the migration tests still verify that `browse_max_pages` and `metacritic_max_games` are handled correctly).

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "refactor: remove max_score_checks from config model + migration"
```

---

### Task 2: Remove `max_score_checks` from `AcquisitionConfig`, `run_acquisition`, and the pipeline usage

**Files:**
- Modify: `src/gamarr/pipeline.py` (lines 108, 173, 203, 320-322, and docstring)

- [ ] **Step 1: Remove from `AcquisitionConfig` dataclass**

```python
# Before (lines 107-108)
    max_games: int = 1000
    max_score_checks: int = 200

# After
    max_games: int = 1000
```

- [ ] **Step 2: Remove from `run_acquisition()` signature**

```python
# Before (lines 172-173)
    max_games: int = 1000,
    max_score_checks: int = 200,

# After
    max_games: int = 1000,
```

- [ ] **Step 3: Remove from `AcquisitionConfig(...)` constructor call**

```python
# Before (lines 202-203)
        max_games=max_games,
        max_score_checks=max_score_checks,

# After
        max_games=max_games,
```

- [ ] **Step 4: Replace usage at the verify-limit site**

```python
# Before (lines 319-322)
                max_verify=len(pending_games)
                if cfg.max_score_checks == 0
                else min(len(pending_games), cfg.max_score_checks),

# After
                max_verify=len(pending_games)
                if cfg.max_games == 0
                else min(len(pending_games), cfg.max_games),
```

- [ ] **Step 5: Update the `run_acquisition()` docstring**

The docstring currently says:
```
    ``max_games`` entries), verifies each game's real
```

It doesn't mention `max_score_checks`, so no change needed there. But check for any other references — search the docstring:

```bash
cd /data/gamarr && grep -n "max_score_checks\|max_games" src/gamarr/pipeline.py | head -5
```

If the only remaining `max_score_checks` reference(s) are the ones just replaced, you're done.

- [ ] **Step 6: Run pipeline tests**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py -v
```

Expected: All pipeline tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "refactor: remove max_score_checks from pipeline, use max_games instead"
```

---

### Task 3: Remove `max_score_checks` from scheduler wiring

**Files:**
- Modify: `src/gamarr/scheduler.py` (line 59)

- [ ] **Step 1: Remove the kwarg**

```python
# Before (lines 58-59)
        "max_games": mc_cfg.max_games,
        "max_score_checks": mc_cfg.max_score_checks,

# After
        "max_games": mc_cfg.max_games,
```

- [ ] **Step 2: Run scheduler tests**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_scheduler.py -v
```

Expected: All scheduler tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/scheduler.py
git commit -m "refactor: remove max_score_checks from scheduler wiring"
```

---

### Task 4: Remove `max_score_checks` from config file

**Files:**
- Modify: `configs/gamarr.yml` (line 34)

- [ ] **Step 1: Remove the line**

```yaml
# Before (lines 33-34)
      max_games: 0
      max_score_checks: 0

# After
      max_games: 0
```

Note: `configs/gamarr.yml` is gitignored (contains user passwords). The change applies locally but cannot be committed.

---

### Task 5: Update pipeline tests that reference `max_score_checks`

**Files:**
- Modify: `tests/unit/test_pipeline.py` (lines 2206-2219)

- [ ] **Step 1: Remove the `test_config_allows_zero_score_checks` test**

Delete the entire test method — it tested that `MetacriticPlatformConfig(max_score_checks=0)` works, which is no longer a valid constructor argument:

```python
# Before (lines 2206-2214)
    def test_config_allows_zero_score_checks(self) -> None:
        """max_score_checks=0 must be accepted by the config model.

        A value of 0 means "unlimited" — score-check all pending games.
        """
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig(max_score_checks=0)
        assert cfg.max_score_checks == 0

# After — delete the entire method
```

- [ ] **Step 2: Update `test_verify_pending_max_checks_zero_passes_all_games` docstring**

Update the docstring to reference `max_games` instead of `max_score_checks`:

```python
# Before (lines 2216-2219)
    def test_verify_pending_max_checks_zero_passes_all_games(self, tmp_path: Path) -> None:
        """When max_score_checks=0, _verify_pending_scores should check ALL games.

        The pipeline passes len(pending_games) as max_verify when max_score_checks=0.
        """

# After
    def test_verify_pending_max_checks_zero_passes_all_games(self, tmp_path: Path) -> None:
        """When max_games=0, _verify_pending_scores should check ALL games.

        The pipeline passes len(pending_games) as max_verify when max_games=0.
        """
```

- [ ] **Step 3: Update the inline comment in the test body**

```python
# Before (line 2261)
        # when max_score_checks=0 (unlimited)

# After
        # when max_games=0 (unlimited)
```

- [ ] **Step 4: Run pipeline tests**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py -v
```

Expected: All pipeline tests pass (the test body didn't change logic, just comments).

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: remove max_score_checks tests, update docstrings to reference max_games"
```

---

### Task 6: Full QC verification

**Files:** (no file changes — just running checks)

- [ ] **Step 1: Run full test suite**

```bash
cd /data/gamarr && uv run pytest --no-cov
```

Expected: All tests pass (should be 1 fewer test than before — the deleted `test_config_allows_zero_score_checks`).

- [ ] **Step 2: Run coverage**

```bash
cd /data/gamarr && uv run pytest --cov=src/gamarr --cov-fail-under=80
```

Expected: Coverage passes.

- [ ] **Step 3: Run linter and type checker**

```bash
cd /data/gamarr && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/gamarr/
```

Expected: All checks pass.

- [ ] **Step 4: Verify no stale references remain**

```bash
cd /data/gamarr && grep -rn "max_score_checks" src/ --include="*.py"
```

Expected: No output — all references removed from source code.
