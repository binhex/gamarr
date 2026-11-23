# Per-Mode Discovery Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split gamarr's flat discovery config into `latest`/`backlog` blocks with unit-named
keys, drop the page-depth fallback from `latest`, and migrate every existing config on load.

**Architecture:** Three layers move in order. (1) `utils.pages_to_months` becomes the single
page-to-month constant. (2) `config.py` gains the nested `DiscoveryConfig` models, `pending_order`
and `source_matching_days`, plus two migration steps that rewrite the legacy flat shape. (3) A new
`discovery.py` resolves the config into an immutable `ScanPlan`, which `pipeline.py` and
`scheduler.py` consume, replacing the ad-hoc mode/date/page branches. The legacy model fields are
removed last, so every task leaves the suite green.

**Tech Stack:** Python 3.12, pydantic v2, SQLAlchemy + SQLite, loguru, pytest, ruff, mypy, `uv`.

**Spec:** `docs/agent/specs/2026-11-22-discovery-config-restructure-design.md` (approved).

**Decisions:** ADR 0001-0007 in `docs/agent/decisions/`, terms in `docs/agent/glossary.md`.

**Commit policy:** when this plan runs inside the chain, do **not** commit between tasks — the finalising step owns commits.

---

## Wave note (read before starting)

Tasks 2-11 keep the old model fields alive alongside the new ones, so the suite stays green at every
task boundary. Task 11 deletes the legacy fields. The intermediate dual-shape state (old `mode`
defaulting to `backlog` while `discovery.mode` defaults to `latest`) must never be released: it only
exists between task 2 and task 11 inside one unreleased branch.

Run `uv run pytest -q` at the end of every task. If a task's own new tests are the only new failures,
that is a task bug, not the wave.

## Refinements found while planning

1. **`backlog.sort_order` is written by migration only when the legacy config runs `backlog` mode.**
   Spec rule 6 copies `sort_order` into both `pending_order` and `backlog.sort_order`; copying it into
   a block a `latest` config never reads would make every `latest` load report one guaranteed inert
   key. `pending_order` is always written.
2. **The DB keeps its `sort_order=` parameter name.** `Database.get_pending(sort_order=...)` orders
   rows and has no mode concept; renaming it would churn 54 test call sites for no reader benefit. The
   pipeline passes `cfg.pending_order` into it.
3. **`backlog.score.page_depth: 0` maps to `EARLIEST_BROWSE_YEAR`** in the year-range branch, which is
   what "sweep to the earliest year" means once the window is no longer derived from a fixed 10 years.
4. **Migrations write the converted shape back to disk** through the existing `load_config` merge, so a
   migrated file contains blocks on the next load and the flat keys are gone.
5. **`report_inert_keys` reads `model_fields_set`,** not the dumped values, so model defaults never
   produce a spurious inert-key line.
6. **A platform that already holds a non-empty `discovery` block is treated as migrated.** Until Task 11
the legacy fields still re-emit their defaults into the rewritten file, so a second load would
otherwise flip `discovery.mode` back to `backlog` and derive a floor from the default
`max_pages: 500`. The block wins (spec rule 8) and the leftover flat keys are logged and left alone.
7. **`max_pages` converts to a month pace only when the legacy latest config was depth-driven** (no
floor and no pace). ADR 0003 scopes that conversion to a page-depth-only config; with a floor
already set the page key was inert and is dropped with INFO instead, which is what the plan's own
`test_latest_drops_the_page_keys_it_never_read` asserts.
8. **`0` counts as unset for `max_date` / `max_cycle_months` during migration**, because every legacy
file carries those defaults and a depth-driven `latest` config would otherwise migrate unpaced. The
cost: an explicitly unpaced legacy latest cycle (`max_cycle_months: 0`) gains the new 1-month
default, which reads less, never more.

## Scope check

One subsystem: the config surface and the scan plan that consumes it. Every task touches the config
model, the migration chain, the scan-plan resolution, or documentation of those. No plan split needed.

## File map

| File | Responsibility after this change |
| --- | --- |
| `src/gamarr/utils.py` | `PAGES_PER_MONTH` and `pages_to_months`, the single page-to-month conversion |
| `src/gamarr/config.py` | `DiscoveryConfig` and its blocks; `pending_order`; `source_matching_days`; two new migration steps; legacy fields removed |
| `src/gamarr/discovery.py` | **new** - `ScanPlan`, `build_scan_plan`, `inert_key_names`, `report_inert_keys` |
| `src/gamarr/pipeline.py` | `AcquisitionConfig`/`run_acquisition` take a plan; discovery reads come from the plan; dead mode helpers and the aged-games sweep deleted; `source_matching_days` rename |
| `src/gamarr/scheduler.py` | `_matching_window_days` resolves from sources then the top-level default; `_build_kwargs` forwards the plan |
| `src/gamarr/database.py` | `update_pending_expiry` parameter renamed to `source_matching_days` |
| `tests/unit/test_utils.py` | conversion tests |
| `tests/unit/test_config.py` | model, defaults, validation and both migration steps |
| `tests/unit/test_discovery.py` | **new** - plan matrix and inert-key reporting |
| `tests/unit/test_pipeline.py` | plan-driven scan cases; dead-helper tests removed; kwargs updated |
| `tests/unit/test_scheduler.py` | window precedence; `_build_kwargs` shape |
| `tests/unit/test_readme_accuracy.py` | new key names and defaults |
| `README.md` | discovery keys grouped by mode; worked example; no warn-and-coerce text |

---

### Task 1: The page-to-month constant

**Files:**

- Modify: `src/gamarr/utils.py`
- Test: `tests/unit/test_utils.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_utils.py`:

```python
class TestPagesToMonths:
    """One page is about one day of PC releases: 24 pages per month."""

    def test_a_month_of_pages_is_one_month(self) -> None:
        from gamarr.utils import pages_to_months

        assert pages_to_months(24) == 1

    def test_rounding_is_up_and_never_zero(self) -> None:
        from gamarr.utils import pages_to_months

        assert pages_to_months(1) == 1
        assert pages_to_months(16) == 1
        assert pages_to_months(25) == 2

    def test_a_large_budget_matches_the_measured_figure(self) -> None:
        from gamarr.utils import pages_to_months

        assert pages_to_months(500) == 21

    def test_the_constant_is_named_once(self) -> None:
        from gamarr.utils import PAGES_PER_MONTH

        assert PAGES_PER_MONTH == 24
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_utils.py::TestPagesToMonths -v`
Expected: FAIL with `ImportError: cannot import name 'pages_to_months'`

- [ ] **Step 3: Implement the helper**

Add to `src/gamarr/utils.py` after the `EARLIEST_BROWSE_YEAR` block:

```python
# One browse page carries roughly 24 PC releases, and roughly 24 are released per
# day, so a page is about a day and a month is about 24 pages.  The figure is the
# repo's own July measurement (500 pages is about 21 months); every page-to-month
# conversion and the year-window derivation use it so the codebase holds one
# model of the same quantity.
PAGES_PER_MONTH = 24


def pages_to_months(pages: int) -> int:
    """Convert a browse-page count to whole months.

    Rounds up, with a minimum of 1: a converted bound must never read less than
    the page count it replaced, and a page count must never become a 0-month
    pace.

    Args:
        pages: The page count to convert.

    Returns:
        Whole months, at least 1.
    """
    return max(1, math.ceil(pages / PAGES_PER_MONTH))
```

