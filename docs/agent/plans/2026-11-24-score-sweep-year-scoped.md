# Year-Scoped Score Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a score-sorted backlog walk release years newest-to-oldest down to a date floor, so reach is stated once as `oldest_month` and pages are only a per-cycle workload budget.

**Architecture:** `BacklogScoreConfig` gains `oldest_month` and loses `page_depth`; a config migration converts every legacy page budget into an equivalent floor. `build_scan_plan` stops carrying a page depth and carries the floor plus a coerced pace, and `pipeline._run_discovery_phases` reads the same year loop the date-sorted backlog already uses instead of the `year=0` all-time sentinel. `utils.PAGES_PER_MONTH` becomes the measured 33. The field removal lands last so every task leaves the suite green.

**Tech Stack:** Python 3.12, pydantic v2, SQLAlchemy + SQLite, loguru, pytest, ruff, mypy, `uv`.

**Spec:** `docs/agent/specs/2026-11-24-score-sweep-year-scoped-design.md` (approved). Decisions: ADR 0001-0009, terms in `docs/agent/glossary.md` (Window floor, Pace, Sort order).

## Global Constraints

- `PAGES_PER_MONTH = 33` (measured over 43,500 games / 870 pages; ADR 0009). `pages_to_months` keeps its round-up-with-minimum-1 rule.
- `discovery.backlog.score` reads exactly two keys: `oldest_month` (default `0`, validated by `validate_oldest_month`, future month clamped) and `pages_per_cycle` (default `20`, `ge=0`, `0` coerced to `20` at plan time with one INFO line).
- `page_depth` is removed; after Task 4 an unknown key in the score block is a validation error (`extra="forbid"`).
- The floor is read at **year grain**: `201601` means all of 2016, and no per-game floor filter is applied to a score sort.
- Completion of the floor year logs and restarts from the newest year; a fetch failure never completes a sweep.
- Score sorts keep `date_mode=False` (no month pacing) while `floor_month` is read by both branches.
- No schema change, no new table, no new dependency. `metacritic.py` and `database.py` are untouched.
- README is the only user-facing doc this plan writes; glossary and ADRs are already committed.
- ASCII only in code and comments; max 50 non-blank lines per function; docstrings on public functions.

## Review Focus

1. **A live config whose score block already holds `page_depth`** (the current release writes `score: {page_depth: 500, pages_per_cycle: 20}`). A reasonable person expects the next start to work, not to fail validation. Pinned by Task 2 (`test_a_block_shaped_page_depth_converts_to_a_floor`) and Task 4 (`test_page_depth_is_rejected_once_migrated`).
2. **A score sweep with a floor must descend year by year and stop at the floor year.** If the floor is ignored the sweep walks to 1958 one probe per empty year; if the floor is applied too tightly the floor year is skipped. Pinned by Task 3 (`test_a_score_sweep_passes_the_year_to_the_client`, which asserts both the starting year and `min(years) == 2024`).
3. **A cycle whose pace is spent mid-year must resume at the next page next cycle.** A lost cursor re-reads the same pages forever. Pinned by Task 3 (`test_a_score_sweep_writes_a_cursor_per_year`).
4. **A fetch failure in the floor year must not be read as completion.** Reading it as complete resets every cursor and re-sweeps from the newest year indefinitely. Pinned by Task 3 (`test_a_failure_in_the_floor_year_does_not_complete_the_sweep`).
5. **`pages_per_cycle: 0` must not become an unpaced cycle** that crawls the catalogue in one run (the November 2026 bug shape). Pinned by Task 3 (`test_a_zero_pace_is_coerced_to_twenty`).

---

## File Structure

