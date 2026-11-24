# review_sites — Review Site Config Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `review_sites` config section that wraps the existing `metacritic` config block, changing the YAML hierarchy from `metacritic:` to `review_sites.metacritic:`.

**Architecture:** A new `ReviewSitesConfig` Pydantic model wraps `MetacriticConfig`. The root `Config` model replaces the `metacritic: MetacriticConfig` field with `review_sites: ReviewSitesConfig`. A migration function moves the old top-level `metacritic` key to `review_sites.metacritic`. All code paths that access `config.metacritic.*` update to `config.review_sites.metacritic.*`.

**Tech Stack:** Python 3.12+, Pydantic, click, pytest

**Spec:** `docs/superpowers/specs/2026-06-12-review-sites-design.md`

---

### Task 1: Add `ReviewSitesConfig` model and migration

**Files:**
- Modify: `src/gamarr/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1.1: Write the failing config model test**

Add to `tests/unit/test_config.py` in `class TestConfigModels`:

```python
def test_config_has_review_sites(self) -> None:
    """Config().review_sites.metacritic should exist, Config().metacritic should not."""
    from gamarr.config import Config

    cfg = Config()
    assert hasattr(cfg, "review_sites"), "Config must have review_sites field"
    assert cfg.review_sites.metacritic is not None
    assert not hasattr(cfg, "metacritic"), "Config should not have top-level metacritic field"
```

- [ ] **Step 1.2: Run the test to verify it fails**

Run: `pytest tests/unit/test_config.py::TestConfigModels::test_config_has_review_sites -x -v`
Expected: FAIL — `Config` object has attribute `metacritic`, not `review_sites`.

- [ ] **Step 1.3: Add `ReviewSitesConfig` model to `config.py`**

Add this new Pydantic model before the root `Config` class (after `LibraryConfig`, around line 127):

```python
class ReviewSitesConfig(BaseModel):
    """Aggregated review site configurations."""

    metacritic: MetacriticConfig = Field(default_factory=MetacriticConfig)