If `math` is not already imported at the top of `utils.py`, add `import math`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_utils.py -v`
Expected: PASS

---

### Task 2: Add the `discovery` models, `pending_order` and `source_matching_days`

Additive step: the legacy fields stay until Task 11.

**Files:**

- Modify: `src/gamarr/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
class TestDiscoveryConfig:
    """The per-mode discovery blocks (ADR 0001, 0003, 0005)."""

    def _defaults(self) -> Any:
        from gamarr.config import MetacriticPlatformConfig

        return MetacriticPlatformConfig().discovery

    def test_default_mode_is_latest_with_one_month_of_pace(self) -> None:
        discovery = self._defaults()
        assert discovery.mode == "latest"
        assert discovery.latest.oldest_month == 0
        assert discovery.latest.months_per_cycle == 1

    def test_backlog_defaults_keep_todays_shape(self) -> None:
        backlog = self._defaults().backlog
        assert backlog.sort_order == "new"
        assert backlog.new.oldest_month == 0
        assert backlog.new.months_per_cycle == 0
        assert backlog.score.page_depth == 500
        assert backlog.score.pages_per_cycle == 0

    def test_latest_has_no_page_keys(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import LatestDiscoveryConfig

        with pytest.raises(ValidationError):
            LatestDiscoveryConfig.model_validate({"page_depth": 500})

    def test_a_legacy_key_inside_a_block_is_an_error(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import BacklogDiscoveryConfig

        with pytest.raises(ValidationError):
            BacklogDiscoveryConfig.model_validate({"sort_order": "new", "new": {"max_date": 202001}})

    def test_oldest_month_is_clamped_when_in_the_future(self) -> None:
        from gamarr.config import LatestDiscoveryConfig
        from gamarr.utils import current_year_month

        future = current_year_month() + 100
        assert LatestDiscoveryConfig(oldest_month=future).oldest_month == current_year_month()
        assert LatestDiscoveryConfig(oldest_month=202001).oldest_month == 202001

    def test_oldest_month_rejects_a_bad_month_and_an_early_year(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import LatestDiscoveryConfig

        with pytest.raises(ValidationError):
            LatestDiscoveryConfig(oldest_month=202013)
        with pytest.raises(ValidationError):
            LatestDiscoveryConfig(oldest_month=195701)

    def test_months_per_cycle_is_capped(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import LatestDiscoveryConfig

        assert LatestDiscoveryConfig(months_per_cycle=1200).months_per_cycle == 1200
        with pytest.raises(ValidationError):
            LatestDiscoveryConfig(months_per_cycle=1201)


class TestQueueConfig:
    """`pending_order` and `source_matching_days` (ADR 0004, 0006)."""

    def test_pending_order_defaults_to_new_at_metacritic_level(self) -> None:
        from gamarr.config import MetacriticConfig

        assert MetacriticConfig().pending_order == "new"

    def test_top_level_source_matching_days_defaults_to_60(self) -> None:
        from gamarr.config import Config

        assert Config().source_matching_days == 60

    def test_a_source_overrides_the_window(self) -> None:
        from gamarr.config import SourceConfigEntry

        entry = SourceConfigEntry(name="fitgirl", source_matching_days=90)
        assert entry.source_matching_days == 90
        assert SourceConfigEntry(name="fitgirl").source_matching_days == 60

    def test_pending_order_rejects_an_unknown_value(self) -> None:
        from pydantic import ValidationError

        from gamarr.config import MetacriticConfig

        with pytest.raises(ValidationError):
            MetacriticConfig(pending_order="metascore")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py::TestDiscoveryConfig tests/unit/test_config.py::TestQueueConfig -v`
Expected: FAIL with `AttributeError: 'MetacriticPlatformConfig' object has no attribute 'discovery'` and
`ValidationError` for the unknown `pending_order`/`source_matching_days` fields

- [ ] **Step 3: Add the models and fields**

In `src/gamarr/config.py`, add `ConfigDict` to the pydantic import:

```python
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator
```

Add the four block models and the container **above** `class MetacriticPlatformConfig`:

```python
def validate_oldest_month(value: int) -> int:
    """Validate an ``oldest_month`` value and clamp a future month.

    ``0`` means "no floor".  A floor later than the current calendar month would
    read nothing, so it is clamped rather than rejected.

    Args:
        value: The configured ``YYYYMM`` floor, or 0.

    Returns:
        ``0`` when there is no floor, otherwise the validated (possibly clamped)
        month.

    Raises:
        ValueError: When *value* is neither 0 nor a valid ``YYYYMM`` month.
    """
    if not is_valid_year_month(value):
        raise ValueError(
            "oldest_month must be 0 (no floor) or YYYYMM with month 01-12 and year >= "
            f"{EARLIEST_BROWSE_YEAR}, e.g. 202401",
        )
    if value == 0:
        return 0
    current_month = current_year_month()
    if value > current_month:
        logger.warning("Config: oldest_month {} is in the future — clamping to {}", value, current_month)
        return current_month
    return value


class LatestDiscoveryConfig(BaseModel):
    """Month-paced settings for ``mode: latest`` (ADR 0003)."""

    model_config = ConfigDict(extra="forbid")

    oldest_month: int = 0
    months_per_cycle: int = Field(default=1, ge=0, le=1200)

    @field_validator("oldest_month")
    @classmethod
    def _check_oldest_month(cls, value: int) -> int:
        return validate_oldest_month(value)


class BacklogNewConfig(BaseModel):
    """Date-paced settings for ``backlog`` with ``sort_order: new``."""

    model_config = ConfigDict(extra="forbid")

    oldest_month: int = 0
    months_per_cycle: int = Field(default=0, ge=0, le=1200)

    @field_validator("oldest_month")
    @classmethod
    def _check_oldest_month(cls, value: int) -> int:
        return validate_oldest_month(value)


class BacklogScoreConfig(BaseModel):
    """Page-paced settings for ``backlog`` with a score sort (ADR 0005)."""

    model_config = ConfigDict(extra="forbid")

    page_depth: int = Field(default=500, ge=0)
    pages_per_cycle: int = Field(default=0, ge=0)


class BacklogDiscoveryConfig(BaseModel):
    """Backlog blocks, selected by ``sort_order`` (ADR 0005)."""

    model_config = ConfigDict(extra="forbid")

    sort_order: Literal["new", "criticscore", "userscore"] = "new"
    new: BacklogNewConfig = Field(default_factory=BacklogNewConfig)
    score: BacklogScoreConfig = Field(default_factory=BacklogScoreConfig)


class DiscoveryConfig(BaseModel):
    """How one cycle walks the release list (ADR 0001)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["latest", "backlog"] = "latest"
    latest: LatestDiscoveryConfig = Field(default_factory=LatestDiscoveryConfig)
    backlog: BacklogDiscoveryConfig = Field(default_factory=BacklogDiscoveryConfig)
```

Add the field to `MetacriticPlatformConfig`, keeping the legacy fields for now:

```python
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
```

Add to `MetacriticConfig`:

```python
    pending_order: Literal["new", "criticscore", "userscore"] = "new"
```

Add to `SourceConfigEntry`, next to `max_queue_days`:

```python
    source_matching_days: int = Field(default=60, ge=0)
```

Add to `Config`, next to the `download_sites` field:

```python
    source_matching_days: int = Field(default=60, ge=0)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS (the whole file, including the pre-existing tests)

---

### Task 3: `src/gamarr/discovery.py` — the scan plan

**Files:**

- Create: `src/gamarr/discovery.py`
- Test: `tests/unit/test_discovery.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_discovery.py`:

```python
"""Tests for the discovery scan plan."""

from __future__ import annotations

from gamarr.config import DiscoveryConfig
from gamarr.discovery import build_scan_plan, inert_key_names


class TestBuildScanPlan:
    """One plan per mode and sort, no key ambiguity (ADR 0001, 0003, 0005)."""

    def test_latest_is_always_date_mode_in_new_order(self) -> None:
        plan = build_scan_plan(DiscoveryConfig.model_validate({"mode": "latest"}))
        assert plan.mode == "latest"
        assert plan.scan_order == "new"
        assert plan.date_mode is True
        assert plan.floor_month == 0
        assert plan.pace_months == 1
        assert plan.page_depth == 0
        assert plan.pages_per_cycle == 0

    def test_latest_carries_floor_and_pace(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {"mode": "latest", "latest": {"oldest_month": 202601, "months_per_cycle": 3}}
        )
        plan = build_scan_plan(discovery)
        assert plan.floor_month == 202601
        assert plan.pace_months == 3

    def test_backlog_new_is_date_mode(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {"mode": "backlog", "backlog": {"sort_order": "new", "new": {"oldest_month": 202001}}}
        )
        plan = build_scan_plan(discovery)
        assert plan.scan_order == "new"
        assert plan.date_mode is True
        assert plan.floor_month == 202001
        assert plan.page_depth == 0

    def test_backlog_score_is_page_mode(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {"mode": "backlog", "backlog": {"sort_order": "criticscore", "score": {"page_depth": 40}}}
        )
        plan = build_scan_plan(discovery)
        assert plan.scan_order == "criticscore"
        assert plan.date_mode is False
        assert plan.floor_month == 0
        assert plan.pace_months == 0
        assert plan.page_depth == 40

    def test_userscore_shares_the_score_block(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {"mode": "backlog", "backlog": {"sort_order": "userscore", "score": {"pages_per_cycle": 7}}}
        )
        plan = build_scan_plan(discovery)
        assert plan.scan_order == "userscore"
        assert plan.pages_per_cycle == 7


class TestInertKeyNames:
    """Only explicitly configured keys in unread blocks are reported."""

    def test_defaults_report_nothing(self) -> None:
        assert inert_key_names(DiscoveryConfig()) == []

    def test_a_latest_config_reports_the_backlog_block(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {"mode": "latest", "backlog": {"new": {"oldest_month": 202001}}}
        )
        assert inert_key_names(discovery) == ["backlog.new.oldest_month"]

    def test_a_new_backlog_reports_the_score_block(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {"mode": "backlog", "backlog": {"sort_order": "new", "score": {"page_depth": 80}}}
        )
        assert inert_key_names(discovery) == ["backlog.score.page_depth"]

    def test_a_score_backlog_reports_the_new_block_and_latest(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {
                "mode": "backlog",
                "latest": {"months_per_cycle": 2},
                "backlog": {"sort_order": "criticscore", "new": {"oldest_month": 202001}},
            }
        )
        assert inert_key_names(discovery) == ["latest.months_per_cycle", "backlog.new.oldest_month"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gamarr.discovery'`

- [ ] **Step 3: Implement the module**

Create `src/gamarr/discovery.py`:

```python
"""Resolve the discovery config into the scan plan one cycle runs.

The plan is the single answer to "what will this cycle read?", so the pipeline
never asks the config which mode it is in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from loguru import logger
from pydantic import BaseModel

from gamarr.config import DiscoveryConfig

SCAN_ORDER = Literal["new", "criticscore", "userscore"]
DISCOVERY_MODE = Literal["latest", "backlog"]


@dataclass(frozen=True)
class ScanPlan:
    """What one discovery cycle will read.

    Attributes:
        mode: The selected discovery mode.
        scan_order: The browse order the cycle reads.
        date_mode: True when a month window bounds the cycle instead of a page count.
        floor_month: The window floor as ``YYYYMM``, ``0`` when there is none.
        pace_months: Months one cycle reads back, ``0`` for no pacing.
        page_depth: Total page budget, ``0`` for none.
        pages_per_cycle: Per-cycle page pace, ``0`` for unlimited.
    """

    mode: DISCOVERY_MODE
    scan_order: SCAN_ORDER
    date_mode: bool
    floor_month: int
    pace_months: int
    page_depth: int
    pages_per_cycle: int


def build_scan_plan(discovery: DiscoveryConfig) -> ScanPlan:
    """Return the plan for the configured mode and sort order.

    Args:
        discovery: The validated discovery config.

    Returns:
        The plan the cycle runs.
    """
    if discovery.mode == "latest":
        latest = discovery.latest
        return ScanPlan(
            mode="latest",
            scan_order="new",
            date_mode=True,
            floor_month=latest.oldest_month,
            pace_months=latest.months_per_cycle,
            page_depth=0,
            pages_per_cycle=0,
        )
    backlog = discovery.backlog
    if backlog.sort_order == "new":
        return ScanPlan(
            mode="backlog",
            scan_order="new",
            date_mode=True,
            floor_month=backlog.new.oldest_month,
            pace_months=backlog.new.months_per_cycle,
            page_depth=0,
            pages_per_cycle=0,
        )
    return ScanPlan(
        mode="backlog",
        scan_order=backlog.sort_order,
        date_mode=False,
        floor_month=0,
        pace_months=0,
        page_depth=backlog.score.page_depth,
        pages_per_cycle=backlog.score.pages_per_cycle,
    )


def _leaf_keys(model: BaseModel, prefix: str) -> list[str]:
    """Return the explicitly configured keys of *model*, recursing into blocks."""
    names: list[str] = []
    for key in sorted(model.model_fields_set):
        value = getattr(model, key)
        if isinstance(value, BaseModel):
            names.extend(_leaf_keys(value, f"{prefix}{key}."))
        else:
            names.append(f"{prefix}{key}")
    return names


def inert_key_names(discovery: DiscoveryConfig) -> list[str]:
    """Return the configured keys the selected mode and sort do not read.

    Args:
        discovery: The validated discovery config.

    Returns:
        Dotted key paths, in a stable order.
    """
    if discovery.mode == "latest":
        return _leaf_keys(discovery.backlog, "backlog.")
    names = _leaf_keys(discovery.latest, "latest.")
    backlog = discovery.backlog
    block = "score" if backlog.sort_order == "new" else "new"
    names.extend(_leaf_keys(getattr(backlog, block), f"backlog.{block}."))
    return names


def report_inert_keys(discovery: DiscoveryConfig) -> None:
    """Log one INFO line per configured key the active blocks do not read.

    Args:
        discovery: The validated discovery config.
    """
    for name in inert_key_names(discovery):
        logger.info("Config: discovery.{} is not read by mode '{}' — ignored", name, discovery.mode)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_discovery.py -v`
Expected: PASS

---

### Task 4: `_migrate_discovery_blocks`

**Files:**

- Modify: `src/gamarr/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
class TestDiscoveryBlockMigration:
    """Legacy flat keys move into the per-mode blocks (ADR 0001, 0007)."""

    def _migrate(self, platform: dict[str, Any], **metacritic: Any) -> dict[str, Any]:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {"metacritic": {"platform_overrides": {"pc": platform}, **metacritic}}
        }
        _migrate_config(raw)
        return raw

    def test_latest_floor_and_pace_move_into_the_latest_block(self) -> None:
        raw = self._migrate(
            {"mode": "latest", "max_date": 202601, "max_cycle_months": 1, "sort_order": "new"}
        )
        discovery = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]
        assert discovery["mode"] == "latest"
        assert discovery["latest"] == {"oldest_month": 202601, "months_per_cycle": 1}
        assert "max_date" not in raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]

    def test_latest_page_depth_converts_to_a_month_pace(self) -> None:
        raw = self._migrate({"mode": "latest", "max_pages": 1000, "sort_order": "new"})
        latest = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["latest"]
        assert latest["months_per_cycle"] == 42

    def test_latest_drops_the_page_keys_it_never_read(self) -> None:
        raw = self._migrate(
            {"mode": "latest", "max_date": 202601, "max_cycle_pages": 16, "max_pages": 1000}
        )
        latest = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["latest"]
        assert latest == {"oldest_month": 202601}
        assert "max_pages" not in raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]

    def test_backlog_new_page_budget_becomes_a_floor(self) -> None:
        from gamarr.utils import current_year_month, subtract_months

        raw = self._migrate({"mode": "backlog", "max_pages": 48, "sort_order": "new"})
        new_block = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]["new"]
        assert new_block["oldest_month"] == subtract_months(current_year_month(), 2)

    def test_backlog_score_keeps_the_page_keys(self) -> None:
        raw = self._migrate(
            {"mode": "backlog", "max_pages": 40, "max_cycle_pages": 5, "sort_order": "criticscore"}
        )
        backlog = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["backlog"]
        assert backlog["sort_order"] == "criticscore"
        assert backlog["score"] == {"page_depth": 40, "pages_per_cycle": 5}

    def test_sort_order_feeds_the_shared_pending_order(self) -> None:
        raw = self._migrate({"mode": "backlog", "sort_order": "userscore"})
        metacritic = raw["review_sites"]["metacritic"]
        assert metacritic["pending_order"] == "userscore"

    def test_a_latest_config_does_not_write_a_backlog_sort_order(self) -> None:
        raw = self._migrate({"mode": "latest", "sort_order": "new"})
        backlog = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"].get("backlog", {})
        assert "sort_order" not in backlog

    def test_an_existing_block_wins_over_the_legacy_key(self) -> None:
        raw = self._migrate({"max_date": 202001, "discovery": {"latest": {"oldest_month": 202501}}})
        latest = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]["discovery"]["latest"]
        assert latest["oldest_month"] == 202501

    def test_a_migrated_file_loads_and_reloads_identically(self, tmp_path: Path) -> None:
        path = tmp_path / "gamarr.yml"
        path.write_text(
            "review_sites:\n"
            "  metacritic:\n"
            "    platform_overrides:\n"
            "      pc:\n"
            "        mode: latest\n"
            "        max_date: 202601\n"
            "        max_cycle_months: 1\n"
            "        max_pages: 1000\n"
            "        max_cycle_pages: 16\n"
            "        sort_order: new\n",
            encoding="utf-8",
        )
        first = load_config(path)
        second = load_config(path)
        assert first.review_sites.metacritic.platform_overrides["pc"].discovery.model_dump() == (
            second.review_sites.metacritic.platform_overrides["pc"].discovery.model_dump()
        )
        assert "max_pages" not in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py::TestDiscoveryBlockMigration -v`
Expected: FAIL with `KeyError: 'discovery'`

- [ ] **Step 3: Implement the migration**

In `src/gamarr/config.py`, add these imports at the top:

```python
from gamarr.utils import (
    EARLIEST_BROWSE_YEAR,
    current_year_month,
    is_valid_year_month,
    pages_to_months,
    subtract_months,
)
```

Add the migration helpers and step above `_migrate_config`:

```python
_DISCOVERY_LEGACY_KEYS = ("mode", "max_pages", "max_cycle_pages", "max_date", "max_cycle_months", "sort_order")


