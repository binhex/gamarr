# max_cycle_weeks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `max_cycle_weeks` config field that limits how far back each acquisition cycle browses Metacritic, reducing HTTP load while ensuring newest games are always discovered first.

**Architecture:** A new config field `max_cycle_weeks` (default `4`) on `MetacriticPlatformConfig` that computes a per-cycle cutoff date. This is passed to `scan_recent_games()` as `cutoff_date`, replacing the `max_weeks`-derived cutoff for page-fetching purposes. `max_weeks` continues to serve as the independent hard cutoff via `_is_older_than()`. When `max_cycle_weeks` exceeds `max_weeks`, a warning is logged and the value is capped.

**Tech Stack:** Python 3.12+, Pydantic, click, pytest

**Spec:** `docs/superpowers/specs/2026-06-12-max-cycle-weeks-design.md`

---

### Task 1: Add `max_cycle_weeks` to the config model

**Files:**
- Modify: `src/gamarr/config.py` (field on `MetacriticPlatformConfig`)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1.1: Write the failing config model test**

Add to `tests/unit/test_config.py` in `class TestConfigModels`:

```python
def test_max_cycle_weeks_default(self) -> None:
    """MetacriticPlatformConfig.max_cycle_weeks defaults to 4."""
    from gamarr.config import MetacriticPlatformConfig

    cfg = MetacriticPlatformConfig()
    assert cfg.max_cycle_weeks == 4

def test_max_cycle_weeks_ge_zero(self) -> None:
    """max_cycle_weeks must be >= 0 (0 or None = unlimited)."""
    from pydantic import ValidationError

    from gamarr.config import MetacriticPlatformConfig

    MetacriticPlatformConfig(max_cycle_weeks=0)
    MetacriticPlatformConfig(max_cycle_weeks=None)
    MetacriticPlatformConfig(max_cycle_weeks=4)
    with pytest.raises(ValidationError):
        MetacriticPlatformConfig(max_cycle_weeks=-1)
```

You'll need to add `import pytest` to the imports at the top of the file if not already present.

- [ ] **Step 1.2: Run the test to verify it fails**

Run: `pytest tests/unit/test_config.py::TestConfigModels::test_max_cycle_weeks_default -x -v`
Expected: AttributeError — `'MetacriticPlatformConfig' object has no attribute 'max_cycle_weeks'`

- [ ] **Step 1.3: Add the field to the config model**

In `src/gamarr/config.py`, add the field to `MetacriticPlatformConfig` after `max_weeks` (around line 73):

```python
max_cycle_weeks: int | None = Field(default=4, ge=0)
```

It should sit between `max_weeks` and `reject_genre`:

```python
    max_weeks: int | None = Field(default=13, ge=0)
    max_cycle_weeks: int | None = Field(default=4, ge=0)
    reject_genre: list[str] = Field(default_factory=list)
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `pytest tests/unit/test_config.py::TestConfigModels -x -v`
Expected: All Tests in TestConfigModels PASS

- [ ] **Step 1.5: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "feat: add max_cycle_weeks field to MetacriticPlatformConfig"
```

---

### Task 2: Wire `max_cycle_weeks` through pipeline and scheduler

**Files:**
- Modify: `src/gamarr/pipeline.py` (AcquisitionConfig, run_acquisition, _run_discovery_phases)
- Modify: `src/gamarr/scheduler.py` (_build_kwargs)
- Test: `tests/unit/test_pipeline.py`
- Test: `tests/unit/test_scheduler.py`

- [ ] **Step 2.1: Write the failing pipeline test**

Add to `tests/unit/test_pipeline.py` in the appropriate test class (e.g., a new class `TestMaxCycleWeeks`):

```python
class TestMaxCycleWeeks:
    """Tests for max_cycle_weeks integration."""

    def test_max_cycle_weeks_lower_than_max_weeks_uses_cycle_cutoff(self, tmp_path: Path) -> None:
        """When max_cycle_weeks < max_weeks, scan_recent_games receives the cycle cutoff."""
        import datetime

        from gamarr.database import Database
        from gamarr.pipeline import AcquisitionConfig

        cfg = AcquisitionConfig(
            min_metascore=75,
            min_metascore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
            max_weeks=52,
            max_cycle_weeks=4,
        )
        # _age_days() still derives from max_weeks (hard cutoff)
        assert cfg._age_days() == 52 * 7
        # The cycle weeks are separate
        assert cfg.max_cycle_weeks == 4
```

- [ ] **Step 2.2: Write the scheduler test**

Add to `tests/unit/test_scheduler.py` in `TestBuildKwargs`:

```python
def test_build_kwargs_includes_max_cycle_weeks(self) -> None:
    """_build_kwargs should include max_cycle_weeks from config."""
    from gamarr.config import Config
    from gamarr.scheduler import _build_kwargs

    config = Config()
    kwargs = _build_kwargs(config)
    assert "max_cycle_weeks" in kwargs
    assert kwargs["max_cycle_weeks"] == 4
```

- [ ] **Step 2.3: Run both tests to verify they fail**