```

- [ ] **Step 1.4: Update the root `Config` model**

Change the `Config` class to replace `metacritic` with `review_sites`:

```python
class Config(BaseModel):
    """Root configuration model that aggregates all sub-configs."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    download_sites: DownloadSitesConfig = Field(default_factory=DownloadSitesConfig)
    review_sites: ReviewSitesConfig = Field(default_factory=ReviewSitesConfig)  # ← replaces metacritic
    torrent_client: TorrentClientConfig = Field(default_factory=TorrentClientConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    library: LibraryConfig = Field(default_factory=LibraryConfig)
```

Remove the line: `metacritic: MetacriticConfig = Field(default_factory=MetacriticConfig)`

- [ ] **Step 1.5: Run the config model test to verify it passes**

Run: `pytest tests/unit/test_config.py::TestConfigModels::test_config_has_review_sites -x -v`
Expected: PASS

- [ ] **Step 1.6: Update existing config model defaults tests**

The test `test_root_config_defaults` at line ~303 checks `cfg.metacritic.platform_overrides["pc"].min_metascore`. Update to `cfg.review_sites.metacritic.platform_overrides["pc"].min_metascore`:

Run: `pytest tests/unit/test_config.py::TestConfigModels::test_root_config_defaults -x -v`
If it fails, update the assertion path.

- [ ] **Step 1.7: Add the migration function**

Add this function before `_migrate_daemon_mode` (around line 430):

```python
def _migrate_metacritic_to_review_sites(raw: dict[str, Any]) -> bool:
    """Move top-level metacritic key under review_sites.

    Runs early so all downstream migrations see the new path.
    Returns True if any migration was applied.
    """
    if "metacritic" not in raw:
        return False
    if "review_sites" not in raw:
        raw["review_sites"] = {}
    if "metacritic" not in raw["review_sites"]:
        raw["review_sites"]["metacritic"] = raw.pop("metacritic")
        logger.info("Config: migrated 'metacritic' to 'review_sites.metacritic'")
        return True
    # Both exist — deep-merge
    raw["review_sites"]["metacritic"] = _deep_merge(
        raw["review_sites"]["metacritic"], raw.pop("metacritic")
    )
    return True
```

- [ ] **Step 1.8: Register the migration in `_migrate_config()`**

In `_migrate_config()`, add `_migrate_metacritic_to_review_sites` as the **first** migration in the list (before all migrations that read `metacritic.*` keys):

```python
_migrations = [
    _migrate_metacritic_to_review_sites,  # ← first
    _migrate_download_sites,
    _migrate_platform_overrides,
    ...
]
```

- [ ] **Step 1.9: Update all `raw.get("metacritic")` calls in existing migrations**

Every migration function that currently reads `raw.get("metacritic", {}).get("platform_overrides", {})` needs to change to `raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})`. The affected migration functions are:

- `_migrate_platform_overrides` (line ~188)
- `_drop_metacritic_max_games` (line ~225)
- `_drop_migrated_deprecated_keys` (line ~247)
- `_drop_max_verify_attempts` (line ~275)
- `_migrate_days_since_release` (line ~294)
- `_rename_pending_days` usage via `_migrate_pending_days_to_max_queue_days` (line ~351)
- `_migrate_cutoff_weeks_to_max_weeks` (line ~386)
- `_migrate_remove_max_games` (line ~405)
- `_migrate_recheck_days_to_max_queue_days` (line ~435)

For each, change:
```python
overrides = raw.get("metacritic", {}).get("platform_overrides", {})
```
to:
```python
overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
```

Also update the similar pattern for `download_sites`/`sources` fitgirl migrations (those already use the `parent_key` variable pattern).

- [ ] **Step 1.10: Write the migration test**

Add to `tests/unit/test_config.py` in `TestLoadConfig`:

```python
def test_migrate_metacritic_to_review_sites(self) -> None:
    """Old top-level metacritic key is moved under review_sites."""
    from typing import Any

    from gamarr.config import _migrate_config

    raw: dict[str, Any] = {
        "metacritic": {
            "platform_overrides": {
                "pc": {"min_metascore": 75, "max_weeks": 12},
            },
        },
    }
    result = _migrate_config(raw)
    assert result is True
    assert "metacritic" not in raw, "Old top-level metacritic key should be removed"
    assert "review_sites" in raw
    assert raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["min_metascore"] == 75
```

- [ ] **Step 1.11: Run all config tests**

Run: `pytest tests/unit/test_config.py -x -v`
Expected: All PASS

- [ ] **Step 1.12: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "feat: add ReviewSitesConfig wrapper and migration"
```

---

### Task 2: Update scheduler code access paths

**Files:**
- Modify: `src/gamarr/scheduler.py`
- Test: `tests/unit/test_scheduler.py`

- [ ] **Step 2.1: Update `_build_kwargs` in `scheduler.py`**

Change the config access paths from `config.metacritic.*` to `config.review_sites.metacritic.*`:

```python
# Old:
mc_cfg = config.metacritic.platform_overrides.get(
    config.download_sites.fitgirl.platform,
    config.metacritic.platform_overrides["pc"],
)
# New:
mc_cfg = config.review_sites.metacritic.platform_overrides.get(
    config.download_sites.fitgirl.platform,
    config.review_sites.metacritic.platform_overrides["pc"],
)
```

- [ ] **Step 2.2: Update scheduler test mocks**

In `tests/unit/test_scheduler.py`, find all mock config setups that set `config.metacritic.platform_overrides` and change them to `config.review_sites.metacritic.platform_overrides`. The tests at lines ~190-230 and ~250-280 need updating.

Search for `.metacritic.platform_overrides` in the test file and replace with `.review_sites.metacritic.platform_overrides`.

- [ ] **Step 2.3: Run scheduler tests**

Run: `pytest tests/unit/test_scheduler.py -x -v`
Expected: All PASS

- [ ] **Step 2.4: Commit**

```bash
git add src/gamarr/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat: update scheduler to use review_sites.metacritic path"
```

---

### Task 3: Update README documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 3.1: Update the config table**

In `README.md`, find the `metacritic` section header in the config table (around line 125). Change it to `review_sites.metacritic`:

```markdown
### `review_sites.metacritic`

| Key | Description | Default |
| --- | ----------- | ------- |
```

- [ ] **Step 3.2: Update all references to `metacritic.platform_overrides`**

Search README for `metacritic.platform_overrides` and change to `review_sites.metacritic.platform_overrides`.

- [ ] **Step 3.3: Commit**

```bash
git add README.md
git commit -m "docs: update README for review_sites.metacritic path"
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

- [ ] **Step 4.3: Verify generated config auto-migration**

The generated config (`configs/gamarr.yml`) was already auto-migrated in Task 1 when tests ran. Check that it now has `review_sites:` instead of `metacritic:`:

```bash
grep -n "review_sites\|^metacritic:" configs/gamarr.yml
```

Expected: `review_sites:` appears, `metacritic:` does NOT appear at top level.

- [ ] **Step 4.4: Final commit (if any changes)**

```bash
git add configs/gamarr.yml
git commit -m "chore: auto-migrate generated config to review_sites structure"
```
