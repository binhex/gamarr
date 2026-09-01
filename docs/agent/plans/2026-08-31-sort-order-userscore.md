# sort_order `criticscore`/`userscore` + critic-threshold rename — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `userscore` browse-sort option, rename `sort_order: metascore` to `criticscore`, and rename `min_metascore`/`min_metascore_reviews` to `min_criticscore`/`min_criticscore_reviews` — with the user's `gamarr.yml` auto-rewritten in place on first load.

**Architecture:** The sort value is already interpolated directly into the Metacritic browse URL slug (`…/browse/game/pc/all/<year>/<sort_order>/`), so `userscore` needs no metacritic.py URL change — only the config/pipeline Literals widen. The rename rides the project's existing key-presence-driven migration: a new `_migrate_*` function rewrites the raw YAML dict in place, and `load_config`'s existing write-back logic (`migrated=True` → dump merged config + bump `config_version`) persists the rename to the user's file exactly once.

**Tech Stack:** Python 3.12, pydantic v2 (`Literal`, `Field`), pytest, uv.

**Spec:** `docs/agent/specs/2026-08-31-sort-order-userscore-design.md` (approved).

---

## File map

| File | Change |
|------|--------|
| `src/gamarr/config.py` | Rename 2 threshold fields; widen `sort_order` Literal; add `_migrate_sort_order_critic_names()`; register it in `_migrations` |
| `src/gamarr/pipeline.py` | Rename `AcquisitionConfig` fields + `run_acquisition` params + 3 threshold-dict keys + consumer reads (lines ~639-640, 731-732, 845-846, 886, 893, 900-903); widen 2 Literals |
| `src/gamarr/scheduler.py` | `_build_kwargs`: rename 2 dict keys (lines ~276-279) |
| `README.md` | `sort_order` row + 2 renamed threshold rows in the config table |
| `tests/unit/test_config.py` | Update renames; add migration tests |
| `tests/unit/test_pipeline.py` | Update renames; add `userscore`/`criticscore` tests |
| `tests/unit/test_scheduler.py` | Update MagicMock attr names in daemon scaffolding |
| `tests/unit/test_readme_accuracy.py` | Update README-default assertions for renamed rows |

`src/gamarr/metacritic.py` is **unchanged** (slug interpolation already generic).

---

### Task 1: Rename config fields and widen the sort Literal

**Files:**
- Modify: `src/gamarr/config.py` (`MetacriticPlatformConfig`, ~lines 107-117)
- Test: `tests/unit/test_config.py` (~lines 77, 380, 1216-1242)

- [ ] **Step 1: Write the failing tests**

Update the two existing assertions that reference old names, and add new tests next to `TestSortOrderConfig` (line ~1216):

```python
class TestCriticThresholdRename:
    def test_defaults(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert cfg.min_criticscore == 75
        assert cfg.min_criticscore_reviews == 10

    def test_old_names_rejected(self) -> None:
        import pydantic
        import pytest

        from gamarr.config import MetacriticPlatformConfig

        with pytest.raises(pydantic.ValidationError):
            MetacriticPlatformConfig(min_metascore=75)  # type: ignore[call-arg]

    def test_sort_order_accepts_three_values(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        assert MetacriticPlatformConfig(sort_order="new").sort_order == "new"
        assert MetacriticPlatformConfig(sort_order="criticscore").sort_order == "criticscore"
        assert MetacriticPlatformConfig(sort_order="userscore").sort_order == "userscore"

    def test_sort_order_rejects_metascore_and_unknown(self) -> None:
        import pydantic
        import pytest

        from gamarr.config import MetacriticPlatformConfig

        with pytest.raises(pydantic.ValidationError):
            MetacriticPlatformConfig(sort_order="metascore")
        with pytest.raises(pydantic.ValidationError):
            MetacriticPlatformConfig(sort_order="bogus")
```