| File | Responsibility | Change |
| ---- | -------------- | ------ |
| `src/gamarr/utils.py` | page-to-month constant and conversion | modify `PAGES_PER_MONTH` |
| `src/gamarr/config.py` | score block model, migrations, deprecation copy | modify `BacklogScoreConfig`, `_migrate_backlog_score_block`, `_migrations` list, deprecation message; add `_land_score_floor`, `_migrate_score_page_depth` |
| `src/gamarr/discovery.py` | config -> `ScanPlan` | modify score branch, drop `ScanPlan.page_depth` |
| `src/gamarr/pipeline.py` | one discovery cycle | year-scope the score sweep, floor-year completion, drop the page-budget plumbing |
| `tests/unit/test_utils.py` | constant and conversion | modify two tests |
| `tests/unit/test_config.py` | defaults, validation, migration | modify and add |
| `tests/unit/test_discovery.py` | plan shape | modify and add |
| `tests/unit/test_pipeline.py` | cycle behaviour | modify the `_discovery` helper, rewrite the budget tests, add sweep tests |
| `tests/unit/test_readme_accuracy.py` | README defaults table | modify |
| `README.md` | operator documentation | modify rows, sample config, narrative |

## Wave note (read before starting)

Tasks 2 and 3 overlap on one axis: Task 2 converts every `page_depth` on load and stops writing it, while the field still exists in the model and in `ScanPlan` so the pipeline keeps compiling; Task 4 deletes it and the budget code that reads it. The intermediate state (a model that still declares a key the migration never writes) must never be released as its own version - it exists only between Task 2 and Task 4 on an unreleased branch.

Run `uv run pytest -q` at the end of every task. If a task's own new tests are the only failures, that is a task bug, not the wave.

---

### Task 1: The page-to-month constant is measured

**Files:**
- Modify: `src/gamarr/utils.py:213`
- Test: `tests/unit/test_utils.py:245-252`, `tests/unit/test_config.py:2189-2193` and `:2330`

**Interfaces:**
- Consumes: nothing.
- Produces: `PAGES_PER_MONTH: int = 33`; `pages_to_months(pages: int) -> int` unchanged in signature and rounding.

- [ ] **Step 1: Write the failing tests**

Replace `test_pages_per_month_is_24` in `tests/unit/test_utils.py` with:

```python
def test_pages_per_month_is_the_measured_figure(self) -> None:
    from gamarr.utils import PAGES_PER_MONTH

    assert PAGES_PER_MONTH == 33, "measured over 870 pages / 43,500 games (ADR 0009)"


def test_pages_converts_with_the_measured_constant(self) -> None:
    from gamarr.utils import pages_to_months

    assert pages_to_months(500) == 16
    assert pages_to_months(33) == 1
    assert pages_to_months(34) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_utils.py -k "pages" -v`
Expected: FAIL - `assert 24 == 33`, and `pages_to_months(500)` returns 21.

- [ ] **Step 3: Implement the constant**

In `src/gamarr/utils.py`, set the constant and replace the comment above it with the measurement and the ADR reference:

```python
# Measured on the live PC index (870 pages, 43,500 games, Nov 2024 - Nov 2026):
# 50 games per page and 33.4 pages per month, rising to ~52 in the last quarter.
# Supersedes ADR 0007's 24 (24 games per page, 24 releases per day).
PAGES_PER_MONTH = 33
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_utils.py -v`
Expected: PASS.

- [ ] **Step 5: Update the two conversion expectations the constant moves**

In `tests/unit/test_config.py`:
- `test_latest_page_depth_converts_to_a_month_pace` asserts `42`; `1000 / 33` rounds up to `31`.
- The `assert discovery["latest"]["months_per_cycle"] == 21` assertion ("the old default page reach is kept") becomes `16`.

- [ ] **Step 6: Run the whole suite and fix every remaining converted-count pin**

Run: `uv run pytest -q`
Expected: PASS. Any other test that hard-codes a converted month count (grep `== 21`, `== 42`, `== 16`) is updated to the `33`-based value; tests that call `pages_to_months` themselves need no change.

---

### Task 2: The score block gains a floor and converts every legacy page budget

**Files:**
- Modify: `src/gamarr/config.py:190-195` (`BacklogScoreConfig`), `:1402-1425` (`_migrate_backlog_score_block`), `:439-447` (deprecation copy), `:1944-1953` (`_migrations` list); add `_land_score_floor` and `_migrate_score_page_depth`
- Test: `tests/unit/test_config.py` (new tests in the class at `:2175` and the defaults class at `:2071`)