Run: `pytest tests/unit/test_pipeline.py::TestMaxCycleWeeks tests/unit/test_scheduler.py::TestBuildKwargs::test_build_kwargs_includes_max_cycle_weeks -x -v`

Expected: First fails with AttributeError on `max_cycle_weeks` field. Fix, then second fails with missing kwarg.

- [ ] **Step 2.4: Add `max_cycle_weeks` to `AcquisitionConfig`**

In `src/gamarr/pipeline.py`, add after `max_weeks` (around line 110):

```python
    max_cycle_weeks: int | None = None
```

- [ ] **Step 2.5: Add `max_cycle_weeks` to `run_acquisition()`**

In `src/gamarr/pipeline.py`, add the parameter in the function signature (after `max_weeks`, around line 178):

```python
    max_cycle_weeks: int | None = None,
```

And in the `AcquisitionConfig(...)` constructor call (around line 212), add:

```python
        max_cycle_weeks=max_cycle_weeks,
```

- [ ] **Step 2.6: Implement the effective cutoff logic in `_run_discovery_phases()`**

In `src/gamarr/pipeline.py`, inside `_run_discovery_phases()`, find the section that computes `cutoff_date` from `max_weeks` (currently around lines 275-283). Replace it with:

```python
            # Compute the effective cutoff for page fetching.
            # max_cycle_weeks limits per-cycle depth; max_weeks is the
            # hard cutoff. When max_cycle_weeks > max_weeks, cap to
            # max_weeks to avoid wasted HTTP requests.
            cutoff_date: str | None = None
            effective_cycle_weeks = cfg.max_cycle_weeks
            if cfg.max_weeks is not None and cfg.max_weeks > 0:
                if cfg.max_cycle_weeks and cfg.max_cycle_weeks > cfg.max_weeks:
                    logger.warning(
                        "max_cycle_weeks ({}) exceeds max_weeks ({}) — "
                        "capping to {} to avoid wasted HTTP requests",
                        cfg.max_cycle_weeks, cfg.max_weeks, cfg.max_weeks,
                    )
                    effective_cycle_weeks = cfg.max_weeks
                cutoff_date = (
                    datetime.datetime.now(tz=datetime.UTC).date()
                    - datetime.timedelta(weeks=effective_cycle_weeks or cfg.max_weeks)
                ).isoformat()
            elif cfg.max_cycle_weeks and cfg.max_cycle_weeks > 0:
                cutoff_date = (
                    datetime.datetime.now(tz=datetime.UTC).date()
                    - datetime.timedelta(weeks=cfg.max_cycle_weeks)
                ).isoformat()

            browse_games = mc.scan_recent_games(
                platform,
                cache_pages_hours=cfg.cache_pages_hours,
                cutoff_date=cutoff_date,
                cancel_event=cancel_event,
            )
```

- [ ] **Step 2.7: Add `max_cycle_weeks` to the scheduler**

In `src/gamarr/scheduler.py`, in `_build_kwargs()`, add after the `max_weeks` line (around line 122):

```python
        "max_cycle_weeks": mc_cfg.max_cycle_weeks,
```

- [ ] **Step 2.8: Run all tests to verify they pass**

Run: `pytest tests/unit/test_pipeline.py::TestMaxCycleWeeks tests/unit/test_scheduler.py::TestBuildKwargs -x -v`
Expected: All PASS

- [ ] **Step 2.9: Commit**

```bash
git add src/gamarr/pipeline.py src/gamarr/scheduler.py
git add tests/unit/test_pipeline.py tests/unit/test_scheduler.py
git commit -m "feat: wire max_cycle_weeks through pipeline and scheduler"
```

---

### Task 3: Update README with config documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 3.1: Add the config table row**

In `README.md`, find the config table under `metacritic.platform_overrides.<platform>` (around line 133). Add a new row after the `max_weeks` row:

```markdown
| `max_cycle_weeks` | How many weeks of Metacritic pages to scan per cycle. Reduces HTTP load by limiting browse depth each cycle. `0` or `null` = unlimited (same as not set). | `4` |
```

- [ ] **Step 3.2: Update "How It Works" section (optional)**

In the "How It Works" section, find the description of the Metacritic browse phase (around line 220s). Update to mention that `max_cycle_weeks` controls per-cycle depth while `max_weeks` remains the hard cutoff.

- [ ] **Step 3.3: Verify README renders correctly**

Run: `python -c "with open('README.md') as f: lines = f.readlines(); print(len(lines), 'lines')"`
Expected: No syntax errors, file parses correctly.

- [ ] **Step 3.4: Commit**

```bash
git add README.md
git commit -m "docs: document max_cycle_weeks in README"
```

---

### Task 4: Full verification suite

- [ ] **Step 4.1: Run the full test suite**

```bash
pytest --cov-fail-under=95
```

Expected: All tests pass, coverage >= 95%

- [ ] **Step 4.2: Run linters**

```bash
ruff check --fix .
ruff format .
mypy .
pre-commit run --all-files
```

Expected: All clean

- [ ] **Step 4.3: Final commit (if any lint fixes applied)**

```bash
git commit -am "chore: lint and format after max_cycle_weeks changes"
```