def _as_page_count(value: Any) -> int | None:
    """Return *value* as a non-negative int, or ``None`` when it is not usable."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _place(dest: dict[str, Any], key: str, value: Any, legacy: str) -> None:
    """Set *dest[key]* unless it already holds a value, reporting the loser."""
    if key in dest:
        logger.info("Config: legacy '{}' ignored — '{}' is already set", legacy, key)
        return
    dest[key] = value


def _migrate_platform_discovery(
    plat: dict[str, Any],
    metacritic: dict[str, Any],
    platform_key: str,
) -> None:
    """Move one platform override's flat discovery keys into blocks."""
    mode = plat.pop("mode", "backlog")
    if mode not in ("latest", "backlog"):
        mode = "backlog"
    sort_order = plat.pop("sort_order", "new")
    if sort_order not in ("new", "criticscore", "userscore"):
        sort_order = "new"
    max_date = _as_page_count(plat.pop("max_date", None))
    months = _as_page_count(plat.pop("max_cycle_months", None))
    pages = _as_page_count(plat.pop("max_pages", None))
    cycle_pages = _as_page_count(plat.pop("max_cycle_pages", None))

    discovery = plat.setdefault("discovery", {})
    discovery["mode"] = mode
    if metacritic.get("pending_order") is None:
        metacritic["pending_order"] = sort_order

    if mode == "latest":
        latest = discovery.setdefault("latest", {})
        if max_date is not None:
            _place(latest, "oldest_month", max_date, "max_date")
        if months is not None:
            _place(latest, "months_per_cycle", months, "max_cycle_months")
        if pages is not None and latest.get("months_per_cycle") in (None, 0):
            pace = pages_to_months(pages)
            latest["months_per_cycle"] = pace
            logger.info(
                "Config: converted max_pages {} to months_per_cycle {} for platform '{}' (approximate)",
                pages,
                pace,
                platform_key,
            )
        elif pages is not None:
            logger.info("Config: max_pages {} is not read in latest mode for platform '{}'", pages, platform_key)
        if cycle_pages is not None:
            logger.info(
                "Config: max_cycle_pages {} is not read in latest mode for platform '{}'",
                cycle_pages,
                platform_key,
            )
        return

    backlog = discovery.setdefault("backlog", {})
    backlog["sort_order"] = sort_order
    new_block = backlog.setdefault("new", {})
    if max_date is not None:
        _place(new_block, "oldest_month", max_date, "max_date")
    if months is not None:
        _place(new_block, "months_per_cycle", months, "max_cycle_months")
    if sort_order == "new":
        if pages is not None and new_block.get("oldest_month") in (None, 0):
            floor = subtract_months(current_year_month(), pages_to_months(pages))
            new_block["oldest_month"] = floor
            logger.info(
                "Config: converted max_pages {} to oldest_month {} for platform '{}' (approximate)",
                pages,
                floor,
                platform_key,
            )
        elif pages is not None:
            logger.info("Config: max_pages {} ignored — oldest_month already set for platform '{}'", pages, platform_key)
        if cycle_pages is not None and new_block.get("months_per_cycle") in (None, 0):
            pace = pages_to_months(cycle_pages)
            new_block["months_per_cycle"] = pace
            logger.info(
                "Config: converted max_cycle_pages {} to months_per_cycle {} for platform '{}' (approximate)",
                cycle_pages,
                pace,
                platform_key,
            )
        elif cycle_pages is not None:
            logger.info("Config: max_cycle_pages {} ignored — months_per_cycle already set for platform '{}'",
                        cycle_pages, platform_key)
        return

    score = backlog.setdefault("score", {})
    if pages is not None:
        _place(score, "page_depth", pages, "max_pages")
    if cycle_pages is not None:
        _place(score, "pages_per_cycle", cycle_pages, "max_cycle_pages")