**Interfaces:**
- Consumes: `pages_to_months`, `subtract_months`, `current_year_month` (already imported in `config.py`).
- Produces:
  - `BacklogScoreConfig(oldest_month: int = 0, page_depth: int = 500, pages_per_cycle: int = 0)` - `page_depth` survives only until Task 4.
  - `_land_score_floor(score: dict[str, Any], pages: int, platform_key: str) -> bool` - writes `oldest_month` from a page count when `pages > 0` and the key is absent; returns True when it wrote.
  - `_migrate_score_page_depth(raw: dict[str, Any]) -> bool` - converts and removes a `page_depth` sitting inside an already-block-shaped `discovery.backlog.score`.
  - After migration, no file carries `page_depth`.

- [ ] **Step 1: Write the failing tests**

Add to the defaults class (`:2071`):

```python
def test_backlog_score_reaches_by_month_and_paces_by_pages(self) -> None:
    score = self._defaults().backlog.score
    assert score.oldest_month == 0
    assert score.pages_per_cycle == 20, "a score sweep is always paced by default"
```

Replace `test_backlog_score_keeps_the_page_keys` (`:2209`) with:

```python
def test_backlog_score_converts_the_page_budget_to_a_floor(self) -> None:
    from gamarr.utils import current_year_month, pages_to_months, subtract_months

    raw = self._migrate({"mode": "backlog", "max_pages": 40, "max_cycle_pages": 5, "sort_order": "criticscore"})
    score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
    assert score == {
        "oldest_month": subtract_months(current_year_month(), pages_to_months(40)),
        "pages_per_cycle": 5,
    }
```

Add to the same class:

```python
def test_a_block_shaped_page_depth_converts_to_a_floor(self) -> None:
    """The shape the current release writes must load and convert (ADR 0007)."""
    from gamarr.utils import current_year_month, pages_to_months, subtract_months

    raw = self._migrate(
        {
            "discovery": {
                "mode": "backlog",
                "backlog": {"sort_order": "criticscore", "score": {"page_depth": 500, "pages_per_cycle": 20}},
            }
        }
    )
    score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
    assert score == {
        "oldest_month": subtract_months(current_year_month(), pages_to_months(500)),
        "pages_per_cycle": 20,
    }


def test_a_zero_page_depth_writes_no_floor(self) -> None:
    raw = self._migrate(
        {"discovery": {"mode": "backlog", "backlog": {"sort_order": "criticscore", "score": {"page_depth": 0}}}}
    )
    score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
    assert "oldest_month" not in score, "a legacy 0 budget means no budget, not a floor of now"
    assert "page_depth" not in score


def test_an_explicit_floor_beats_a_legacy_page_depth(self) -> None:
    raw = self._migrate(
        {
            "discovery": {
                "mode": "backlog",
                "backlog": {
                    "sort_order": "criticscore",
                    "score": {"oldest_month": 201601, "page_depth": 500},
                },
            }
        }
    )
    score = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["score"]
    assert score["oldest_month"] == 201601
    assert "page_depth" not in score
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -k "score" -v`
Expected: FAIL - `BacklogScoreConfig` has no `oldest_month`, and the block-shaped `page_depth` survives.

- [ ] **Step 3: Implement the model field and the shared converter**

In `src/gamarr/config.py`, add `oldest_month: int = 0` to `BacklogScoreConfig` with the `validate_oldest_month` field validator (copy the `BacklogNewConfig` shape) and a docstring line: `page_depth: legacy total page budget, converted to ``oldest_month`` on load and removed in the next task.`

Add below `_migrate_backlog_new_pages`:

```python
def _land_score_floor(score: dict[str, Any], pages: int, platform_key: str) -> bool:
    """Convert a legacy page budget into the score block's floor month."""
```

It returns False for `pages <= 0`, otherwise writes
`_clamp_floor(subtract_months(current_year_month(), pages_to_months(pages)), platform_key)` with `_place(score, "oldest_month", floor, "page_depth", platform_key)`, and logs `Config: converted page budget {} to oldest_month {} for platform '{}' (approximate)` when `_place` wrote it.

- [ ] **Step 4: Route both migration paths through it**

