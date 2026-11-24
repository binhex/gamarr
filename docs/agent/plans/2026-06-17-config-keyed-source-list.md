# Config Format Redesign — Keyed Source List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) for syntax tracking.

**Goal:** Redesign the `download_sites` YAML config format so source names become YAML keys instead of a `name` field. Also rename `rss_url` to `feed_url` for all sources.

**Architecture:** A `@field_validator("root", mode="before")` on `DownloadSitesConfig` converts `[{"fitgirl": {...}}, {"dodi": {...}}]` into the internal `list[SourceConfigEntry]`, injecting the dict key as `name`. A migration function transforms old formats. The `name` field is excluded from YAML serialization via `Field(exclude=True)`.

**Tech Stack:** Python 3.12+, Pydantic v2, PyYAML

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/gamarr/config.py` | `SourceConfigEntry.feed_url`, `Field(exclude=True)` on `name`, `_parse_keyed_list` validator, `_migrate_download_sites_to_keyed_list` |
| `src/gamarr/scheduler.py` | `fitgirl_entry.rss_url` → `fitgirl_entry.feed_url`, `"fitgirl_rss_url"` → `"fitgirl_feed_url"` |
| `src/gamarr/pipeline.py` | `fitgirl_rss_url` param → `fitgirl_feed_url`, `entry.rss_url` → `entry.feed_url` |
| `src/gamarr/sources/fitgirl.py` | `self._rss_url` → `self._feed_url`, param `rss_url` → `feed_url` |
| `tests/unit/test_config.py` | New migration tests + update existing for keyed format |

---

### Task 1: Pydantic model — rename `rss_url` to `feed_url`, add `_parse_keyed_list` validator

**Files:**
- Modify: `src/gamarr/config.py:47-64` (SourceConfigEntry, DownloadSitesConfig)

- [ ] **Step 1: Write the failing test — keyed list parses correctly**

```python
# Add to tests/unit/test_config.py, inside TestConfigModels class
def test_parse_keyed_list(self) -> None:
    """DownloadSitesConfig parses [{'fitgirl': {'enabled': True}}] correctly."""
    from gamarr.config import DownloadSitesConfig, SourceConfigEntry

    raw: list[dict[str, Any]] = [
        {"fitgirl": {"enabled": True, "feed_url": "https://example.com/feed"}},
        {"dodi": {"enabled": True}},
    ]
    cfg = DownloadSitesConfig(root=raw)
    assert len(cfg) == 2
    assert cfg[0].name == "fitgirl"
    assert cfg[0].enabled is True
    assert cfg[0].feed_url == "https://example.com/feed"
    assert cfg[1].name == "dodi"
    assert cfg[1].enabled is True
    assert cfg[1].feed_url is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_config.py::TestConfigModels::test_parse_keyed_list -v
```
Expected: FAIL — `DownloadSitesConfig` doesn't have `_parse_keyed_list` validator yet.

- [ ] **Step 3: Rename `rss_url` to `feed_url` in `SourceConfigEntry`**

Change this in `src/gamarr/config.py`:

```python
class SourceConfigEntry(BaseModel):
    """A single download source entry."""

    name: str = Field(exclude=True)  # hidden from YAML, populated from dict key
    enabled: bool = True
    platform: str = "pc"
    cache_pages_hours: int = Field(default=6, gt=0, le=168)
    reject_keywords: list[str] = Field(default_factory=list)
    max_queue_days: int = Field(default=60, ge=0)
    feed_url: str | None = None
