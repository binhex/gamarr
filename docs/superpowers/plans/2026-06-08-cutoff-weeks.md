# cutoff_weeks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static `cutoff_date: str | None` config with `cutoff_weeks: int | None` that computes the cutoff as *today minus N weeks* at runtime.

**Architecture:** Rename the field in two Pydantic/dataclass models, remove the old migration renames, add a deprecation warning for stale `cutoff_date` values, compute the ISO date string from weeks in the pipeline's `_run_discovery_phases()`, and update the scheduler wiring and config file. The Metacritic client API (which accepts ISO date strings) is unchanged.

**Tech Stack:** Python 3.12+, Pydantic v2, `datetime`, `uv`, `pytest`

**Spec:** `docs/superpowers/specs/2026-06-08-cutoff-weeks-design.md`

---

### Task 1: Rename field in `MetacriticPlatformConfig` and update migration

**Files:**
- Modify: `src/gamarr/config.py` (line 71 + lines 176-183)
- Test: `tests/unit/test_config.py` (lines 60-124)

- [ ] **Step 1: Update the model field**

In `src/gamarr/config.py`, change the `MetacriticPlatformConfig` field:

```python
# Before (line 71)
cutoff_date: str | None = None

# After
cutoff_weeks: int | None = None
```

- [ ] **Step 2: Update `_migrate_config` — remove old cutoff renames, add deprecation warning**

In `src/gamarr/config.py`, inside `_migrate_config()`, find the two `_rename_config_key` calls that target `"cutoff_date"` and replace them with a deprecation block:

```python
# Before — lines 176 and 180 (remove both):
            _rename_config_key(mc_pc, "browse_cutoff_date", "cutoff_date", platform_key)
            ...
            _rename_config_key(mc_pc, "metacritic_cutoff_date", "cutoff_date", platform_key)

# After — replace with this block (same indentation, inside the for loop):
            # Deprecated: cutoff_date — warn and drop
            for old_key in ("browse_cutoff_date", "metacritic_cutoff_date", "cutoff_date"):
                if old_key in mc_pc:
                    logger.warning(
                        "Config: '{}' is deprecated for platform '{}'; "
                        "set 'cutoff_weeks' instead (e.g. cutoff_weeks: 52 for \u223c1 year). "
                        "Ignoring value.",
                        old_key,
                        platform_key,
                    )
                    mc_pc.pop(old_key)
```

This single loop handles all three variants: `browse_cutoff_date`, `metacritic_cutoff_date`, and the bare `cutoff_date`.

- [ ] **Step 3: Update the migration tests**

In `tests/unit/test_config.py`, update two tests to expect the old keys are dropped without being preserved:

Test `test_migrate_config_renames_browse_keys` (around line 60):

```python
    def test_migrate_config_renames_browse_keys(self) -> None:
        """_migrate_config should rename browse_* keys to bare names
        and drop deprecated cutoff_date keys."""
        from gamarr.config import _migrate_config

        raw = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {
                        "browse_max_pages": 200,
                        "browse_enabled": True,
                        "browse_cutoff_date": "2025-01-01",
                        "browse_cache_ttl_hours": 4,
                    }
                }
            }
        }
        _migrate_config(raw)
        mc_pc = raw["metacritic"]["platform_overrides"]["pc"]
        assert "browse_max_pages" not in mc_pc
        assert "browse_enabled" not in mc_pc
        assert "browse_cutoff_date" not in mc_pc
        assert "browse_cache_ttl_hours" not in mc_pc
        assert mc_pc["enabled"] is True
        # cutoff_date is deprecated — should be dropped, not preserved
        assert "cutoff_date" not in mc_pc
        assert mc_pc["cache_ttl_hours"] == 4
```

Test `test_migrate_config_renames_metacritic_keys` (around line 106):

```python
    def test_migrate_config_renames_metacritic_keys(self) -> None:
        """_migrate_config should rename metacritic_* keys to bare names
        and drop deprecated cutoff_date keys."""
        from gamarr.config import _migrate_config

        raw = {
            "metacritic": {
                "platform_overrides": {
                    "pc": {
                        "metacritic_enabled": False,
                        "metacritic_max_games": 500,
                        "metacritic_cutoff_date": "2026-06-01",
                        "metacritic_cache_ttl_hours": 12,
                    }
                }
            }
        }
        _migrate_config(raw)
        mc_pc = raw["metacritic"]["platform_overrides"]["pc"]
        assert "metacritic_enabled" not in mc_pc
        assert "metacritic_max_games" not in mc_pc
        assert "metacritic_cutoff_date" not in mc_pc
        assert "metacritic_cache_ttl_hours" not in mc_pc
        assert mc_pc["enabled"] is False
        assert mc_pc["max_games"] == 500
        # cutoff_date is deprecated — should be dropped, not preserved
        assert "cutoff_date" not in mc_pc
        assert mc_pc["cache_ttl_hours"] == 12
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_config.py -v
```