def _migrate_discovery_blocks(raw: dict[str, Any]) -> bool:
    """Move the flat discovery keys into per-mode blocks.

    Args:
        raw: The raw config, mutated in place.

    Returns:
        True when at least one platform held legacy discovery keys.
    """
    review_sites = raw.get("review_sites")
    if not isinstance(review_sites, dict):
        return False
    metacritic = review_sites.get("metacritic")
    if not isinstance(metacritic, dict):
        return False
    overrides = metacritic.get("platform_overrides")
    if not isinstance(overrides, dict):
        return False
    changed = False
    for platform_key, plat in overrides.items():
        if not isinstance(plat, dict):
            continue
        if not any(key in plat for key in _DISCOVERY_LEGACY_KEYS):
            continue
        _migrate_platform_discovery(plat, metacritic, platform_key)
        changed = True
    return changed
```

Register it at the end of the `_migrations` list in `_migrate_config`, after
`_migrate_sort_order_critic_names`:

```python
            _migrate_sort_order_critic_names,
            _migrate_discovery_blocks,
```

Bump `_CONFIG_VERSION = "1.59.0"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS

---

### Task 5: `_migrate_source_matching_days`

**Files:**

- Modify: `src/gamarr/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
class TestSourceMatchingDaysMigration:
    """Three keys collapse into one, overridable per source (ADR 0004)."""

    def _migrate(self, raw: dict[str, Any]) -> dict[str, Any]:
        from gamarr.config import _migrate_config

        _migrate_config(raw)
        return raw

    def test_a_source_key_is_renamed(self) -> None:
        raw = self._migrate({"download_sites": [{"fitgirl": {"max_queue_days": 90}}]})
        assert raw["download_sites"][0]["fitgirl"]["source_matching_days"] == 90
        assert "max_queue_days" not in raw["download_sites"][0]["fitgirl"]

    def test_the_metacritic_fallback_is_hoisted(self) -> None:
        raw = self._migrate(
            {
                "review_sites": {"metacritic": {"max_queue_days": 30, "platform_overrides": {"pc": {}}}},
                "download_sites": [{"fitgirl": {"enabled": True}}],
            }
        )
        assert raw["source_matching_days"] == 30
        assert "max_queue_days" not in raw["review_sites"]["metacritic"]

    def test_the_existing_top_level_value_wins(self) -> None:
        raw = self._migrate(
            {
                "source_matching_days": 12,
                "review_sites": {"metacritic": {"max_queue_days": 30, "platform_overrides": {"pc": {}}}},
            }
        )
        assert raw["source_matching_days"] == 12

    def test_a_renamed_source_key_wins(self) -> None:
        raw = self._migrate(
            {"download_sites": [{"fitgirl": {"max_queue_days": 90, "source_matching_days": 15}}]}
        )
        assert raw["download_sites"][0]["fitgirl"]["source_matching_days"] == 15
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py::TestSourceMatchingDaysMigration -v`
Expected: FAIL — `max_queue_days` still present / `source_matching_days` missing

- [ ] **Step 3: Implement the migration**

Add above `_migrate_config`:

```python
def _rename_source_window(settings: dict[str, Any], label: str) -> bool:
    """Rename one source entry's ``max_queue_days`` in place."""
    if "max_queue_days" not in settings:
        return False
    if "source_matching_days" in settings:
        settings.pop("max_queue_days")
        logger.info("Config: '{}' max_queue_days ignored — source_matching_days already set", label)
    else:
        settings["source_matching_days"] = settings.pop("max_queue_days")
        logger.info("Config: renamed '{}' max_queue_days to source_matching_days", label)
    return True


def _migrate_source_matching_days(raw: dict[str, Any]) -> bool:
    """Collapse the three queue-window keys into one (ADR 0004).

    Args:
        raw: The raw config, mutated in place.

    Returns:
        True when any legacy key was found.
    """
    changed = False
    entries = raw.get("download_sites")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if "name" in entry:
                # Flat legacy form: {"name": "fitgirl", "max_queue_days": 60}
                changed |= _rename_source_window(entry, str(entry.get("name")))
                continue
            for name, settings in entry.items():
                if isinstance(settings, dict):
                    changed |= _rename_source_window(settings, str(name))
    review_sites = raw.get("review_sites")
    metacritic = review_sites.get("metacritic") if isinstance(review_sites, dict) else None
    if isinstance(metacritic, dict) and "max_queue_days" in metacritic:
        fallback = metacritic.pop("max_queue_days")
        if "source_matching_days" in raw:
            logger.info("Config: legacy metacritic max_queue_days ignored — source_matching_days already set")
        else:
            raw["source_matching_days"] = fallback
            logger.info("Config: hoisted metacritic max_queue_days to top-level source_matching_days")
        changed = True
    return changed
```