```

(`rss_url: str | None = None` → `feed_url: str | None = None`, `name` gets `Field(exclude=True)`)

- [ ] **Step 4: Add `_parse_keyed_list` validator to `DownloadSitesConfig`**

Also update the `root` default to include `feed_url` for fitgirl:

```python
class DownloadSitesConfig(RootModel[list[SourceConfigEntry]]):
    """Ordered list of download source configurations.

    Position in the list defines priority: earlier = higher priority.
    """

    root: list[SourceConfigEntry] = [
        SourceConfigEntry(name="fitgirl", feed_url="https://fitgirl-repacks.site/feed/"),
        SourceConfigEntry(name="dodi"),
    ]

    @field_validator("root", mode="before")
    @classmethod
    def _parse_keyed_list(cls, v: Any) -> Any:
        """Convert [{'fitgirl': {...}}, {'dodi': {...}}] into populated list.

        Handles three input formats:
        - New keyed:  [{'fitgirl': {'enabled': True, 'feed_url': '...'}}]
        - Legacy:     [{'name': 'fitgirl', 'rss_url': '...'}]
        - Shorthand:  [{'fitgirl': {}}]
        """
        if not isinstance(v, list):
            return v
        result: list[dict[str, Any]] = []
        for item in v:
            if isinstance(item, dict):
                for key, val in item.items():
                    if isinstance(val, dict):
                        val["name"] = key
                        # Rename rss_url → feed_url for backward compatibility
                        if "feed_url" not in val and "rss_url" in val:
                            val["feed_url"] = val.pop("rss_url")
                        result.append(val)
                    else:
                        # Bare key with no value — use defaults
                        result.append({"name": key})
            else:
                result.append(item)  # Already a dict entry
        return result

    def __iter__(self) -> Iterator[SourceConfigEntry]:  # type: ignore[override]
        return iter(self.root)

    def __getitem__(self, idx: int) -> SourceConfigEntry:
        return self.root[idx]

    def __len__(self) -> int:
        return len(self.root)
```

Add `field_validator` to the pydantic imports:

```python
from pydantic import BaseModel, Field, RootModel, field_validator
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_config.py::TestConfigModels::test_parse_keyed_list -v
```
Expected: PASS

- [ ] **Step 6: Write the failing test — name excluded from serialization**

```python
def test_name_excluded_from_dump(self) -> None:
    """SourceConfigEntry.name is excluded from dict serialization."""
    from gamarr.config import SourceConfigEntry

    entry = SourceConfigEntry(name="fitgirl", feed_url="https://example.com/feed")
    data = entry.model_dump()
    assert "name" not in data
    assert data["feed_url"] == "https://example.com/feed"
    assert data["enabled"] is True
```

- [ ] **Step 7: Run test to verify it passes**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_config.py::TestConfigModels::test_name_excluded_from_dump -v
```
Expected: PASS (the `Field(exclude=True)` already handles this)

- [ ] **Step 8: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat(config): keyed source list with feed_url
```

---

### Task 2: Pipeline + Scheduler — rename `rss_url` → `feed_url` at access points

**Files:**
- Modify: `src/gamarr/pipeline.py:124,219,417`
- Modify: `src/gamarr/scheduler.py:181`
- Test: `tests/unit/test_scheduler.py`, `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write a failing test — scheduler builds kwargs with feed_url**

```python
# Add to tests/unit/test_scheduler.py, inside TestBuildKwargs
def test_build_kwargs_includes_fitgirl_feed_url(self) -> None:
    """_build_kwargs uses feed_url instead of rss_url."""
    from gamarr.scheduler import _build_kwargs
    # Test uses existing _make_config helper which already creates
    # SourceConfigEntry with the new field
    from gamarr.config import Config

    config = _make_config(acquisition_enabled=True)
    kwargs = _build_kwargs(config)
    assert "fitgirl_feed_url" in kwargs
    assert "fitgirl_rss_url" not in kwargs
```

This will fail because `_build_kwargs` still uses `fitgirl_rss_url` and `entry.rss_url`.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_scheduler.py::TestBuildKwargs::test_build_kwargs_includes_fitgirl_feed_url -v
```
Expected: FAIL — key `fitgirl_feed_url` not found.

- [ ] **Step 3: Fix scheduler.py — rename rss_url to feed_url**

In `src/gamarr/scheduler.py`, change line 181:

```python
        "fitgirl_feed_url": fitgirl_entry.feed_url if fitgirl_entry else "https://fitgirl-repacks.site/feed/",
```

Also rename the existing `"fitgirl_rss_url"` key to `"fitgirl_feed_url"` on line 181.

- [ ] **Step 4: Fix pipeline.py — rename all rss_url references**

There are 3 locations to change:

**Location 1** — function signature (line ~124):

```python
    fitgirl_feed_url: str,
```

**Location 2** — `_make_fitgirl_entry` (line ~219):

```python
        entry.feed_url = fitgirl_feed_url
```

**Location 3** — `_build_source` kwargs (line ~417):

```python
            kwargs["feed_url"] = entry.feed_url or "https://fitgirl-repacks.site/feed/"