Expected: All config tests pass (including the two updated migration tests).

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "refactor: rename cutoff_date to cutoff_weeks in config + migration"
```

---

### Task 2: Rename field in `AcquisitionConfig` and `run_acquisition` parameter

**Files:**
- Modify: `src/gamarr/pipeline.py` (lines 109, 202)

- [ ] **Step 1: Rename in `AcquisitionConfig` dataclass**

In `src/gamarr/pipeline.py`:

```python
# Before (line 109)
cutoff_date: str | None = None

# After
cutoff_weeks: int | None = None
```

- [ ] **Step 2: Rename in `run_acquisition()` signature and `AcquisitionConfig` construction**

In `src/gamarr/pipeline.py`, change the function parameter:

```python
# Before (line 174)
    cutoff_date: str | None = None,

# After
    cutoff_weeks: int | None = None,
```

And the `AcquisitionConfig(...)` constructor call inside `run_acquisition()` (around line 202):

```python
# Before
        cutoff_date=cutoff_date,

# After
        cutoff_weeks=cutoff_weeks,
```

- [ ] **Step 3: Run tests to verify nothing is broken**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py -v
```

Expected: All 46+ pipeline tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "refactor: rename cutoff_date to cutoff_weeks in AcquisitionConfig and run_acquisition"
```

---

### Task 3: Compute cutoff date from weeks in `_run_discovery_phases`

**Files:**
- Modify: `src/gamarr/pipeline.py` (around line 260)

- [ ] **Step 1: Add the weeks→date computation**

In `src/gamarr/pipeline.py`, inside `_run_discovery_phases()`, find the `mc.scan_recent_games(...)` call (around line 260) and add a computed `cutoff_date` variable:

```python
        browse_games: list[dict[str, Any]] = []
        if cfg.enabled:
            # Compute absolute cutoff date from cutoff_weeks (if set and > 0)
            cutoff_date: str | None = None
            if cfg.cutoff_weeks is not None and cfg.cutoff_weeks > 0:
                cutoff_date = (
                    datetime.datetime.now(tz=datetime.UTC).date()
                    - datetime.timedelta(weeks=cfg.cutoff_weeks)
                ).isoformat()

            browse_games = mc.scan_recent_games(
                platform,
                max_games=cfg.max_games,
                cache_ttl_hours=cfg.cache_ttl_hours,
                cutoff_date=cutoff_date,  # ← was cfg.cutoff_date
            )
```

The change from `cfg.cutoff_date` to the local `cutoff_date` variable is the only difference in the `scan_recent_games` call.

- [ ] **Step 2: Run full test suite**

```bash
cd /data/gamarr && uv run pytest --no-cov -v
```

Expected: All 262 tests pass. No regressions (the Metacritic client API is unchanged, so the cutoff filtering tests in `test_metacritic.py` still pass unchanged).

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/pipeline.py
git commit -m "feat: compute cutoff date from cutoff_weeks at runtime"
```

---

### Task 4: Update scheduler wiring

**Files:**
- Modify: `src/gamarr/scheduler.py` (line 60)

- [ ] **Step 1: Rename the kwarg key**

In `src/gamarr/scheduler.py`, inside the `run_once()` function (or wherever `run_acquisition()` kwargs are assembled):

```python
# Before (around line 60)
        "cutoff_date": mc_cfg.cutoff_date,

# After
        "cutoff_weeks": mc_cfg.cutoff_weeks,
```

- [ ] **Step 2: Run tests**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_scheduler.py -v 2>/dev/null || echo "no scheduler tests"; uv run pytest --no-cov
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/scheduler.py
git commit -m "refactor: rename cutoff_date to cutoff_weeks in scheduler wiring"
```

---

### Task 5: Update config file

**Files:**
- Modify: `configs/gamarr.yml` (line 34)

- [ ] **Step 1: Replace the old config key**

In `configs/gamarr.yml`:

```yaml
# Before (line 34)
      cutoff_date: 2026-05-01

# After
      cutoff_weeks: 0    # 0 = no cutoff, positive N = look back N weeks
```

- [ ] **Step 2: Commit**

```bash
git add configs/gamarr.yml
git commit -m "chore: replace cutoff_date with cutoff_weeks in default config"
```

---

### Task 6: Full QC verification

**Files:** (no file changes — just running checks)

- [ ] **Step 1: Run full test suite**

```bash
cd /data/gamarr && uv run pytest --no-cov
```

Expected: All 262+ tests pass.

- [ ] **Step 2: Run coverage**

```bash
cd /data/gamarr && uv run pytest --cov=src/gamarr --cov-fail-under=80
```

Expected: Coverage passes (should remain ~96%).

- [ ] **Step 3: Run linter and type checker**

```bash
cd /data/gamarr && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/gamarr/
```

Expected: All checks pass.

- [ ] **Step 4: Run pre-commit**

```bash
cd /data/gamarr && pre-commit run --all-files 2>&1 | tail -20
```

Expected: All hooks pass.