Also rename the existing `test_sort_order_accepts_metascore` test to `test_sort_order_accepts_criticscore` and assert `criticscore`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py::TestCriticThresholdRename tests/unit/test_config.py::TestSortOrderConfig -q`
Expected: FAIL — `min_metascore` field missing / Literal mismatch.

- [ ] **Step 3: Modify the model**

In `src/gamarr/config.py`, replace (inside `MetacriticPlatformConfig`):

```python
    min_metascore: int = 75
    min_metascore_reviews: int = 10
    min_user_score: float = 7.5
```
with:
```python
    min_criticscore: int = 75
    min_criticscore_reviews: int = 10
    min_user_score: float = 7.5
```
and replace:
```python
    sort_order: Literal["new", "metascore"] = "new"
```
with:
```python
    sort_order: Literal["new", "criticscore", "userscore"] = "new"
```

Update the other two test references to the old field names (`tests/unit/test_config.py` lines ~77 and ~380): `cfg.min_metascore` → `cfg.min_criticscore` (both are default-value assertions; change the attribute name only).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py::TestCriticThresholdRename tests/unit/test_config.py::TestSortOrderConfig -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "refactor: rename critic thresholds and widen sort_order Literal"
```

---

### Task 2: One-time in-place YAML migration

**Files:**
- Modify: `src/gamarr/config.py` (new `_migrate_sort_order_critic_names`, register in `_migrations` list inside `_migrate_config`, ~lines 826-864)
- Test: `tests/unit/test_config.py` (add class after existing migration tests)

- [ ] **Step 1: Write the failing tests**

```python
class TestSortOrderCriticMigration:
    def _write_config(self, tmp_path, content: str):
        path = tmp_path / "gamarr.yml"
        path.write_text(content)
        return path

    def test_migrates_legacy_names_and_rewrites_file(self, tmp_path) -> None:
        from gamarr.config import load_config

        path = self._write_config(
            tmp_path,
            "review_sites:\n"
            "  metacritic:\n"
            "    platform_overrides:\n"
            "      pc:\n"
            "        min_metascore: 80\n"
            "        min_metascore_reviews: 15\n"
            "        sort_order: metascore\n",
        )
        cfg = load_config(path)
        mc = cfg.review_sites.metacritic.platform_overrides["pc"]
        assert mc.sort_order == "criticscore"
        assert mc.min_criticscore == 80
        assert mc.min_criticscore_reviews == 15

        rewritten = path.read_text()
        assert "min_criticscore" in rewritten
        assert "min_metascore" not in rewritten
        assert "sort_order: criticscore" in rewritten

    def test_second_load_does_not_rewrite(self, tmp_path) -> None:
        from gamarr.config import load_config

        path = self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n        sort_order: metascore\n",
        )
        load_config(path)
        first = path.read_text()
        load_config(path)
        assert path.read_text() == first

    def test_keeps_existing_new_value_on_collision(self, tmp_path) -> None:
        from gamarr.config import load_config

        path = self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n"
            "        min_metascore: 70\n        min_criticscore: 90\n",
        )
        cfg = load_config(path)
        assert cfg.review_sites.metacritic.platform_overrides["pc"].min_criticscore == 90

    def test_userscore_value_passes_through_untouched(self, tmp_path) -> None:
        from gamarr.config import load_config

        path = self._write_config(
            tmp_path,
            "review_sites:\n  metacritic:\n    platform_overrides:\n      pc:\n        sort_order: userscore\n",
        )
        cfg = load_config(path)
        assert cfg.review_sites.metacritic.platform_overrides["pc"].sort_order == "userscore"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py::TestSortOrderCriticMigration -q`
Expected: FAIL — `ValidationError` (`metascore` not a valid Literal / extra field).

- [ ] **Step 3: Implement the migration function**

Add above `_migrate_config` in `src/gamarr/config.py`:

```python
def _migrate_sort_order_critic_names(raw: dict[str, Any]) -> bool:
    """Rename critic-score keys/values to critic-style names.

    Renames ``min_metascore`` -> ``min_criticscore``,
    ``min_metascore_reviews`` -> ``min_criticscore_reviews``, and the
    ``sort_order`` value ``"metascore"`` -> ``"criticscore"`` for every
    platform override. Returns True if any migration was applied; the
    caller (load_config) then rewrites gamarr.yml in place, so the
    rename is naturally one-time.
    """
    changed = False
    overrides = raw.get("review_sites", {}).get("metacritic", {}).get("platform_overrides", {})
    for platform_key, mc_pc in overrides.items():
        if not isinstance(mc_pc, dict):
            continue
        changed |= _rename_config_key(mc_pc, "min_metascore", "min_criticscore", platform_key)
        changed |= _rename_config_key(mc_pc, "min_metascore_reviews", "min_criticscore_reviews", platform_key)
        if mc_pc.get("sort_order") == "metascore":
            mc_pc["sort_order"] = "criticscore"
            logger.info("Config: migrated sort_order 'metascore'->'criticscore' for platform '{}'", platform_key)
            changed = True
    return changed
```

Register it at the END of the `_migrations` list in `_migrate_config`:

```python
            _migrate_remove_search_mode,
            _migrate_sort_order_critic_names,
```

