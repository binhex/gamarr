# Pretty Path Case Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `path_case` config field that controls library path template formatting — `"pretty"` uses display-friendly names, `"lowercase"` downcases everything.

**Architecture:** One new Pydantic field on `PostProcessConfig`, one module-level lookup table in `post_processor.py`, formatting applied per-key inside the existing `_build_destination_path()` function. Migration follows the existing config migration pattern in `config.py`.

**Tech Stack:** Python 3.12+, Pydantic, existing pytest test suite

---

### Task 1: Add `path_case` field to `PostProcessConfig`

**Files:**
- Modify: `src/gamarr/config.py`

- [ ] **Step 1: Add the Pydantic field**

In `src/gamarr/config.py`, find the `PostProcessConfig` class and add the new field:

```python
class PostProcessConfig(BaseModel):
    """Post-processing settings for copying completed downloads to library."""

    post_process_enabled: bool = True
    schedule_time_mins: int = Field(default=5, gt=0, description="Polling interval in minutes (must be > 0).")
    run_on_start: bool = True
    library_path: str = ""
    copy_completed: bool = True
    remove_completed: bool = True
    max_seed_wait_hours: int = Field(
        default=168, ge=0, description="Fallback: delete after hours even if seeding. 0 = never."
    )
    exclude_file_min_kb: int = 0
    exclude_file_regex_list: list[str] = Field(default_factory=list)
    exclude_folder_regex_list: list[str] = Field(default_factory=list)
    path_case: Literal["pretty", "lowercase"] = "pretty"  # ← new field
```

The `Literal` is already imported at the top of `config.py`.

- [ ] **Step 2: Add migration function**

Add a migration function in `config.py` that adds `path_case` to existing configs. Place it near the other migration functions (e.g., after `_migrate_daemon_mode` or before `_migrate_add_freegog_to_download_sites`):

```python
def _migrate_add_post_process_path_case(raw: dict[str, Any]) -> bool:
    """Add path_case field to post_process if missing.

    Returns True if the field was added.
    """
    pp = raw.get("post_process")
    if isinstance(pp, dict) and "path_case" not in pp:
        pp["path_case"] = "pretty"
        logger.info("Config: added post_process.path_case = 'pretty'")
        return True
    return False
```

- [ ] **Step 3: Register migration in `_migrate_config()`**

Add `_migrate_add_post_process_path_case` to the `_migrations` list inside `_migrate_config()`. Place it near the end, after any migration that creates the `post_process` section:

```python
_migrations = [
    # ... existing migrations ...
    _migrate_add_freegog_to_download_sites,
    _migrate_add_post_process_path_case,        # ← add this line
]
```

- [ ] **Step 4: Verify Pydantic validates correctly**

Run a quick Python check:

```bash
cd /data/gamarr && uv run python -c "
from gamarr.config import PostProcessConfig
cfg = PostProcessConfig()
assert cfg.path_case == 'pretty'
cfg2 = PostProcessConfig(path_case='lowercase')
assert cfg2.path_case == 'lowercase'
print('OK')
"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py
git commit -m "feat: add path_case field to PostProcessConfig with migration"
```

---

### Task 2: Add pretty formatting logic to `_build_destination_path()`

**Files:**
- Modify: `src/gamarr/post_processor.py`

- [ ] **Step 1: Add the `_PRETTY_SOURCE` lookup table**

At the top of `src/gamarr/post_processor.py`, below the existing `__all__` and `_RE_PATH_UNSAFE` definitions, add:

```python
_SOURCE_DISPLAY: dict[str, str] = {"fitgirl": "FitGirl", "freegog": "FreeGOG"}
```

- [ ] **Step 2: Add `_format_path_value()` helper function**

Add a new private function before `_build_destination_path()`:

```python
def _format_path_value(value: str, key: str, path_case: str) -> str:
    """Format a single template value according to *path_case*.

    In ``"pretty"`` mode, only the ``site`` key is transformed via the
    display-name lookup table.  All other keys pass through unchanged
    (their values are already correctly capitalized from Metacritic /
    user config).  In ``"lowercase"`` mode, every value is downcased.
    """
    if path_case == "lowercase":
        return value.lower()
    if key == "site":
        return _SOURCE_DISPLAY.get(value, value)
    return value
```

- [ ] **Step 3: Modify `_build_destination_path()` to accept and use `path_case`**

Update the function signature and body:

```python
def _build_destination_path(
    *,
    template: str,
    source: str,
    platform: str,
    genres: str | None,
    game_title: str,
    path_case: str = "pretty",   # ← new parameter
) -> str:
    """Resolve a library path template into a concrete filesystem path.

    Supported placeholders: {site}, {platform}, {genre}, {title}.
    {genre} uses only the first genre from a comma-separated list.
    """
    if not template:
        return ""
    first_genre = genres.split(",")[0].strip() if genres else "Unknown"
    replacements = {
        "site": source,
        "platform": platform,
        "genre": first_genre,
        "title": game_title,
    }
    result = template
    for key, value in replacements.items():
        formatted = _format_path_value(value, key, path_case)
        result = result.replace("{" + key + "}", _safe_path_component(formatted))
    return result
```