Register it as the **last** entry of `_migrations`, after `_migrate_discovery_blocks`, so the keyed
source list exists first:

```python
            _migrate_discovery_blocks,
            _migrate_source_matching_days,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS

---

### Task 6: Pipeline takes the plan

**Files:**

- Modify: `src/gamarr/pipeline.py:318-430`
- Test: `tests/unit/test_pipeline.py`

This task adds the new surface **alongside** the legacy one. Removing the legacy fields here would
break the module, because `_run_discovery_phases` still reads them until Task 7 and the scheduler still
passes them until Task 12. Keep every legacy field and keyword; Task 10 deletes the shim.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_pipeline.py`:

```python
class TestAcquisitionConfigCarriesThePlan:
    """The pipeline reads a resolved plan, not the mode matrix (ADR 0001)."""

    def test_the_default_plan_is_latest_one_month(self) -> None:
        from gamarr.pipeline import AcquisitionConfig

        cfg = AcquisitionConfig(min_criticscore=75, min_criticscore_reviews=10, min_user_score=7.5, min_user_reviews=10)
        assert cfg.plan.mode == "latest"
        assert cfg.plan.pace_months == 1
        assert cfg.plan.date_mode is True
        assert cfg.pending_order == "new"
        assert cfg.source_matching_days == 60

    def test_a_backlog_score_plan_keeps_the_page_keys(self) -> None:
        from gamarr.config import DiscoveryConfig
        from gamarr.discovery import build_scan_plan
        from gamarr.pipeline import AcquisitionConfig

        plan = build_scan_plan(
            DiscoveryConfig.model_validate(
                {"mode": "backlog", "backlog": {"sort_order": "criticscore", "score": {"page_depth": 40}}}
            )
        )
        cfg = AcquisitionConfig(
            min_criticscore=75,
            min_criticscore_reviews=10,
            min_user_score=7.5,
            min_user_reviews=10,
            plan=plan,
        )
        assert cfg.plan.page_depth == 40
        assert cfg.plan.date_mode is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_pipeline.py::TestAcquisitionConfigCarriesThePlan -v`
Expected: FAIL with `TypeError: AcquisitionConfig.__init__() missing ... 'plan'` / `AttributeError: plan`

- [ ] **Step 3: Replace the dataclass fields and the facade arguments**

In `src/gamarr/pipeline.py`, add imports:

```python
from gamarr.config import DiscoveryConfig
from gamarr.discovery import SCAN_ORDER, ScanPlan, build_scan_plan, report_inert_keys
```

Replace the discovery block of `AcquisitionConfig` (the fields `max_queue_days` through `sort_order`)
with:

```python
    plan: ScanPlan = field(default_factory=lambda: build_scan_plan(DiscoveryConfig()))
    pending_order: SCAN_ORDER = "new"
    source_matching_days: int = 60
```

Keep `fitgirl_max_queue_days` for now (renamed in Task 10). Add `field` to the dataclasses import if it
is not there.

In `run_acquisition`, replace these keyword parameters:

```python
    max_queue_days: int = 30,
    max_pages: int | None = None,
    max_cycle_pages: int | None = None,
    max_date: int = 0,
    max_cycle_months: int = 0,
    mode: Literal["latest", "backlog"] = "backlog",
```

with:

```python
    discovery: DiscoveryConfig | None = None,
    pending_order: SCAN_ORDER = "new",
    source_matching_days: int = 60,
```

and replace the `sort_order: Literal[...] = "new",` parameter and `age_recheck_weeks: int | None = None,`
with nothing (both are removed; `age_recheck_weeks` is deleted in Task 9).

Replace the body's normalise block:

```python
    mode = _normalise_mode(mode)
    max_date = _normalise_max_date(max_date)
    max_cycle_months = _normalise_cycle_months(max_cycle_months)
    date_driven = _date_driven("new" if mode == "latest" else sort_order, mode == "latest", max_date, max_cycle_months)
    max_pages = _normalise_latest_depth(max_pages, mode, date_driven=date_driven)
```

with:

```python
    discovery = discovery or DiscoveryConfig()
    plan = build_scan_plan(discovery)
    report_inert_keys(discovery)
```

and replace the matching `AcquisitionConfig(...)` arguments with:

```python
        plan=plan,
        pending_order=pending_order,
        source_matching_days=source_matching_days,
```

- [ ] **Step 4: Point the tests at the new arguments**

Mechanical updates in `tests/unit/test_pipeline.py`, all of which currently pass the removed keywords:

```bash
cd /data/gamarr
rg -n "mode=|max_pages=|max_date=|max_cycle_months=|max_cycle_pages=|sort_order=" tests/unit/test_pipeline.py | wc -l
```

Each `run_acquisition(...)` call and each `AcquisitionConfig(...)` construction that passes one of
`mode`, `max_pages`, `max_date`, `max_cycle_months`, `max_cycle_pages` must now pass
`discovery=DiscoveryConfig.model_validate({...})` instead, for example:

```python
        run_acquisition(
            db_path=str(tmp_path / "t.db"),
            discovery=DiscoveryConfig.model_validate(
                {"mode": "backlog", "backlog": {"sort_order": "new", "new": {"oldest_month": 202001}}}
            ),
        )
```

`sort_order=` on `db.get_pending(...)` calls stays unchanged (see refinement 2); only the
`run_acquisition`/`AcquisitionConfig` keyword changes.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_pipeline.py -q`
Expected: PASS. Failures that remain are calls still passing a removed keyword; fix each the same way.

---

### Task 7: Read the plan inside the discovery phases

**Files:**

- Modify: `src/gamarr/pipeline.py` (`_run_discovery_phases`, roughly lines 500-1230)
- Test: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_pipeline.py`:

```python
class TestPlanDrivenScan:
    """The plan decides the scan shape; no key can be inert (ADR 0003, 0005)."""

    def _plan(self, payload: dict[str, Any]) -> Any:
        from gamarr.config import DiscoveryConfig
        from gamarr.discovery import build_scan_plan

        return build_scan_plan(DiscoveryConfig.model_validate(payload))

    def test_latest_with_a_floor_and_pace_reads_one_month(self) -> None:
        plan = self._plan({"mode": "latest", "latest": {"oldest_month": 202601, "months_per_cycle": 1}})
        assert (plan.date_mode, plan.floor_month, plan.pace_months) == (True, 202601, 1)

    def test_latest_with_a_zero_pace_reads_to_the_floor(self) -> None:
        plan = self._plan({"mode": "latest", "latest": {"months_per_cycle": 0}})
        assert plan.pace_months == 0
        assert plan.date_mode is True

    def test_a_score_backlog_has_no_date_bound(self) -> None:
        plan = self._plan({"mode": "backlog", "backlog": {"sort_order": "userscore"}})
        assert plan.date_mode is False
        assert plan.floor_month == 0
        assert plan.page_depth == 500

    def test_a_zero_page_depth_sweeps_from_the_earliest_year(self) -> None:
        from gamarr.pipeline import _year_range_start

        plan = self._plan({"mode": "backlog", "backlog": {"sort_order": "criticscore", "score": {"page_depth": 0}}})
        assert _year_range_start(plan, scan_year_anchor=2026) == 1958

    def test_a_page_budget_uses_the_pages_per_month_constant(self) -> None:
        from gamarr.pipeline import _year_range_start

        plan = self._plan({"mode": "backlog", "backlog": {"sort_order": "criticscore", "score": {"page_depth": 500}}})
        assert _year_range_start(plan, scan_year_anchor=2026) == 2024
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py::TestPlanDrivenScan -v`
Expected: FAIL with `ImportError: cannot import name '_year_range_start'`

- [ ] **Step 3: Add the year-range helper and substitute the plan reads**

Add next to the other discovery helpers in `pipeline.py`:

```python
def _year_range_start(plan: ScanPlan, scan_year_anchor: int) -> int:
    """Return the oldest year a page-budgeted backlog cycle may read.

    A page budget is converted with the same 24-pages-per-month constant the
    migrations use, so the derived reach matches the configured budget instead of
    the old ``max_pages / 52`` figure that overstated it fivefold.

    Args:
        plan: The resolved scan plan.
        scan_year_anchor: The current year.

    Returns:
        The oldest year to read.  A zero budget means the whole catalogue.
    """
    if plan.page_depth <= 0:
        return EARLIEST_BROWSE_YEAR
    return max(EARLIEST_BROWSE_YEAR, scan_year_anchor - math.ceil(pages_to_months(plan.page_depth) / 12))
```