- `_migrate_backlog_score_block`: replace the `page_depth` `_place` with `_land_score_floor(score, pages, platform_key)` when `pages is not None`; keep the `pages_per_cycle` copy unchanged; update the docstring (it no longer "copies unchanged" for the budget).
- Add `_migrate_score_page_depth(raw)` after `_migrate_discovery_blocks`'s definition: for each `(platform_key, plat)` in `_discovery_overrides(raw)`, walk `plat["discovery"]["backlog"]["score"]` with an `isinstance(..., dict)` guard at every level, and when `"page_depth"` is present, pop it and call `_land_score_floor(score, popped, platform_key)`. Return True when anything changed.
- Register `_migrate_score_page_depth` in the `_migrations` list (`:1944-1953`) immediately after `_migrate_discovery_blocks`.
- Update the deprecated-key message at `:443` to name `discovery.backlog.score.oldest_month` instead of `discovery.backlog.score.page_depth`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: PASS.

- [ ] **Step 6: Verify the whole suite still passes**

Run: `uv run pytest -q`
Expected: PASS - the pipeline still reads `plan.page_depth`, which the model still declares.

---

### Task 3: The score sweep is year-scoped

**Files:**
- Modify: `src/gamarr/discovery.py:17-89`, `src/gamarr/pipeline.py:444-470` (year range), `:585-590` (`year=`), `:594-596` (floor filter), `:657-690` (floor-year completion), `:716-735` (sweep completion), `:958-990` (progress log)
- Test: `tests/unit/test_discovery.py`, `tests/unit/test_pipeline.py`

**Interfaces:**
- Consumes: `BacklogScoreConfig.oldest_month`, `pages_per_cycle` (Task 2); `EARLIEST_BROWSE_YEAR`, `scan_year_anchor`, `_games_at_or_after_floor`, `db.get_last_scanned_page`, `db.set_last_scanned_page`, `db.reset_progress` (existing).
- Produces:
  - `ScanPlan(mode, scan_order, date_mode, floor_month, pace_months, page_depth, pages_per_cycle)` - unchanged fields; a score plan now carries `floor_month > 0` and `pages_per_cycle == 20` when the config said `0`.
  - `build_scan_plan` logs `Config: pages_per_cycle 0 has no meaning for a score sweep - using 20 so one cycle cannot crawl the catalogue` (loguru INFO) exactly when it coerces.

- [ ] **Step 1: Write the failing plan tests**

In `tests/unit/test_discovery.py`, replace `test_backlog_score_is_page_mode` and `test_a_floorless_unpaced_backlog_gets_a_pace`'s score sibling with:

```python
def test_backlog_score_carries_a_floor_and_a_page_pace(self) -> None:
    discovery = DiscoveryConfig.model_validate(
        {
            "mode": "backlog",
            "backlog": {"sort_order": "criticscore", "score": {"oldest_month": 201601, "pages_per_cycle": 20}},
        }
    )
    plan = build_scan_plan(discovery)
    assert plan.scan_order == "criticscore"
    assert plan.date_mode is False, "a score sweep is not month-paced"
    assert plan.floor_month == 201601, "the floor bounds the score sweep"
    assert plan.pace_months == 0
    assert plan.pages_per_cycle == 20


def test_a_zero_page_pace_is_coerced_to_twenty(self) -> None:
    discovery = DiscoveryConfig.model_validate(
        {"mode": "backlog", "backlog": {"sort_order": "userscore", "score": {"pages_per_cycle": 0}}}
    )
    plan = build_scan_plan(discovery)
    assert plan.pages_per_cycle == 20, "one cycle must never crawl the catalogue"
```

- [ ] **Step 2: Write the failing pipeline tests**

In `tests/unit/test_pipeline.py`, update the `_discovery` helper's score payload to
`backlog["score"] = {"oldest_month": oldest_month, "page_depth": page_depth, "pages_per_cycle": pages_per_cycle}`,
and add `oldest_month` to the score-sort calls that exist today so each walks one year, for example
`_discovery(sort_order="criticscore", oldest_month=<current year>, page_depth=20, pages_per_cycle=4)`.

Add, modelled on `test_new_sort_order_descends_through_drained_years` (`tests/unit/test_pipeline.py:7985`): copy that test's mock scaffolding (the `Database`/`QBittorrentClient`/`MetacriticClient` patches, the `_mock_scan` side effect and the `run_acquisition` argument list) verbatim, changing only the discovery payload and the assertions below.