(`load_config` already rewrites the file + bumps `config_version` whenever `migrated` is True — no other plumbing needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py::TestSortOrderCriticMigration -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "feat: migrate metascore sort/threshold config to critic names in place"
```

---

### Task 3: Rename pipeline fields and pass-through keys

**Files:**
- Modify: `src/gamarr/pipeline.py` (dataclass ~112-114, params ~140-142, kwargs ~174-176, threshold dicts ~340-342/407-409/458-460, consumer reads ~639-640/731-732/845-846/886/893/900-903, Literals at 126 and 163)
- Modify: `src/gamarr/scheduler.py` (`_build_kwargs`, ~276-279)
- Test: `tests/unit/test_pipeline.py` (~427-444 and sort-order tests), `tests/unit/test_scheduler.py` (daemon scaffolding)

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_pipeline.py`, update the `AcquisitionConfig` construction tests:

```python
        cfg = AcquisitionConfig(
            min_criticscore=75,
            min_criticscore_reviews=5,
            min_user_score=7.5,
            min_user_reviews=10,
        )
        assert cfg.min_criticscore == 75
```

Also rename the `min_metascore=…` / `min_metascore_reviews=…` kwargs in every
`run_acquisition(...)` call in the test file (e.g. `TestBacklogLatestMode`,
~lines 6370 and 6395) to `min_criticscore=…` / `min_criticscore_reviews=…`.

Add:

```python
def test_run_acquisition_accepts_userscore_sort_order(self) -> None:
    from gamarr.pipeline import run_acquisition

    # Signature-level acceptance only (network phases are mocked elsewhere):
    import inspect

    sig = inspect.signature(run_acquisition)
    assert sig.parameters["sort_order"].default == "new"
```

In `tests/unit/test_scheduler.py` daemon-mode scaffolding (TestDaemonMode), rename:
```python
                config.review_sites.metacritic.platform_overrides["pc"].min_metascore = 75
                config.review_sites.metacritic.platform_overrides["pc"].min_metascore_reviews = 5
```
to `min_criticscore` / `min_criticscore_reviews` (same lines in both daemon tests).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py::TestAcquisitionConfig -q`
Expected: FAIL — `min_metascore` unexpected keyword.

- [ ] **Step 3: Apply the renames**

In `src/gamarr/pipeline.py`:
1. `AcquisitionConfig` dataclass (lines ~112-114): `min_metascore` → `min_criticscore`, `min_metascore_reviews` → `min_criticscore_reviews`.
2. `run_acquisition` signature (lines ~140-142): same two renames; also change both `sort_order: Literal["new", "metascore"]` (lines 126 and 163) to `Literal["new", "criticscore", "userscore"]`.
3. Kwargs mapping (lines ~174-176): `min_metascore=min_metascore,` → `min_criticscore=min_criticscore,` and `min_metascore_reviews=min_metascore_reviews,` → `min_criticscore_reviews=min_criticscore_reviews,`.
4. Three threshold dicts (lines ~340-342, ~407-409, ~458-460): change the string keys `"min_metascore"` → `"min_criticscore"` and `"min_metascore_reviews"` → `"min_criticscore_reviews"` (values already use the renamed attribute names after step 3).
5. Consumer reads — rename every remaining `thresholds["min_metascore"]` / `thresholds.get("min_metascore", ...)` / `thresholds["min_metascore_reviews"]` / `thresholds.get("min_metascore_reviews", ...)` occurrence (lines ~639-640, 731-732, 845-846, 886, 893, 900-903) to the critic names.
6. Docstrings mentioning ``min_metascore`` (lines ~584, 712, 869, 879) — update the text to the critic names.

In `src/gamarr/scheduler.py` `_build_kwargs` (lines ~276-279):
```python
        "min_criticscore": mc_cfg.min_criticscore,
        "min_criticscore_reviews": mc_cfg.min_criticscore_reviews,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_pipeline.py tests/unit/test_scheduler.py -q`
Expected: PASS (no remaining `min_metascore` references in src/)

Verify with: `grep -rn "min_metascore" src/ tests/` → only expected migration-test legacy literals remain.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/pipeline.py src/gamarr/scheduler.py tests/unit/test_pipeline.py tests/unit/test_scheduler.py
git commit -m "refactor: rename critic threshold fields through pipeline and scheduler"
```

---

### Task 4: userscore browse-flow test

**Files:**
- Test: `tests/unit/test_pipeline.py` (or `tests/unit/test_metacritic.py` — URL building lives in `metacritic.py`; use `test_metacritic.py`)
- Modify: none expected in src (verify only)

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_metacritic.py`:

```python
class TestSortOrderURLSlug:
    def test_userscore_slug_used_in_browse_url(self) -> None:
        from unittest.mock import patch

        from gamarr.database import Database
        from gamarr.metacritic import MetacriticCache, MetacriticClient

        db = Database(":memory:")
        client = MetacriticClient(cache=MetacriticCache(db))
        client.sort_order = "userscore"

        class _FakeResp:
            status_code = 200
            content = b""  # parse fails -> returns None; URL assertion is the point

        with patch("gamarr.metacritic.requests.get", return_value=_FakeResp()) as mock_get:
            client._fetch_browse_page("pc", page_number=0, cache_pages_hours=0, year=0)

        url = mock_get.call_args.args[0]
        assert "/userscore/" in url
        assert "/criticscore/" not in url
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_metacritic.py::TestSortOrderURLSlug -q`
Expected: FAIL — the URL will contain `/new/` (the client's default
`self.sort_order = "new"` is used until the test sets `userscore` on the
client, so the failure should be the assertion on `/userscore/`).

- [ ] **Step 3: Make it pass (test-only fix or minimal src change)**

`MetacriticClient.__init__` requires `cache` (no default), so construct a real cache:

```python
        from gamarr.database import Database
        from gamarr.metacritic import MetacriticCache, MetacriticClient

        db = Database(":memory:")
        client = MetacriticClient(cache=MetacriticCache(db))
        client.sort_order = "userscore"

        class _FakeResp:
            status_code = 200
            content = b""  # parse fails -> returns None; URL assertion is the point

        with patch("gamarr.metacritic.requests.get", return_value=_FakeResp()) as mock_get:
            client._fetch_browse_page("pc", page_number=0, cache_pages_hours=0, year=0)

        url = mock_get.call_args.args[0]
        assert "/userscore/" in url
        assert "/criticscore/" not in url
```
(No src change should be needed — the URL slug comes straight from `self.sort_order`.)

- [ ] **Step 3b: Add the rename-triggers-progress-reset test**

Add next to the existing budget-reset tests in `tests/unit/test_pipeline.py`
(`TestBacklogLatestMode`), reusing their exact `mocker` scaffolding:

```python
def test_metascore_to_criticscore_rename_triggers_progress_reset(self, mocker: Any, tmp_path: Path) -> None:
    """A legacy 'metascore' progress state must reset when config now says criticscore."""
    from gamarr.pipeline import run_acquisition

    mock_db = mocker.patch("gamarr.pipeline.Database")
    mock_db_instance = mock_db.return_value
    mock_db_instance.sum_scanned_pages.return_value = 0
    mock_db_instance.has_verified_pending.return_value = False
    mock_db_instance.get_pending.return_value = []
    mock_db_instance.get_last_scanned_page.return_value = 0
    mock_db_instance.get_last_sort_order.return_value = "metascore"  # legacy state

    mock_qbt = mocker.patch("gamarr.pipeline.QBittorrentClient")
    mock_qbt_instance = mock_qbt.return_value
    mock_qbt_instance.is_connected.return_value = True

    mock_mc = mocker.patch("gamarr.pipeline.MetacriticClient")
    mock_mc_instance = mock_mc.return_value
    mock_mc_instance.scan_recent_games.return_value = []

    run_acquisition(
        platform="pc",
        db_path=str(tmp_path / "test.db"),
        enabled=True,
        sort_order="criticscore",
        max_pages=20,
        max_cycle_pages=4,
    )

    mock_db_instance.clear_cache.assert_called_once_with("metacritic")
    mock_db_instance.reset_progress.assert_called_once()
```

Note: `get_last_sort_order` may need to be explicitly stubbed to a real
string value if the pipeline checks `is not None` before comparing (the
MagicMock default is truthy but not a string); the stub above covers it.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_metacritic.py::TestSortOrderURLSlug -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_metacritic.py
git commit -m "test: userscore slug reaches the Metacritic browse URL"
```

---

### Task 5: README and readme-accuracy updates

**Files:**
- Modify: `README.md` (config table, ~lines 170-176)
- Test: `tests/unit/test_readme_accuracy.py`

- [ ] **Step 1: Update README rows**

Replace:
```
| `min_metascore` | Minimum Metacritic critic score (0–100). | `75` |
| `min_metascore_reviews` | Minimum number of critic reviews required. | `10` |
...
| `sort_order` | Browse sort order: `"new"` (release date) or `"metascore"` (by critic score). | `"new"` |
```
with:
```
| `min_criticscore` | Minimum Metacritic critic score (0–100). | `75` |
| `min_criticscore_reviews` | Minimum number of critic reviews required. | `10` |
...
| `sort_order` | Browse sort order: `"new"` (release date), `"criticscore"` (by critic score), or `"userscore"` (by user score). | `"new"` |
```

- [ ] **Step 2: Update the accuracy tests**

In `tests/unit/test_readme_accuracy.py`, any regex/assertion referencing `min_metascore` or the old `sort_order` description must switch to the new names (grep `metascore` in the file; update matches to `min_criticscore` and the three-value sort_order text).

- [ ] **Step 3: Run the accuracy tests**

Run: `uv run pytest tests/unit/test_readme_accuracy.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add README.md tests/unit/test_readme_accuracy.py
git commit -m "docs: document criticscore/userscore sort options and renamed thresholds"
```

---

### Task 6: Full-suite verification

**Files:** none expected (fix-only if anything surfaces)

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest tests/unit -q`
Expected: all pass (baseline 869; plus the new tests).

- [ ] **Step 2: Run lint and type gates**

Run: `uv run ruff check src/ tests/ && uv run mypy .`
Expected: "All checks passed!" and "Success: no issues found".

- [ ] **Step 3: Grep for leftover old names**

Run: `grep -rn "metascore" src/ README.md`
Expected: no hits in `src/` (the word may remain only in migration code strings and legacy test fixtures).

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "test: full-suite verification for sort_order rename"
```
(only if changes were needed; otherwise skip the commit)