Import `pages_to_months` from `gamarr.utils`.

Substitute these reads inside `_run_discovery_phases` (line numbers are the pre-change anchors):

| Anchor | Old | New |
| --- | --- | --- |
| 503 | `latest_mode = cfg.mode == "latest"` | `latest_mode = cfg.plan.mode == "latest"` |
| 504 | `scan_order = "new" if latest_mode else cfg.sort_order` | `scan_order = cfg.plan.scan_order` |
| 505-509 | the `sort_order` warn-and-coerce branch | delete |
| 514-515 | `date_mode = _date_driven(...)`; `floor_month = cfg.max_date if date_mode else 0` | `date_mode = cfg.plan.date_mode`; `floor_month = cfg.plan.floor_month` |
| 571-605 | six inert-key `if` branches | delete (reported once in `run_acquisition`) |
| 614, 616, 623, 630-640, 662 | `cfg.max_cycle_months` | `cfg.plan.pace_months` |
| 670 | `years_back = max(0, math.ceil((cfg.max_pages if cfg.max_pages else 500) / 52))` | see below |
| 678 | `max_pages_cfg = cfg.max_pages if cfg.max_pages else 0` | `max_pages_cfg = cfg.plan.page_depth` |
| 705 | `cycle_allowance = cfg.max_cycle_pages if ...` | `cycle_allowance = cfg.plan.pages_per_cycle` |
| 732, 761, 766, 777 | `cfg.max_cycle_months` | `cfg.plan.pace_months` |
| 1193 | `cfg.max_cycle_months` | `cfg.plan.pace_months` |
| 1212, 1215, 1219 | `cfg.max_pages` | `cfg.plan.page_depth` |

The year-range branch at 670 becomes:

```python
                else:
                    cutoff_year = _year_range_start(cfg.plan, scan_year_anchor)
```

The mode logging at 517-526 becomes:

```python
        if latest_mode:
            logger.info(
                "Discovery mode: latest (floor {}, pace {})",
                floor_month or "none",
                f"{cfg.plan.pace_months} month(s)" if cfg.plan.pace_months else "unpaced",
            )
        else:
            logger.info(
                "Discovery mode: backlog (sort {}, floor {}, pace {})",
                scan_order,
                floor_month or "none",
                f"{cfg.plan.pace_months} month(s)" if date_mode and cfg.plan.pace_months else "unpaced",
            )
```

Delete the latest depth branch (pre-change lines 778-787):

```python
                    elif latest_mode:
                        # Depth-driven fallback: leave a page for the years below
                        ...
                        month_span_left = 0
```

`latest` now always takes the `date_mode` branch above it, so the `elif latest_mode and date_mode:`
guard collapses to `elif latest_mode:` if the linter flags the redundant condition. Leave the
condition as written if it is still reachable through `date_mode` being False (it is not, after this
change: `build_scan_plan` sets `date_mode=True` for every latest plan).

Keep the legacy `AcquisitionConfig` fields and `run_acquisition` keywords in place: the body no longer
reads them, the scheduler still passes them, and Task 10 removes both ends together.

- [ ] **Step 4: Convert or delete the tests that encode the removed behaviour**

These test classes assert behaviour this design deletes. Convert each to the new semantics where the
scenario still exists, and delete it where the behaviour is gone:

| Class | Old behaviour | Action |
| --- | --- | --- |
| `TestLatestMode` | depth-driven latest (`mode="latest"`, `max_pages=N`, no date keys) | convert to a month pace via `discovery={"mode": "latest", "latest": {"months_per_cycle": N}}`; delete the cases asserting a page depth |
| `TestLatestModeReachesTheFloorYear` | latest depth reaching a lower year | delete: latest has no depth |
| `TestMaxCycleWeeks` | `max_cycle_pages` allowance in backlog `new` | convert to `backlog.new.months_per_cycle` |
| `TestYearDrainOrder` | page-budget drain in backlog `new` | convert to the floored date sweep, or delete if the ordering was a page-budget artefact |
| `TestBacklogAdvancing` | `max_pages` budget with cursors in `new` | convert to `oldest_month` plus cursors |
| `TestBacklogLatestMode` | both modes on page budgets | delete the page-depth cases, keep the floor ones |
| `TestScanWindowAdvancing` | window derived from `max_pages / 52` | convert to `oldest_month` and `_year_range_start` |

Every deletion must be justified in the report by naming the behaviour the design removed (ADR 0003 or
ADR 0005). A deleted test is only acceptable when `build_scan_plan` cannot express its scenario.
Run: `uv run pytest tests/unit/test_pipeline.py -q`
Expected: PASS

Simplify the year-loop stop at 728-730:

```python
                if latest_mode and not date_mode:
                    if pages_this_cycle >= max_pages_cfg:
                        break
```

to:

```python
                if date_mode:
                    ...
```

i.e. delete the `latest_mode and not date_mode` arm, since latest is always date mode.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_pipeline.py::TestPlanDrivenScan -v`
Then: `uv run pytest tests/unit/test_pipeline.py -q`
Expected: PASS

---

### Task 8: Delete the dead mode-resolution helpers

**Files:**

- Modify: `src/gamarr/pipeline.py:116-290`
- Test: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Delete the tests first**

Remove from `tests/unit/test_pipeline.py` every test that imports or exercises:

- `_normalise_mode`
- `_normalise_max_date`
- `_normalise_cycle_months`
- `_date_driven`
- `_normalise_latest_depth`
- `_latest_call_depth`
- `_DEFAULT_MAX_PAGES`

Run: `uv run pytest tests/unit/test_pipeline.py -q`
Expected: FAIL with `ImportError` for the removed names — this is the RED step for the deletion.

- [ ] **Step 2: Delete the helpers**

Remove from `src/gamarr/pipeline.py`: `_normalise_mode`, `_normalise_max_date`,
`_normalise_cycle_months`, `_date_driven`, `_normalise_latest_depth`, `_latest_call_depth`, and the
`_DEFAULT_MAX_PAGES` constant. Keep `_floorless_latest_bound` and `_FLOORLESS_LATEST_SLACK_YEARS`,
which still bound a floorless latest cycle.

Also delete the four `_normalise_*` calls in `run_acquisition` (they fed only the legacy fields the body
no longer reads). The legacy keywords themselves stay accepted until Task 10.

- [ ] **Step 3: Verify nothing references them**

Run:

```bash
cd /data/gamarr
rg -n -e "_latest_call_depth" -e "_normalise_latest_depth" -e "_date_driven" \
  -e "_normalise_mode" -e "_normalise_max_date" -e "_normalise_cycle_months" \
  -e "_DEFAULT_MAX_PAGES" src tests
```

Expected: no output.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_pipeline.py -q`
Expected: PASS

---

### Task 9: `pending_order` for the queue, and delete the aged-games sweep

**Files:**

- Modify: `src/gamarr/pipeline.py` (pending reads, `_process_aged_games` call at ~1183, helpers at ~900-1200)
- Test: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_pipeline.py`:

```python
class TestPendingOrderIsIndependent:
    """The queue order comes from pending_order, not from the browse order (ADR 0006)."""

    def test_a_discovery_date_order_does_not_change_a_score_queue(self) -> None:
        from gamarr.config import DiscoveryConfig
        from gamarr.discovery import build_scan_plan
        from gamarr.pipeline import AcquisitionConfig

        cfg = AcquisitionConfig(
            min_criticscore=75,
            min_criticscore_reviews=10,
            min_user_score=7.5,
            min_user_reviews=10,
            plan=build_scan_plan(DiscoveryConfig.model_validate({"mode": "latest"})),
            pending_order="userscore",
        )
        assert cfg.plan.scan_order == "new"
        assert cfg.pending_order == "userscore"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_pipeline.py::TestPendingOrderIsIndependent -v`
Expected: FAIL with `TypeError: AcquisitionConfig.__init__() got an unexpected keyword argument 'pending_order'`
if Task 6 has not landed; otherwise PASS. If it passes, keep it as the regression pin and continue.

- [ ] **Step 3: Substitute the queue order and delete the aged sweep**

Every queue read in `pipeline.py` currently passes `sort_order=cfg.sort_order`. Replace each with
`sort_order=cfg.pending_order`:

- `db.get_pending(platform=platform, sort_order=cfg.sort_order)` (line ~1034)
- the forwarded `sort_order=cfg.sort_order` at ~1073 and ~1148
- `db.get_pending(platform=platform, sort_order=cfg.sort_order)` inside `_process_aged_games` (~2042)
  disappears with the function below.

Delete `_process_aged_games`, `_should_age_game`, and the call
`_process_aged_games(db, cfg, platform, cancel_event=cancel_event)` at ~1183 with its comment block.
Delete every test that references `_process_aged_games` or `_should_age_game`.

Verify:

```bash
cd /data/gamarr
rg -n "_process_aged_games|_should_age_game|age_recheck_weeks|cfg\.sort_order" src tests
```

Expected: no output.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_pipeline.py -q`
Expected: PASS