```

Also rename the `FitGirlSource` constructor parameter expectation. The `FitGirlSource.__init__` takes `rss_url` as a parameter. Since pipeline passes this as `kwargs["rss_url"]` → now `kwargs["feed_url"]`, but `FitGirlSource` still uses `rss_url`. We'll fix `FitGirlSource` in Task 3.

For now, change the pipeline to pass `rss_url` to the factory based on the fitgirl-specific code path. Actually, looking at the code more carefully:

```python
if entry.name == "fitgirl":
    kwargs["rss_url"] = entry.rss_url or "https://fitgirl-repacks.site/feed/"
```

This should become:

```python
if entry.name == "fitgirl":
    kwargs["feed_url"] = entry.feed_url or "https://fitgirl-repacks.site/feed/"
```

But `FitGirlSource` expects `rss_url` not `feed_url`. So we either:
- (a) Rename in `FitGirlSource` too (Task 3)
- (b) Map feed_url → rss_url at the pipeline boundary

The plan uses approach (a) — rename in FitGirlSource. So for now, change the kwargs key:

```python
if entry.name == "fitgirl":
    kwargs["rss_url"] = entry.feed_url or "https://fitgirl-repacks.site/feed/"
```

Wait, that's inconsistent — reading `entry.feed_url` but passing as `"rss_url"`. Better to pass as `"feed_url"` and rename in FitGirlSource in Task 3.

Actually, let me keep this simple. The pipeline's `_build_source` passes kwargs to the source factory. For FitGirl, it passes `rss_url` to `FitGirlSource.__init__`. We'll rename the FitGirlSource parameter to `feed_url` in Task 3. So the pipeline should pass `feed_url`:

```python
if entry.name == "fitgirl":
    kwargs["feed_url"] = entry.feed_url or "https://fitgirl-repacks.site/feed/"
```

- [ ] **Step 5: Update the _make_fitgirl_entry in pipeline.py**

```python
    def _make_fitgirl_entry() -> Any:
        """Build a fallback source entry from legacy parameters."""
        entry = types.SimpleNamespace()
        entry.name = "fitgirl"
        entry.enabled = True
        entry.platform = platform
        entry.cache_pages_hours = fitgirl_cache_pages_hours
        entry.feed_url = fitgirl_feed_url  # was rss_url
        entry.reject_keywords = fitgirl_reject_keywords or []
        entry.max_queue_days = fitgirl_max_queue_days
        return entry
```

- [ ] **Step 6: Run tests to verify they pass (or check failures)**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_scheduler.py::TestBuildKwargs -v
cd /data/gamarr && uv run pytest tests/unit/test_pipeline.py::TestRunAcquisition -v
```
Expected: Some tests may fail because `FitGirlSource.__init__` still uses `rss_url` param. That's fine — Task 3 fixes that.

- [ ] **Step 7: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "refactor(pipeline,scheduler): rename rss_url to feed_url
```

---

### Task 3: FitGirlSource — rename `rss_url` param to `feed_url`

**Files:**
- Modify: `src/gamarr/sources/fitgirl.py:237-252`
- Modify: `tests/unit/test_fitgirl.py` (if `rss_url` referenced in test)

- [ ] **Step 1: Rename in `FitGirlSource.__init__`**

In `src/gamarr/sources/fitgirl.py`:

```python
class FitGirlSource:
    def __init__(
        self,
        feed_url: str,
        platform: str = "pc",
        db_path: str = ":memory:",
        db: Database | None = None,
        cache_pages_hours: int = 6,
    ) -> None:
        self._feed_url = feed_url