```python
def test_a_score_sweep_passes_the_year_to_the_client(self, mocker: Any, tmp_path: Path) -> None:
    """A score sort reads year-scoped listings, newest year first (ADR 0008)."""
    # ... same mock scaffolding as test_new_sort_order_descends_through_drained_years ...
    mock_mc_instance._recent_games_exhausted = True
    run_acquisition(
        platform="pc",
        db_path=str(tmp_path / "test.db"),
        enabled=True,
        discovery=_discovery(sort_order="criticscore", oldest_month=202401, pages_per_cycle=20),
    )
    years = [c.kwargs["year"] for c in mock_mc_instance.scan_recent_games.call_args_list]
    assert years[:1] == [datetime.datetime.now(tz=datetime.UTC).year]
    assert min(years) == 2024, "the sweep stops at the floor year"
    assert None not in years, "the all-time listing is never requested"


def test_a_score_sweep_keeps_the_whole_floor_year(self, mocker: Any, tmp_path: Path) -> None:
    """Year grain: no per-game floor filter is applied to a score sort."""
    # Same scaffolding as above, plus mocker.patch("gamarr.pipeline._games_at_or_after_floor") as mock_floor_filter.
    run_acquisition(
        platform="pc",
        db_path=str(tmp_path / "test.db"),
        enabled=True,
        discovery=_discovery(sort_order="userscore", oldest_month=201601, pages_per_cycle=20),
    )
    mock_floor_filter.assert_not_called()


def test_a_score_sweep_writes_a_cursor_per_year(self, mocker: Any, tmp_path: Path) -> None:
    """A pace spent mid-year resumes at the next page, not page 1."""
    # Same scaffolding, with _mock_scan setting mock_mc_instance._recent_games_last_page = kwargs["start_page"] + 3
    # and _recent_games_exhausted = False.
    run_acquisition(
        platform="pc",
        db_path=str(tmp_path / "test.db"),
        enabled=True,
        discovery=_discovery(sort_order="criticscore", oldest_month=202601, pages_per_cycle=4),
    )
    year = datetime.datetime.now(tz=datetime.UTC).year
    mock_db_instance.set_last_scanned_page.assert_any_call("pc", year, 4)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_discovery.py -k "score or pace" tests/unit/test_pipeline.py -k "score_sweep" -v`
Expected: FAIL - the score plan has `floor_month == 0`, and the pipeline passes `year=None`.

- [ ] **Step 4: Implement the plan change**

In `src/gamarr/discovery.py`:
- Score branch returns
  `ScanPlan(mode="backlog", scan_order=backlog.sort_order, date_mode=False, floor_month=backlog.score.oldest_month, pace_months=0, page_depth=backlog.score.page_depth, pages_per_cycle=pace)` where `pace = backlog.score.pages_per_cycle or 20`, and logs the coercion line from the Interfaces block when `backlog.score.pages_per_cycle == 0`.
- Restate the `ScanPlan` docstrings: `floor_month` is read by both branches; `date_mode` selects month pacing only.

- [ ] **Step 5: Implement the pipeline year scope**

In `src/gamarr/pipeline.py`:
- Year range: replace the score branch's `cutoff_year = 0; current_year = 0` with `cutoff_year = floor_month // 100 if floor_month else EARLIEST_BROWSE_YEAR; current_year = scan_year_anchor`, and delete the "no year dimension" comment.
- Pass `year=scan_year` unconditionally; delete the `if scan_order == "new"` conditional and its comment.
- Apply `_games_at_or_after_floor` only when `scan_order == "new"`.
- Floor-year completion: move the `scan_year == cutoff_year` computation out of the `if date_mode:` block into a shared step that keeps the date-mode logic verbatim and, for a non-`new` sort, sets `floor_year_finished` when `scan_year == cutoff_year` and (`_recent_games_exhausted` is True or the probe scanned zero pages) and not `_recent_games_failed` and not cancelled. A score sweep never reads `_recent_games_cutoff_reached`.
- Sweep completion: change `if date_mode and not latest_mode:` to `if not latest_mode:` and fold the existing `elif not latest_mode:` drained-restart branch into it (that branch is the `range_empty` case, so leaving it as an `elif` would silently lose it). Choose the message by sort (`Backlog complete - reached year {} (floor year {}), restarting from the newest year` for a score sweep; the existing month message otherwise), and keep the `db.reset_progress(platform, scan_order)` call.
- Track `last_year_scanned: int = 0`, assigned at the top of the year loop, and rewrite the progress line for a score sweep as
  `Backlog sweep ({} order): reached year {}, floor {}, {} page(s) this cycle` with `(scan_order, last_year_scanned, floor_month // 100 if floor_month else "none", pages_this_cycle)`.