---

### Task 10: Rename the source-matching window through the pipeline

**Files:**

- Modify: `src/gamarr/pipeline.py`, `src/gamarr/database.py`
- Test: `tests/unit/test_pipeline.py`, `tests/unit/test_database.py`

- [ ] **Step 1: Rename across the modules**

```bash
cd /data/gamarr
sed -i 's/\bfitgirl_max_queue_days\b/source_matching_days/g' \
  src/gamarr/pipeline.py src/gamarr/scheduler.py tests/unit/test_pipeline.py tests/unit/test_scheduler.py
sed -i 's/\bmax_queue_days\b/source_matching_days/g' src/gamarr/database.py tests/unit/test_database.py
```

Then fix the two places where the sed left a name that now collides:

- `src/gamarr/pipeline.py`, `_make_fitgirl_entry`: `entry.source_matching_days = source_matching_days`
  (the fallback entry carries the same name as the pipeline parameter).
- `src/gamarr/database.py`, `update_pending_expiry(self, slug: str, source_matching_days: int)` — the
  docstring and `_INDEFINITE_DAYS` comment now say `source_matching_days`.

- [ ] **Step 2: Verify no legacy name survives**

```bash
cd /data/gamarr
rg -n "fitgirl_max_queue_days|max_queue_days" src tests README.md
```

Expected: no output in `src` and `tests`. README is rewritten in Task 13; if it still matches, that is
expected and is fixed there.

- [ ] **Step 3: Remove the Task 6 compatibility shim**

Delete the legacy discovery fields from `AcquisitionConfig` (`max_pages`, `max_cycle_pages`,
`max_date`, `max_cycle_months`, `mode`, `sort_order`, `max_queue_days`, `age_recheck_weeks`) and the
matching `run_acquisition` keywords; the scheduler is updated in the same wave, so nothing passes them
any more. Keep `min_*`, `cache_*`, `enabled`, `fitgirl_cache_hours`, `reject_*` and the notification and
library keywords.

Verify:

```bash
cd /data/gamarr
rg -n "AcquisitionConfig\(" -A2 src tests | rg -n "max_pages|max_date|max_cycle_months|sort_order|age_recheck_weeks"
```

Expected: no output.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/unit/test_pipeline.py tests/unit/test_database.py -q`
Expected: PASS

---

### Task 11: Remove the legacy config fields

**Files:**

- Modify: `src/gamarr/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Delete the fields**

Remove from `MetacriticPlatformConfig`: `mode`, `max_queue_days`, `max_pages`, `max_cycle_pages`,
`max_date`, `max_cycle_months`, `sort_order`, and the `_validate_max_date` validator (its logic now
lives in `validate_oldest_month`).

Remove from `SourceConfigEntry`: `max_queue_days`.

- [ ] **Step 2: Update the tests that read them**

Replace, in `tests/unit/test_config.py`:

- `cfg.max_queue_days == 60` on `SourceConfigEntry` -> `cfg.source_matching_days == 60`
- `cfg.max_queue_days == 30` on `MetacriticPlatformConfig` -> delete (the fallback key is gone)
- any `MetacriticPlatformConfig(mode=...)`, `.max_pages`, `.max_date`, `.max_cycle_months`,
  `.sort_order`, `.max_cycle_pages` -> the equivalent `discovery.*` field, e.g.
  `MetacriticPlatformConfig().discovery.latest.months_per_cycle`

Search for leftovers:

```bash
cd /data/gamarr
rg -n -e "mc_cfg\.(max_pages|max_date|max_cycle_months|max_cycle_pages)" \
  -e "mc_cfg\.(mode|sort_order|max_queue_days)" \
  -e "MetacriticPlatformConfig\([^)]*(mode|max_pages|max_date)" src tests
```

Expected: no output.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: PASS

Note: `src/gamarr/scheduler.py` still reads `mc_cfg.max_pages` and friends until Task 12; if the
config test run imports the scheduler, that failure is expected here and is fixed next.

---

### Task 12: Scheduler resolves the window and forwards the plan

**Files:**

- Modify: `src/gamarr/scheduler.py:313-390`
- Test: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_scheduler.py`:

```python
class TestMatchingWindowResolution:
    """One window, resolved from sources then the top-level default (ADR 0004)."""

    def _config(self, **kwargs: Any) -> Any:
        from gamarr.config import Config

        return Config.model_validate(kwargs)

    def test_the_fitgirl_entry_wins(self) -> None:
        from gamarr.scheduler import _matching_window_days

        cfg = self._config(
            source_matching_days=10,
            download_sites=[{"fitgirl": {"source_matching_days": 90}}, {"freegog": {"source_matching_days": 20}}],
        )
        assert _matching_window_days(cfg) == 90

    def test_a_disabled_fitgirl_falls_through_to_the_next_source(self) -> None:
        from gamarr.scheduler import _matching_window_days

        cfg = self._config(
            source_matching_days=10,
            download_sites=[{"fitgirl": {"enabled": False, "source_matching_days": 90}}, {"freegog": {}}],
        )
        assert _matching_window_days(cfg) == 60

    def test_no_sources_falls_back_to_the_top_level_default(self) -> None:
        from gamarr.scheduler import _matching_window_days

        cfg = self._config(source_matching_days=10, download_sites=[])
        assert _matching_window_days(cfg) == 10

    def test_build_kwargs_forwards_the_plan_and_pending_order(self) -> None:
        from gamarr.config import Config
        from gamarr.scheduler import _build_kwargs

        cfg = Config.model_validate(
            {
                "review_sites": {
                    "metacritic": {
                        "pending_order": "userscore",
                        "platform_overrides": {"pc": {"discovery": {"mode": "latest"}}},
                    }
                }
            }
        )
        kwargs = _build_kwargs(cfg)
        assert kwargs["pending_order"] == "userscore"
        assert kwargs["discovery"].mode == "latest"
        assert "max_pages" not in kwargs
        assert "sort_order" not in kwargs
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_scheduler.py::TestMatchingWindowResolution -v`
Expected: FAIL with `AttributeError: source_matching_days` / `KeyError: 'discovery'`

- [ ] **Step 3: Rewrite the resolution**

Replace `_matching_window_days` in `src/gamarr/scheduler.py`:

```python
def _matching_window_days(config: Config) -> int:
    """Return the source-matching window applied to queued games.

    Args:
        config: The loaded configuration.

    Returns:
        An enabled FitGirl entry's window, else the first enabled source's own
        ``source_matching_days``, else the top-level default.
    """
    fitgirl_entry = _find_fitgirl_entry(config)
    if fitgirl_entry is not None and getattr(fitgirl_entry, "enabled", True):
        return int(fitgirl_entry.source_matching_days)
    for entry in config.download_sites:
        if getattr(entry, "enabled", True):
            return int(entry.source_matching_days)
    return int(config.source_matching_days)
```

In `_build_kwargs`, replace the forwarded discovery keys:

```python
        "max_pages": mc_cfg.max_pages,
        "max_cycle_pages": mc_cfg.max_cycle_pages,
        "max_date": mc_cfg.max_date,
        "max_cycle_months": mc_cfg.max_cycle_months,
        "mode": mc_cfg.mode,
```

with:

```python
        "discovery": mc_cfg.discovery,