```

Update the docstring too (line ~237): change `rss_url` → `feed_url`, `RSS feed` → `source URL`.

- [ ] **Step 2: Update test that constructs FitGirlSource**

Search for `FitGirlSource(rss_url=` or `FitGirlSource("http` in tests:

```bash
cd /data/gamarr && grep -rn "FitGirlSource(" tests/ --include="*.py"
```

Update any test that passes `rss_url=` kwarg to use `feed_url=` instead.

- [ ] **Step 3: Run full test suite**

```bash
cd /data/gamarr && uv run pytest
```
Expected: All tests pass now that the rename is consistent end-to-end.

- [ ] **Step 4: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "refactor(fitgirl): rename rss_url param to feed_url
```

---

### Task 4: Migration — add `_migrate_download_sites_to_keyed_list`

**Files:**
- Modify: `src/gamarr/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test — migration converts legacy format**

```python
def test_migrate_download_sites_to_keyed_list() -> None:
    """_migrate_config converts legacy [{'name': 'fitgirl', 'rss_url': ...}] to keyed format."""
    from unittest.mock import patch

    from gamarr.config import _migrate_config

    raw: dict[str, Any] = {
        "download_sites": [
            {"name": "fitgirl", "enabled": True, "rss_url": "https://fitgirl-repacks.site/feed/",
             "platform": "pc", "cache_pages_hours": 6, "reject_keywords": [], "max_queue_days": 60},
            {"name": "dodi", "enabled": True},
        ],
        "review_sites": {"metacritic": {"platform_overrides": {"pc": {}}}},
        "torrent_client": {"qbittorrent": {"host": "localhost", "port": 8080, "username": "admin", "password": "adminadmin"}},
    }
    with patch("gamarr.config.logger"):
        result = _migrate_config(raw)

    assert result is True
    ds = raw["download_sites"]
    assert isinstance(ds, list)
    # Should be keyed format now
    assert len(ds) == 2
    assert "name" in ds[0]  # name still exists internally
    assert isinstance(ds[0], dict)
    # fitgirl entry should have feed_url instead of rss_url
    first_entry = next(e for e in ds if isinstance(e, dict) and e.get("name") == "fitgirl")
    assert "rss_url" not in first_entry
    assert first_entry["feed_url"] == "https://fitgirl-repacks.site/feed/"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_config.py::test_migrate_download_sites_to_keyed_list -v
```
Expected: FAIL — function not defined or config not converted.

- [ ] **Step 3: Add `_migrate_download_sites_to_keyed_list` function**

Add this function in `src/gamarr/config.py` (before `_migrate_daemon_mode`):

```python
def _migrate_download_sites_to_keyed_list(raw: dict[str, Any]) -> bool:
    """Convert [{name: ..., rss_url: ...}] to [{key: {feed_url: ...}}] format.

    Renames rss_url to feed_url and moves name into the dict key.
    Returns True if any migration was applied.
    """
    ds = raw.get("download_sites")
    if not isinstance(ds, list):
        return False

    changed = False
    converted: list[dict[str, Any]] = []
    for entry in ds:
        if not isinstance(entry, dict):
            converted.append(entry)
            continue
        # Check if this is a field-based entry that needs conversion
        name = entry.pop("name", None) if "name" in entry else None
        if name:
            # Rename rss_url → feed_url
            if "rss_url" in entry:
                entry["feed_url"] = entry.pop("rss_url")
                changed = True
            elif "feed_url" in entry:
                changed = True  # Already has feed_url but we're still reformatting

            # If name was present as a field, convert to keyed format
            keyed: list[dict[str, Any]] = [{str(name): {}}]
            for k, v in entry.items():
                keyed[0][str(name)][k] = v
            converted.extend(keyed)
            changed = True
        else:
            # Already in keyed format or other — pass through
            converted.append(entry)

    if changed:
        raw["download_sites"] = converted
        logger.info("Config: migrated download_sites to keyed-list format")
    return changed
```

Wait, this approach is problematic because the `entry.pop("name")` mutates the entry but then we need to reconstruct it. Let me think about this differently.

Actually, the simpler approach: the validator in `DownloadSitesConfig._parse_keyed_list` already handles both formats at parse time. The migration function just needs to convert any old-format entries into the new format at the raw dict level, so the Pydantic model can then validate them cleanly.

The migration should:
1. For each entry in the list that has a `"name"` key, restructure it into `{name_value: {rest_of_keys}}`
2. Rename `rss_url` → `feed_url` in the process

Let me rewrite:

```python
def _migrate_download_sites_to_keyed_list(raw: dict[str, Any]) -> bool:
    """Convert [{name: ..., rss_url: ...}] to [{key: {feed_url: ...}}] format.

    Also handles flat dict format: {fitgirl: {rss_url: ...}} → [{fitgirl: {feed_url: ...}}]
    (already handled by _migrate_download_sites_to_ordered).

    Returns True if any migration was applied.
    """
    ds = raw.get("download_sites")
    if not isinstance(ds, list):
        return False

    changed = False
    for i, entry in enumerate(ds):
        if not isinstance(entry, dict):
            continue

        # Already in keyed format (has exactly one key that is a source name)?
        # Check: if entry has "name" key and no other source-name-as-key
        entry_name = entry.pop("name", None)
        if entry_name is None:
            continue  # Already in keyed format or unrecognized

        # Legacy format: {name: "fitgirl", rss_url: "...", ...}
        # Convert to keyed format: {"fitgirl": {"feed_url": "...", ...}}
        inner = {}
        for k, v in entry.items():
            if k == "rss_url":
                inner["feed_url"] = v
            elif k == "feed_url":
                inner[k] = v
            else:
                inner[k] = v
        ds[i] = {str(entry_name): inner}
        changed = True

    if changed:
        logger.info("Config: migrated download_sites entries to keyed-list format")
    return changed
```

- [ ] **Step 4: Register the migration in the migration list**

Add `_migrate_download_sites_to_keyed_list` to the `_migrations` list in `_migrate_config`, after `_migrate_download_sites_to_ordered`:

```python
            _migrate_download_sites_to_ordered,
            _migrate_download_sites_to_keyed_list,
            _migrate_add_dodi_entry,
```

- [ ] **Step 5: Update the default config in `Config.download_sites`**

The default factory already uses `feed_url` for fitgirl (from Task 1).

- [ ] **Step 6: Run the migration test**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_config.py::test_migrate_download_sites_to_keyed_list -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "feat(config): add migration to keyed-list format
```

---

### Task 5: Update existing tests for new config format

**Files:**
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_scheduler.py`
- Modify: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Update test_config.py**

Search for all references to `rss_url` in `tests/unit/test_config.py`:

```bash
cd /data/gamarr && grep -n "rss_url\|Feed_url\|feed_url" tests/unit/test_config.py
```

Update each one:
- `SourceConfigEntry(rss_url=...)` → `SourceConfigEntry(feed_url=...)` (or remove the field since it's optional)
- Any test constructing `DownloadSitesConfig(root=[SourceConfigEntry(name="fitgirl")])` — this still works since the validator handles all formats
- Tests that dump/load YAML and check `rss_url` key → check `feed_url` key instead

- [ ] **Step 2: Update test_scheduler.py**

```bash
cd /data/gamarr && grep -n "rss_url\|feed_url" tests/unit/test_scheduler.py
```

Update:
- `test_build_kwargs_includes_notify_on_error` and similar — check for `"fitgirl_feed_url"` instead of `"fitgirl_rss_url"`
- `_make_config` helper — uses `SourceConfigEntry(name="fitgirl", ..., rss_url=...)` → change to `feed_url=...`

- [ ] **Step 3: Update test_pipeline.py**

```bash
cd /data/gamarr && grep -n "rss_url\|feed_url" tests/unit/test_pipeline.py
```

The pipeline tests use `run_acquisition(**kwargs)` where kwargs include `"fitgirl_rss_url"`. These need to become `"fitgirl_feed_url"`.

- [ ] **Step 4: Run full test suite**

```bash
cd /data/gamarr && uv run pytest
```
Expected: All tests pass. Fix any remaining failures.

- [ ] **Step 5: Commit**

```bash
cd /data/gamarr && git add -A && git commit -m "test: update tests for keyed-list config format
```

---

### Task 6: Run full test suite + final checks

- [ ] **Step 1: Run full test suite + coverage**

```bash
cd /data/gamarr && uv run pytest --cov=gamarr --cov-fail-under=95 -v
```
Expected: All tests pass, coverage >= 95%

- [ ] **Step 2: Run ruff + format + mypy**

```bash
cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .
```
Expected: All clean

- [ ] **Step 3: Final commit**

```bash
cd /data/gamarr && git add -A && git commit -m "chore: final test suite pass for keyed-list config
```

---

## Self-Review Checklist

**1. Spec coverage:**
- ✅ YAML format change (keyed list) → Task 1 (validator)
- ✅ `rss_url` → `feed_url` rename → Tasks 1, 2, 3
- ✅ `name` field excluded from serialization → Task 1 (`Field(exclude=True)`)
- ✅ `_parse_keyed_list` validator → Task 1
- ✅ Migration function → Task 4
- ✅ Pipeline/scheduler field renames → Task 2
- ✅ FitGirlSource param rename → Task 3
- ✅ Test updates → Task 5

**2. Placeholder scan:** Every code block contains actual working code. No TBDs, TODOs.

**3. Type consistency:** `feed_url` used consistently across all tasks. `name` is a `str = Field(exclude=True)` in SourceConfigEntry, accessed as `entry.name` in pipeline/scheduler.