- [ ] **Step 6: Add the failure and completion tests**

```python
def test_a_failure_in_the_floor_year_does_not_complete_the_sweep(self, mocker: Any, tmp_path: Path) -> None:
    """A fetch failure must never be read as 'floor reached' (it would reset every cursor)."""
    # ... scaffolding; scan_recent_games sets _recent_games_failed = True ...
    run_acquisition(
        platform="pc",
        db_path=str(tmp_path / "test.db"),
        enabled=True,
        discovery=_discovery(sort_order="criticscore", oldest_month=202601, pages_per_cycle=20),
    )
    mock_db_instance.reset_progress.assert_not_called()


def test_reaching_the_floor_year_restarts_from_the_newest_year(self, mocker: Any, tmp_path: Path) -> None:
    """Completion resets the per-year cursors so the next pass starts over (ADR 0008)."""
    # ... scaffolding; the floor year returns an exhausted empty listing ...
    run_acquisition(
        platform="pc",
        db_path=str(tmp_path / "test.db"),
        enabled=True,
        discovery=_discovery(sort_order="criticscore", oldest_month=202601, pages_per_cycle=20),
    )
    mock_db_instance.reset_progress.assert_called_once_with("pc", "criticscore")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_discovery.py tests/unit/test_pipeline.py -q`
Expected: PASS.

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS - the page budget still works because `ScanPlan.page_depth` is still populated.

---

### Task 4: Remove `page_depth` and the page-budget plumbing

**Files:**
- Modify: `src/gamarr/config.py:190-195`, `src/gamarr/discovery.py:26-89`, `src/gamarr/pipeline.py:471-505` (budget setup), `:536-540` (stop), `:566-600` (per-call allowance), `:684-686` (decrement), `:975-984` (progress percentage)
- Test: `tests/unit/test_config.py`, `tests/unit/test_discovery.py`, `tests/unit/test_pipeline.py:35-63` and the score-budget tests at `:6795-7000`, `:10608`

**Interfaces:**
- Consumes: Task 2's migration (no file carries `page_depth` any more) and Task 3's year scope.
- Produces: `BacklogScoreConfig(oldest_month, pages_per_cycle)`; `ScanPlan` without `page_depth`; `pages_per_cycle` as the only page bound in a cycle.

- [ ] **Step 1: Write the failing tests**

```python
def test_page_depth_is_rejected_once_migrated(self) -> None:
    """The key is gone from the model; a file that still has it is converted before validation."""
    from pydantic import ValidationError

    from gamarr.config import BacklogScoreConfig

    with pytest.raises(ValidationError):
        BacklogScoreConfig.model_validate({"page_depth": 500})


def test_a_page_budget_no_longer_caps_a_score_sweep(self, mocker: Any, tmp_path: Path) -> None:
    """Pages only pace a cycle; nothing accumulates a total (ADR 0008)."""
    # ... scaffolding ...
    run_acquisition(
        platform="pc",
        db_path=str(tmp_path / "test.db"),
        enabled=True,
        discovery=_discovery(sort_order="criticscore", oldest_month=202601, pages_per_cycle=4),
    )
    mock_db_instance.sum_scanned_pages.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -k "page_depth" tests/unit/test_pipeline.py -k "page_budget" -v`
Expected: FAIL - `page_depth` validates, and `sum_scanned_pages` is still called.

- [ ] **Step 3: Remove the field**

- `BacklogScoreConfig`: delete `page_depth`; the docstring loses its legacy note.
- `ScanPlan`: delete the `page_depth` field and its docstring line; the three construction sites in `build_scan_plan` drop the argument.
- `pipeline.py`: delete `max_pages_cfg`, `remaining_pages`, both `db.sum_scanned_pages(...)` calls, the `Budget exhausted - ... restarting from page 1` reset, the `if max_pages_cfg > 0 and remaining_pages <= 0: break` stop, the `remaining_pages -= pages_scanned` decrement, the `per_call_max = remaining_pages ...` line (the per-call allowance becomes `allowance_left` only), and the `Progress: {} of {} pages scanned ({}%)` branch.
- Leave `page_depth` in `_STRAY_DISCOVERY_KEYS` (`src/gamarr/config.py:41`): a stray key one level too high must still be dropped with its notice rather than rejected.