```

replace `"sort_order": mc_cfg.sort_order,` with `"pending_order": mc_cfg.pending_order,`, delete
`"max_queue_days": mc_cfg.max_queue_days,` and `"age_recheck_weeks": ...,`, and replace
`"fitgirl_max_queue_days": _matching_window_days(config, mc_cfg),` with:

```python
        "source_matching_days": _matching_window_days(config),
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_scheduler.py -q`
Expected: PASS

---

### Task 13: README and its accuracy test

**Files:**

- Modify: `README.md`, `tests/unit/test_readme_accuracy.py`

- [ ] **Step 1: Replace the discovery rows**

In the `review_sites.metacritic.platform_overrides.<platform>` table, replace the rows `mode`,
`max_pages`, `max_cycle_pages`, `max_date`, `max_cycle_months`, `sort_order`, `max_queue_days` with:

<!-- markdownlint-disable MD013 -->
```markdown
| `discovery.mode` | How a cycle walks the release list. `"latest"` reads the newest end every cycle and is always newest-first; `"backlog"` sweeps backwards with per-year cursors. The block for the other mode is ignored, with one INFO line per key. | `"latest"` |
| `discovery.latest.oldest_month` | Oldest month a `latest` cycle may read, as `YYYYMM` (month `01`-`12`, year `1958` or later). `0` = no floor, so the cycle reads back `months_per_cycle` months from the newest release. A later month is clamped to the current one. | `0` |
| `discovery.latest.months_per_cycle` | How many months of releases one `latest` cycle reads back from its newest page. `0` = read all the way to the floor. Maximum `1200`. | `1` |
| `discovery.backlog.sort_order` | Which backlog block runs: `"new"` reads `backlog.new`, `"criticscore"` and `"userscore"` read `backlog.score`. | `"new"` |
| `discovery.backlog.new.oldest_month` | Oldest month a `backlog` sweep may read. `0` = no floor, so the sweep runs as far back as the listing goes. | `0` |
| `discovery.backlog.new.months_per_cycle` | How many months one `backlog` sweep cycle covers. `0` = the whole sweep in one cycle. Maximum `1200`. | `0` |
| `discovery.backlog.score.page_depth` | Total browse-page budget for a score-sorted sweep. `0` = no budget: the sweep reaches the earliest listing. A page is about a day of PC releases, so 24 pages is about one month. | `500` |
| `discovery.backlog.score.pages_per_cycle` | Pages per cycle for a score-sorted sweep. `0` = unlimited. | `0` |
| `pending_order` | Order the pending queue is processed in: `"new"`, `"criticscore"`, or `"userscore"`. Independent of how discovery reads. | `"new"` |
```
<!-- markdownlint-enable MD013 -->

Add a `source_matching_days` row to the top-level section's table:

```markdown
<!-- markdownlint-disable MD013 -->
| `source_matching_days` | How long a queued game waits for a matching repack before it leaves the queue. A download source may override it under the same name. `0` = never expires. | `60` |
```
<!-- markdownlint-enable MD013 -->

In the `download_sites` table, rename the `max_queue_days` row to `source_matching_days`.

- [ ] **Step 2: Rewrite the surrounding prose**

- Features bullet "Date-based backlog cutoff": replace with a bullet that states `latest` reads a
  month window and `backlog` sweeps a floor, and that a page is about a day of releases.
- "How it works" step 2 (the paragraph starting "Games are ordered by `sort_order`"): state that
  `discovery.latest` reads newest-first, `discovery.backlog.sort_order` picks the sweep order, and
  `pending_order` orders the queue.
- Delete every sentence about "date-driven latest", "max_pages is unused", "warned about and coerced",
  and the `sort_order`-changes-reprioritize-the-queue claim.
- The troubleshooting question **"Why does `max_queue_days` have two separate settings?"**: rewrite as
  "Why is there one `source_matching_days` and a per-source override?".
- The troubleshooting bullet "Increase `max_pages` to catch more recently released games": replace
  with "Raise `discovery.latest.months_per_cycle` to read further back each cycle".
- The flow diagram's `Within max_date / max_pages window?` node becomes
  `Within oldest_month floor?`.

- [ ] **Step 3: Repoint the accuracy tests**

In `tests/unit/test_readme_accuracy.py`, replace the bodies of `TestReadmeDateModeRows` with:

```python
class TestReadmeDiscoveryRows:
    """README must document the discovery blocks and their defaults."""

    def _readme_defaults(self) -> dict[str, str]:
        with open(README_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        block = _find_config_block(lines, "review_sites.metacritic.platform_overrides.<platform>")
        assert block is not None, "platform_overrides table missing from README"
        return _parse_readme_defaults(block)

    def test_mode_default_row(self) -> None:
        from gamarr.config import DiscoveryConfig

        discovery = DiscoveryConfig()
        assert self._readme_defaults().get("discovery.mode") == f'"{discovery.mode}"'

    def test_latest_pace_default_row(self) -> None:
        from gamarr.config import DiscoveryConfig

        discovery = DiscoveryConfig()
        assert self._readme_defaults().get("discovery.latest.months_per_cycle") == str(
            discovery.latest.months_per_cycle
        )

    def test_backlog_page_depth_default_row(self) -> None:
        from gamarr.config import DiscoveryConfig

        discovery = DiscoveryConfig()
        assert self._readme_defaults().get("discovery.backlog.score.page_depth") == str(
            discovery.backlog.score.page_depth
        )

    def test_no_legacy_discovery_rows_remain(self) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        for legacy in ("`max_date`", "`max_cycle_months`", "`max_cycle_pages`", "`max_queue_days`"):
            assert legacy not in readme, f"legacy key {legacy} still documented"
        for line in readme.splitlines():
            assert not line.startswith("| `max_pages`"), "the max_pages config row must be gone"

    def test_source_matching_days_is_documented_once(self) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        assert "`source_matching_days`" in readme
        assert "`max_queue_days`" not in readme
```

Fix the pre-existing `test_max_pages_default` and `test_sort_order_row_documents_three_values` tests by
deleting them: both assert rows this change removes.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_readme_accuracy.py -v`
Expected: PASS

Then run the markdown lint the repo configures:

```bash
cd /data/gamarr
markdownlint --config /tmp/mdlint.json README.md
```

If `/tmp/mdlint.json` does not exist, create it from `.markdownlint-cli2.jsonc`:

```bash
printf '{\n  "MD013": { "line_length": 120, "tables": false }\n}\n' > /tmp/mdlint.json
```

---

### Task 14: Full verification

- [ ] **Step 1: Run the whole suite with coverage**

Run: `uv run pytest --cov=gamarr --cov-fail-under=80 -q`
Expected: PASS, coverage at or above the previous 96.90%

- [ ] **Step 2: Lint and type-check**

Run: `uv run ruff check . && uv run ruff format . && uv run mypy .`
Expected: no findings, no reformat, `Success: no issues found in 40 source files`

- [ ] **Step 3: Prove no legacy key survives in code or docs**

```bash
cd /data/gamarr
rg -n "max_cycle_months|max_cycle_pages|fitgirl_max_queue_days|max_queue_days|age_recheck_weeks" src tests README.md
rg -n "cfg\.(max_pages|max_date|mode|sort_order|max_cycle_pages|max_cycle_months)" src
```

Expected: both commands print nothing. `pipeline.py` and `metacritic.py` keep a `max_pages` parameter
as the Metacritic client's per-call page limit; that is a different quantity and must survive.
`docs/agent/` is allowed to keep the old names, because it records the migration.

- [ ] **Step 4: Prove the live config still loads**

Run: `uv run python -c "from gamarr.config import load_config; c = load_config('configs/gamarr.yml'); print(c.review_sites.metacritic.platform_overrides['pc'].discovery)"`
Expected: the printed discovery block shows `mode='latest'`, `oldest_month=202601`,
`months_per_cycle=1`, and no page keys, with INFO lines naming the converted values.

- [ ] **Step 5: Confirm the migrated file round-trips**

Run: `git diff --stat configs/gamarr.yml`
Expected: the flat discovery keys are gone and the `discovery` block is present. This file is the
operator's live config; if it must not be rewritten in place, copy it to `/tmp/` first and run the
load against the copy.

---

## Self-review

**Spec coverage:** target shape (Task 2, 11), key mapping (Tasks 4, 5, 11), defaults (Task 2, 13),
validation (Task 2), migration rules 1-9 (Tasks 4, 5), `pages_to_months` and the 24 constant (Task 1,
7), pipeline scan plan and the six deleted warnings (Tasks 6, 7, 8), year-window constant (Task 7),
`pending_order` (Tasks 2, 9, 12), scheduler resolution (Task 12), README (Task 13), testing matrix
(Tasks 1-13), out-of-scope items left alone.

**Placeholder scan:** no "TBD"/"similar to Task N"; every code step carries the code. Step 11 of Task 6
and Step 3 of Task 9 use search-and-fix instructions for mechanical call-site updates, each with the
exact command and the expected empty output.

**Type consistency:** `ScanPlan` fields (`mode`, `scan_order`, `date_mode`, `floor_month`,
`pace_months`, `page_depth`, `pages_per_cycle`) are used with those names in Tasks 3, 6, 7 and 9.
`pages_to_months`, `PAGES_PER_MONTH`, `validate_oldest_month`, `_year_range_start`,
`_matching_window_days(config)`, `source_matching_days` and `pending_order` each have one spelling
throughout.