- [ ] **Step 4: Update caller in `_run_copy_phase()`**

Find the call to `_build_destination_path()` inside `_run_copy_phase()` and add the `path_case` parameter:

```python
    dst_dir = _build_destination_path(
        template=pp.library_path,
        source=row.source,
        platform=row.platform,
        genres=row.genres,
        game_title=row.game_title or "Unknown",
        path_case=pp.path_case,   # ← add this line
    )
```

- [ ] **Step 5: Verify existing tests still pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_post_processor.py -v --tb=short
```

Expected: 35 passed (or more if tests were added in earlier rounds)

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/post_processor.py
git commit -m "feat: add pretty path case formatting to post-processor"
```

---

### Task 3: Write tests

**Files:**
- Modify: `tests/unit/test_post_processor.py`

- [ ] **Step 1: Add test class with pretty-mode tests**

Add a new test class at the end of `tests/unit/test_post_processor.py`:

```python
class TestPathCaseFormatting:
    """Tests for path_case formatting in _build_destination_path."""

    def test_pretty_source_name_is_display_name(self) -> None:
        """fitgirl → FitGirl, freegog → FreeGOG."""
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="/lib/{site}",
            source="fitgirl",
            platform="pc",
            genres="Action",
            game_title="Test",
            path_case="pretty",
        )
        assert result == "/lib/FitGirl"

        result = _build_destination_path(
            template="/lib/{site}",
            source="freegog",
            platform="pc",
            genres="Action",
            game_title="Test",
            path_case="pretty",
        )
        assert result == "/lib/FreeGOG"

    def test_pretty_unknown_source_is_pass_through(self) -> None:
        """Unknown source names are passed through unchanged."""
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="/lib/{site}",
            source="unknown-source",
            platform="pc",
            genres="Action",
            game_title="Test",
            path_case="pretty",
        )
        assert result == "/lib/unknown-source"

    def test_pretty_platform_genre_title_are_pass_through(self) -> None:
        """Platform, genre, and title are already correct from Metacritic — no transformation."""
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="/lib/{platform}/{genre}/{title}",
            source="fitgirl",
            platform="Nintendo Switch",
            genres="Action, RPG",
            game_title="Zelda",
            path_case="pretty",
        )
        assert result == "/lib/Nintendo Switch/Action/Zelda"

    def test_lowercase_downs_everything(self) -> None:
        """In lowercase mode, all values are downcased."""
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="/lib/{site}/{platform}/{genre}/{title}",
            source="fitgirl",
            platform="PC",
            genres="Action,RPG",
            game_title="Elden Ring",
            path_case="lowercase",
        )
        assert result == "/lib/fitgirl/pc/action,rpg/elden ring"

    def test_default_is_pretty(self) -> None:
        """Default path_case is 'pretty'."""
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="/lib/{site}",
            source="fitgirl",
            platform="pc",
            genres="Action",
            game_title="Test",
        )
        assert result == "/lib/FitGirl"

    def test_empty_template_returns_empty(self) -> None:
        """Empty template returns empty string regardless of path_case."""
        from gamarr.post_processor import _build_destination_path

        result = _build_destination_path(
            template="",
            source="fitgirl",
            platform="pc",
            genres="Action",
            game_title="Test",
            path_case="lowercase",
        )
        assert result == ""
```

- [ ] **Step 2: Run the new tests to verify they pass**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_post_processor.py::TestPathCaseFormatting -v --tb=short
```

Expected: 6 passed

- [ ] **Step 3: Run full post_processor test suite**

```bash
cd /data/gamarr && uv run pytest tests/unit/test_post_processor.py -v --tb=short
```

Expected: all tests pass (41 if starting from 35, exact count depends on prior additions)

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_post_processor.py
git commit -m "test: add path_case formatting tests"
```

---

### Task 4: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

```bash
cd /data/gamarr && uv run pytest -q --tb=short
```

Expected: all tests pass (802+)

- [ ] **Step 2: Run lint and type check**

```bash
cd /data/gamarr && uv run ruff check --fix . && uv run ruff format . && uv run mypy .
```

Expected: all clean

- [ ] **Step 3: Verify migration works on existing configs**

```bash
cd /data/gamarr && uv run python -c "
from gamarr.config import load_config, Config
# Simulate an old config dict without path_case
raw = {'general': {}, 'post_process': {'post_process_enabled': True}}
from gamarr.config import _migrate_config
_migrate_config(raw)
assert raw['post_process']['path_case'] == 'pretty', f'Got: {raw.get(\"post_process\", {}).get(\"path_case\")}}'
print('Migration OK')
"
```

Expected: `Migration OK`

- [ ] **Step 4: Commit any outstanding changes**

```bash
git add -A
git commit -m "chore: final verification — all tests pass, lint clean"
```