- [ ] **Step 4: Rewrite the tests the removal invalidates**

In `tests/unit/test_pipeline.py`: `_discovery`'s score payload becomes `{"oldest_month": oldest_month, "pages_per_cycle": pages_per_cycle}` and the `page_depth` parameter is deleted; delete
`test_budget_exhausted_resets_and_continues`, `test_budget_exhausted_when_total_exceeds_max`,
`test_the_page_depth_and_the_cycle_allowance_are_separate`, `test_a_cycle_allowance_runs_with_the_page_depth`,
and `test_a_zero_page_depth_reaches_the_client_as_no_cap`, replacing the last one with
`test_a_floorless_score_sweep_asks_the_client_for_no_page_cap` (same assertion on `max_pages=0`, discovery payload
`{"oldest_month": 0, "pages_per_cycle": 20}`). In `tests/unit/test_config.py` delete the two `page_depth` assertions
at `:86` and `:2087` and `test_page_depth_rejects_a_negative_value` (`:2125`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py tests/unit/test_discovery.py tests/unit/test_pipeline.py -q`
Expected: PASS.

- [ ] **Step 6: Run the whole suite and the type checks**

Run: `uv run pytest -q && uv run mypy .`
Expected: PASS, no unused-variable or attribute errors from the removed names.

---

### Task 5: README describes the year-scoped sweep

**Files:**
- Modify: `README.md:220-232` (sample config), `:248-249` (defaults rows), `:388-396` (narrative paragraph)
- Test: `tests/unit/test_readme_accuracy.py:263-269`

**Interfaces:**
- Consumes: `BacklogScoreConfig` defaults from Task 4.
- Produces: no code; the README rows the accuracy test reads.

- [ ] **Step 1: Write the failing test**

Replace `test_backlog_page_depth_default_row` (`:263`):

```python
def test_backlog_score_floor_and_pace_default_rows(self) -> None:
    from gamarr.config import DiscoveryConfig

    discovery = DiscoveryConfig()
    defaults = self._readme_defaults()
    assert defaults.get("discovery.backlog.score.oldest_month") == str(discovery.backlog.score.oldest_month)
    assert defaults.get("discovery.backlog.score.pages_per_cycle") == str(discovery.backlog.score.pages_per_cycle)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_readme_accuracy.py -k score -v`
Expected: FAIL - the README has no `oldest_month` row and still lists `page_depth`.

- [ ] **Step 3: Rewrite the README rows**

Replace the `discovery.backlog.score.page_depth` row with
`discovery.backlog.score.oldest_month`: "Oldest month a score-sorted sweep may read, as `YYYYMM`. Read at year grain - the index filters by whole release year, so `201601` reads all of 2016. `0` = no floor, so the sweep runs down to the earliest listing. A later month is clamped to the current one." Default `0`.
Change the `pages_per_cycle` row to default `20` and: "Pages one cycle reads for a score-sorted sweep - a workload budget, not a reach. `0` is treated as `20` so a cycle cannot crawl the catalogue."

- [ ] **Step 4: Rewrite the sample config and the narrative**

- Sample block (`:220-232`): `score: {oldest_month: 201601, pages_per_cycle: 20}`.
- Narrative (`:388-396`): a score-sorted sweep walks release years newest to oldest with a per-year cursor, stops at `oldest_month` read at year grain, restarts at the newest year once the floor year is drained, accepts page-boundary drift as best-effort (dedup prevents repeat work), cannot see titles with no release date, and is paced by `pages_per_cycle`. Replace the "24 pages is about one month" claim with the measured 33 pages/month where the README makes that claim.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_readme_accuracy.py -q`
Expected: PASS.

- [ ] **Step 6: Lint the README and run the whole suite**

Run: `markdownlint-cli2 --fix README.md && uv run pytest -q`
Expected: PASS.
