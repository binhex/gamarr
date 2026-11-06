# Date-based backlog cutoff (`max_date`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a human-readable `max_date: YYYYMM` backlog cutoff plus a `max_cycle_months` pace for `sort_order: new`,
and rename the config key `cache_pages_hours` to `cache_hours`, without changing page-mode behaviour.

**Architecture:** `max_date` is a month floor enforced by the browse scan's existing (but never used) `cutoff_date`
early-stop; `max_cycle_months` is enforced client-side as a month span anchored on each call's first page, with the
pipeline sharing the allowance across years exactly as it shares `max_cycle_pages` today. Page mode (`max_pages` +
`max_cycle_pages`) is untouched, and no database schema or persisted frontier state is added. The cache rename rides
the existing key-presence migration mechanism.

**Tech Stack:** Python 3.12, pydantic v2, pytest, ruff, mypy, `uv`, loguru.

**Spec:** `docs/agent/specs/2026-11-06-max-date-backlog-cutoff-design.md` (approved; the planning refinements below are folded into the tasks).

---

## Scope check

The spec covers **one** subsystem: the Metacritic browse cutoff/pace and its config surface. Config, pipeline, client,
scheduler, and README change together for one behaviour. **No plan split needed.**

## File map

| File | Responsibility after this change |
| ------ | ---------------------------------- |
| `src/gamarr/utils.py` | Pure month arithmetic + parsing helpers (`subtract_months`, `month_of`, `year_month_to_date`) |
| `src/gamarr/config.py` | `max_date` / `max_cycle_months` fields + validation; `cache_hours` rename; cache-key migration |
| `src/gamarr/metacritic.py` | `max_month_span` pace parameter; `_recent_games_cutoff_reached` flag |
| `src/gamarr/pipeline.py` | Mode resolution (date vs page), date-mode browse loop, progress log, cache-param renames |
| `src/gamarr/scheduler.py` | `_build_kwargs` carries the new/renamed keys |
| `README.md` | Two new rows, two renamed rows, date-mode narrative, troubleshooting |
| `tests/unit/test_utils.py`, `test_config.py`, `test_metacritic.py`, `test_pipeline.py`, `test_scheduler.py`, `test_readme_accuracy.py` | Coverage for every behaviour above |

**Unchanged:** `src/gamarr/database.py`, `src/gamarr/sources/fitgirl.py`, `src/gamarr/sources/freegog.py` (their `cache_pages_hours` *constructor arguments* keep their names; only config keys and pipeline API are renamed).

## Naming contract (follow exactly; the tasks depend on it)

| Concept | Name | Where |
| --------- | ------ | ------- |
| Config key / field | `max_date: int` (0 = off, else `YYYYMM`) | `config.py`, `pipeline.py` |
| Config key / field | `max_cycle_months: int` (0 = no pace) | `config.py`, `pipeline.py` |
| Config key / field | `cache_hours: int` | `config.py`, `pipeline.py`, `scheduler.py` |
| Pipeline param | `cache_hours`, `fitgirl_cache_hours` | `run_acquisition`, `AcquisitionConfig` |
| Client param | `cache_pages_hours` (**unchanged**), `max_month_span` (new) | `metacritic.py`, source classes |
| Client flag | `_recent_games_cutoff_reached` | `metacritic.py` |
| Utils helpers | `subtract_months`, `month_of`, `year_month_to_date` | `utils.py` |
| Pipeline helper | `_month_span` (private) | `pipeline.py` |

## Planning refinements to the spec

- `scan_recent_games` gains `max_month_span` — the month pace cannot be a precomputed `cutoff_date` because the cycle
  origin month is only known after the first page is fetched.
- A single `_recent_games_cutoff_reached` flag still suffices: the client folds the pace boundary and the floor into
  one effective cutoff; the pipeline distinguishes floor from pace by comparing months covered against the span to the
  floor.
- `cache_pages_hours` is renamed in **config keys and pipeline parameters only**. `MetacriticClient` / `FitGirlSource`
  / `FreeGOGSource` constructor kwargs keep the name `cache_pages_hours`; the mapping points are called out explicitly
  in Task 5 so nobody "fixes" them by mistake.

---

### Task 1: Month helpers in `utils.py`

**Files:**

- Modify: `src/gamarr/utils.py` (add `import datetime`; append three helpers after `is_cancelled`)
- Test: `tests/unit/test_utils.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_utils.py`:

```python
class TestSubtractMonths:
    """Tests for subtract_months()."""

    def test_subtracts_within_year(self) -> None:
        from gamarr.utils import subtract_months

        assert subtract_months(202601, 2) == 202511

    def test_subtracts_across_year_boundary(self) -> None:
        from gamarr.utils import subtract_months

        assert subtract_months(202501, 1) == 202412
        assert subtract_months(202501, 13) == 202312

    def test_zero_and_negative_months_return_input(self) -> None:
        from gamarr.utils import subtract_months

        assert subtract_months(202501, 0) == 202501
        assert subtract_months(202501, -3) == 202501


class TestMonthOf:
    """Tests for month_of()."""

    def test_iso_date(self) -> None:
        from gamarr.utils import month_of

        assert month_of("2025-11-14") == 202511

    def test_uk_date(self) -> None:
        from gamarr.utils import month_of

        assert month_of("14-11-2025") == 202511

    def test_none_and_unparsable(self) -> None:
        from gamarr.utils import month_of

        assert month_of(None) is None
        assert month_of("") is None
        assert month_of("not-a-date") is None


class TestYearMonthToDate:
    """Tests for year_month_to_date()."""

    def test_returns_first_of_month(self) -> None:
        from gamarr.utils import year_month_to_date

        assert year_month_to_date(202511) == "2025-11-01"
        assert year_month_to_date(202401) == "2024-01-01"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_utils.py -k "SubtractMonths or MonthOf or YearMonthToDate" -v`
Expected: FAIL — `ImportError: cannot import name 'subtract_months' from 'gamarr.utils'`.

- [ ] **Step 3: Implement the helpers**

Add `import datetime` to the import block at the top of `src/gamarr/utils.py` (alphabetical, before `import itertools`).

Append at the end of `src/gamarr/utils.py`:

```python
def subtract_months(year_month: int, months: int) -> int:
    """Return *year_month* (``YYYYMM``) reduced by *months* months.

    ``months <= 0`` returns *year_month* unchanged, matching the project's
    ``0 = no limit`` convention.

    Args:
        year_month: A ``YYYYMM`` value, e.g. ``202511``.
        months: Whole months to subtract.

    Returns:
        The resulting ``YYYYMM`` value.
    """
    if months <= 0:
        return year_month
    index = (year_month // 100) * 12 + (year_month % 100 - 1) - months
    return (index // 12) * 100 + (index % 12) + 1


def month_of(date_str: str | None) -> int | None:
    """Return the ``YYYYMM`` month of a release-date string.

    Accepts the ISO (``YYYY-MM-DD``) and UK (``DD-MM-YYYY``) shapes the
    Metacritic scrubbing already tolerates.

    Args:
        date_str: The date string, or ``None``.

    Returns:
        The ``YYYYMM`` month, or ``None`` when absent or unparsable.
    """
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            parsed = datetime.datetime.strptime(date_str.strip(), fmt)
        except (ValueError, TypeError):
            continue
        return parsed.year * 100 + parsed.month
    return None


def year_month_to_date(year_month: int) -> str:
    """Return the first day of a ``YYYYMM`` month as an ISO date string.

    Args:
        year_month: A ``YYYYMM`` value, e.g. ``202401``.

    Returns:
        ``"YYYY-MM-01"``, the form the browse scan's ``cutoff_date`` expects.
    """
    return f"{year_month // 100:04d}-{year_month % 100:02d}-01"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_utils.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/utils.py tests/unit/test_utils.py
git commit -m "feat: add YYYYMM month arithmetic helpers"
```

---

### Task 2: `max_date` and `max_cycle_months` config fields

**Files:**

- Modify: `src/gamarr/config.py` (`import datetime`; `MetacriticPlatformConfig`)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
class TestMaxDateConfig:
    """Tests for the max_date / max_cycle_months config keys."""

    def test_defaults_are_zero(self) -> None:
        """Both date keys default to 0 (disabled / no pacing)."""
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert cfg.max_date == 0
        assert cfg.max_cycle_months == 0

    @pytest.mark.parametrize("value", [0, 202401, 202512])
    def test_accepts_zero_and_valid_year_month(self, value: int) -> None:
        """0 disables date mode; a real YYYYMM month is accepted."""
        from gamarr.config import MetacriticPlatformConfig

        assert MetacriticPlatformConfig(max_date=value).max_date == value

    @pytest.mark.parametrize("value", [2024, 202413, 202400, -202401, 999])
    def test_rejects_invalid_values(self, value: int) -> None:
        """Anything that is not 0 or a valid YYYYMM month is a config error."""
        from gamarr.config import MetacriticPlatformConfig

        with pytest.raises(ValidationError) as excinfo:
            MetacriticPlatformConfig(max_date=value)
        assert "max_date must be 0 (disabled) or YYYYMM with month 01-12" in str(excinfo.value)

    def test_rejects_dashed_string(self) -> None:
        """A dashed date string is not an integer and must fail validation."""
        from gamarr.config import MetacriticPlatformConfig

        with pytest.raises(ValidationError):
            MetacriticPlatformConfig(max_date="2024-01")

    def test_future_max_date_clamps_to_current_month(self) -> None:
        """A future floor would sweep nothing, so it clamps with a warning."""
        import datetime

        from gamarr.config import MetacriticPlatformConfig

        now = datetime.datetime.now(tz=datetime.UTC)
        future = (now.year + 2) * 100 + now.month
        cfg = MetacriticPlatformConfig(max_date=future)
        assert cfg.max_date == now.year * 100 + now.month

    def test_max_cycle_months_rejects_negative(self) -> None:
        """max_cycle_months is never negative."""
        from gamarr.config import MetacriticPlatformConfig

        with pytest.raises(ValidationError):
            MetacriticPlatformConfig(max_cycle_months=-1)

    def test_platform_override_carries_date_keys(self) -> None:
        """Both keys work inside review_sites.metacritic.platform_overrides."""
        from gamarr.config import Config

        cfg = Config(
            review_sites={
                "metacritic": {
                    "platform_overrides": {
                        "pc": {"max_date": 202401, "max_cycle_months": 3},
                    },
                },
            },
        )
        pc = cfg.review_sites.metacritic.platform_overrides["pc"]
        assert pc.max_date == 202401
        assert pc.max_cycle_months == 3
```

`tests/unit/test_config.py` already imports `pytest` and `ValidationError`; if `ValidationError` is not imported, add
`from pydantic import ValidationError` beside the existing imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -k "MaxDateConfig" -v`
Expected: FAIL — `AttributeError: 'MetacriticPlatformConfig' object has no attribute 'max_date'` (extra keys are ignored by pydantic by default, so the assertion fails rather than erroring).

- [ ] **Step 3: Implement the fields and validator**

In `src/gamarr/config.py`, add `import datetime` to the stdlib import block (before `import contextlib` ordering — ruff `I` will sort it; run `uv run ruff check --fix src/gamarr/config.py` afterwards).

In `MetacriticPlatformConfig`, after the `max_cycle_pages` line add:

```python
    max_date: int = 0
    max_cycle_months: int = Field(default=0, ge=0)
```

and after the field block add the validator:

```python
    @field_validator("max_date")
    @classmethod
    def _validate_max_date(cls, value: int) -> int:
        """Validate ``YYYYMM`` and clamp a future floor to the current month.

        ``0`` disables date mode. A floor later than the current calendar
        month would sweep nothing, so it is clamped rather than rejected.

        Args:
            value: The configured ``max_date``.

        Returns:
            The validated (possibly clamped) month.

        Raises:
            ValueError: When *value* is neither 0 nor a valid ``YYYYMM`` month.
        """
        if value == 0:
            return 0
        if value < 0 or not 100001 <= value <= 999912 or not 1 <= value % 100 <= 12:
            raise ValueError("max_date must be 0 (disabled) or YYYYMM with month 01-12, e.g. 202401")
        now = datetime.datetime.now(tz=datetime.UTC)
        current_month = now.year * 100 + now.month
        if value > current_month:
            logger.warning(
                "Config: max_date {} is in the future — clamping to {}",
                value,
                current_month,
            )
            return current_month
        return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -k "MaxDateConfig" -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Prove the default config dict is populated**

Run: `uv run python -c "from gamarr.config import _default_config_dict; pc=_default_config_dict()['review_sites']['metacritic']['platform_overrides']['pc']; print(pc['max_date'], pc['max_cycle_months'])"`
Expected: `0 0` — `_default_config_dict()` derives from `Config()`, so no manual dict edit is needed.

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "feat: add max_date and max_cycle_months config keys"
```

---

### Task 3: Rename `cache_pages_hours` to `cache_hours` with migration

**Files:**

- Modify: `src/gamarr/config.py` (`SourceConfigEntry`, `MetacriticPlatformConfig`, `_freegog_defaults`, new migration,
  `_migrations` registry)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
class TestCacheHoursRename:
    """cache_pages_hours is renamed to cache_hours everywhere it appears."""

    def test_metacritic_config_uses_cache_hours(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert cfg.cache_hours == 6
        assert not hasattr(cfg, "cache_pages_hours")

    def test_source_entry_uses_cache_hours(self) -> None:
        from gamarr.config import SourceConfigEntry

        cfg = SourceConfigEntry(name="fitgirl")
        assert cfg.cache_hours == 6
        assert not hasattr(cfg, "cache_pages_hours")

    def test_migrates_metacritic_override(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {"pc": {"cache_pages_hours": 12}},
                },
            },
        }
        assert _migrate_config(raw) is True
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["cache_hours"] == 12
        assert "cache_pages_hours" not in pc

    def test_migrates_every_download_site(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "download_sites": [
                {"fitgirl": {"cache_pages_hours": 3}},
                {"freegog": {"cache_pages_hours": 9}},
            ],
        }
        assert _migrate_config(raw) is True
        entries = {list(e.keys())[0]: e[list(e.keys())[0]] for e in raw["download_sites"]}
        assert entries["fitgirl"]["cache_hours"] == 3
        assert entries["freegog"]["cache_hours"] == 9
        assert "cache_pages_hours" not in entries["fitgirl"]
        assert "cache_pages_hours" not in entries["freegog"]

    def test_existing_cache_hours_wins(self) -> None:
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {
            "review_sites": {
                "metacritic": {
                    "platform_overrides": {"pc": {"cache_pages_hours": 1, "cache_hours": 24}},
                },
            },
        }
        _migrate_config(raw)
        pc = raw["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["cache_hours"] == 24
        assert "cache_pages_hours" not in pc

    def test_default_config_dict_uses_cache_hours(self) -> None:
        from gamarr.config import _default_config_dict

        defaults = _default_config_dict()
        pc = defaults["review_sites"]["metacritic"]["platform_overrides"]["pc"]
        assert pc["cache_hours"] == 6
        assert "cache_pages_hours" not in pc
        for entry in defaults["download_sites"]:
            name = list(entry.keys())[0]
            assert entry[name]["cache_hours"] == 6
            assert "cache_pages_hours" not in entry[name]

    def test_freegog_injection_uses_new_key(self) -> None:
        """A config missing freegog gets the new key, not the legacy one."""
        from gamarr.config import _migrate_config

        raw: dict[str, Any] = {"download_sites": [{"fitgirl": {"enabled": True}}]}
        _migrate_config(raw)
        freegog = next(e["freegog"] for e in raw["download_sites"] if "freegog" in e)
        assert freegog["cache_hours"] == 6
        assert "cache_pages_hours" not in freegog
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -k "CacheHoursRename" -v`
Expected: FAIL — `AttributeError: 'MetacriticPlatformConfig' object has no attribute 'cache_hours'`.

- [ ] **Step 3: Rename the fields**

In `src/gamarr/config.py`:

- `SourceConfigEntry`: `cache_pages_hours: int = Field(default=6, gt=0, le=168)` → `cache_hours: int = Field(default=6, gt=0, le=168)`.
- `MetacriticPlatformConfig`: `cache_pages_hours: int = 6` → `cache_hours: int = 6`.
- In `_migrate_add_freegog_to_download_sites`, change the `_freegog_defaults` key `"cache_pages_hours": 6` →
  `"cache_hours": 6`.

- [ ] **Step 4: Add the migration and register it**

In `src/gamarr/config.py`, immediately before `def _migrate_config(`:

```python
def _migrate_cache_pages_hours_to_cache_hours(raw: dict[str, Any]) -> bool:
    """Rename ``cache_pages_hours`` to ``cache_hours`` for every owner.

    The key exists on two different models — the Metacritic platform config
    (browse-page cache) and each download source (sitemap cache) — so both
    shapes are migrated here. Values are preserved.

    Args:
        raw: The raw YAML config dict, mutated in place.

    Returns:
        True when at least one key was renamed or cleaned up.
    """
    changed = False

    mc_cfg = raw.get("review_sites")
    if isinstance(mc_cfg, dict):
        metacritic = mc_cfg.get("metacritic")
        if isinstance(metacritic, dict):
            overrides = metacritic.get("platform_overrides")
            if isinstance(overrides, dict):
                for platform_key, platform_cfg in overrides.items():
                    if isinstance(platform_cfg, dict):
                        changed |= _rename_config_key(
                            platform_cfg,
                            "cache_pages_hours",
                            "cache_hours",
                            str(platform_key),
                        )

    download_sites = raw.get("download_sites")
    if isinstance(download_sites, list):
        for entry in download_sites:
            if isinstance(entry, dict) and entry:
                source_name = str(list(entry.keys())[0])
                body = entry[list(entry.keys())[0]]
                if isinstance(body, dict):
                    changed |= _rename_config_key(body, "cache_pages_hours", "cache_hours", source_name)

    return changed
```

Register it in the `_migrations` list in `_migrate_config` immediately before
`_migrate_add_freegog_to_download_sites`, not at the end of the list:

```python
            _migrate_add_post_process_path_case,
            # _migrate_cache_pages_hours_to_cache_hours must run BEFORE
            # _migrate_add_freegog_to_download_sites: the freegog defaults
            # injection now writes cache_hours, so a still-unrenamed legacy
            # key would look "already migrated" and its value would be
            # discarded in favour of the default.
            _migrate_cache_pages_hours_to_cache_hours,
            _migrate_add_freegog_to_download_sites,
```

Position matters: an end-of-list registration makes
`test_migrates_every_download_site` fail (`assert 6 == 9`) because
`_rename_config_key` keeps the injected default and drops the user's legacy
value. Running before the injection keeps the value-preservation guarantee.

- [ ] **Step 5: Update the existing tests that assert the old name**

In `tests/unit/test_config.py` the following assertions reference `cache_pages_hours` and must be updated to
`cache_hours` (mechanical rename, same line numbers ±3):

- `test_cache_pages_hours_replaces_cache_ttl_hours` → rename to `test_cache_hours_replaces_cache_ttl_hours`; assert `cfg.cache_hours == 6` and `not hasattr(cfg, "cache_pages_hours")`.
- `test_source_config_entry_cache_pages_hours` → rename to `test_source_config_entry_cache_hours`; same treatment.
- `test_migrate_cache_ttl_hours_to_cache_pages_hours` → rename to `test_migrate_cache_ttl_hours_to_cache_hours`; assert the migrated key is `cache_hours`.
- `test_migrate_fitgirl_cache_ttl_hours_to_cache_pages_hours` → rename to `test_migrate_fitgirl_cache_ttl_hours_to_cache_hours`; assert `fg["cache_hours"] == 12`.
- `test_default_config_dict_contains_cache_pages_hours` → rename to `test_default_config_dict_contains_cache_hours`; assert `mc_pc["cache_hours"] == 6`.
- Remaining dict fixtures using `"cache_pages_hours": N` as *input* stay as-is (they now exercise the migration); dict
  assertions using `["cache_pages_hours"]` as *output* must become `["cache_hours"]` — specifically the assertions at
  the old lines 208, 229, 254-263 (fixtures), 301, 342, 634.

Verify with: `grep -n "cache_pages_hours" tests/unit/test_config.py` and confirm every remaining hit is an **input** fixture inside a migration test, or a `not in` / `not hasattr` assertion.

- [ ] **Step 6: Run the config tests**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS — all config tests, including the new `TestCacheHoursRename`.

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/config.py tests/unit/test_config.py
git commit -m "refactor!: rename cache_pages_hours to cache_hours"
```

---

### Task 4: Metacritic client — cutoff flag and month span

**Files:**

- Modify: `src/gamarr/metacritic.py` (`__init__`, `scan_recent_games`, imports)
- Test: `tests/unit/test_metacritic.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_metacritic.py`:

```python
class TestCutoffReachedFlag:
    """A cutoff stop must be distinguishable from an exhausted listing."""

    def _client(self) -> Any:
        from gamarr.metacritic import MetacriticClient

        client = MetacriticClient.__new__(MetacriticClient)
        client.sort_order = "new"
        client._recent_games_last_page = None
        client._recent_games_exhausted = False
        client._recent_games_failed = False
        client._recent_games_cutoff_reached = False
        return client

    def test_cutoff_stop_sets_flag(self) -> None:
        """A page entirely older than the cutoff sets the flag and stops."""
        client = self._client()
        page = [{"title": "Old Game", "slug": "old-game", "release_date": "2019-05-01"}]
        with patch.object(client, "_fetch_browse_page", return_value=page):
            games = client.scan_recent_games("pc", cutoff_date="2024-01-01", year=2025)
        assert games == []
        assert client._recent_games_cutoff_reached is True

    def test_exhausted_listing_does_not_set_flag(self) -> None:
        """An empty listing past page 1 is exhaustion, not a cutoff stop."""
        client = self._client()
        with patch.object(client, "_fetch_browse_page", return_value=[]):
            client.scan_recent_games("pc", start_page=2, year=2025)
        assert client._recent_games_cutoff_reached is False
        assert client._recent_games_exhausted is True

    def test_score_mode_ignores_cutoff(self) -> None:
        """Score modes have no date dimension, so the cutoff never applies."""
        client = self._client()
        client.sort_order = "criticscore"
        page = [{"title": "Old Game", "slug": "old-game", "release_date": "2019-05-01"}]
        with patch.object(client, "_fetch_browse_page", return_value=page):
            games = client.scan_recent_games("pc", cutoff_date="2024-01-01")
        assert len(games) == 1
        assert client._recent_games_cutoff_reached is False

    def test_month_span_stops_the_call(self) -> None:
        """max_month_span stops once pages reach older than the anchored span."""
        client = self._client()
        pages = {
            1: [{"title": "A", "slug": "a", "release_date": "2025-11-10"}],
            2: [{"title": "B", "slug": "b", "release_date": "2025-10-10"}],
            3: [{"title": "C", "slug": "c", "release_date": "2025-08-10"}],
        }
        with patch.object(client, "_fetch_browse_page", side_effect=lambda _p, page, *_a, **_k: pages.get(page)):
            games = client.scan_recent_games("pc", year=2025, max_month_span=2)
        assert [g["title"] for g in games] == ["A", "B"]
        assert client._recent_games_cutoff_reached is True

    def test_month_span_zero_does_not_stop(self) -> None:
        """0 means no month pacing, so every page is collected."""
        client = self._client()
        pages = {
            1: [{"title": "A", "slug": "a", "release_date": "2025-11-10"}],
            2: [{"title": "C", "slug": "c", "release_date": "2025-08-10"}],
        }
        with patch.object(client, "_fetch_browse_page", side_effect=lambda _p, page, *_a, **_k: pages.get(page)):
            games = client.scan_recent_games("pc", year=2025, max_month_span=0)
        assert [g["title"] for g in games] == ["A", "C"]
```

Ensure `tests/unit/test_metacritic.py` imports `patch` (`from unittest.mock import patch`) — it already patches
`_fetch_browse_page` elsewhere; reuse the same import style found in the file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_metacritic.py -k "CutoffReachedFlag" -v`
Expected: FAIL — `AttributeError: '_recent_games_cutoff_reached'` and `TypeError: scan_recent_games() got an unexpected keyword argument 'max_month_span'`.

- [ ] **Step 3: Add the flag and the span parameter**

In `src/gamarr/metacritic.py`:

- Extend the utils import:

```python
from gamarr.utils import is_cancelled, month_of, normalise_for_compare, subtract_months, year_month_to_date
```

- In `MetacriticClient.__init__`, after the `_recent_games_failed` assignment add:

```python
        # True only when the last scan ended because a page's games were all
        # older than the effective cutoff (the absolute floor and/or the month
        # span). A cutoff stop is not an exhausted listing, so the caller can
        # tell "sweep reached its date bound" from "year listing ran out".
        self._recent_games_cutoff_reached: bool = False
```

- In `scan_recent_games`, extend the signature after `max_pages: int = 0,`:

```python
        max_month_span: int = 0,
```

and extend the docstring Args block with:

```python
            max_month_span: Stop once a page's games are all older than
                ``first page month - max_month_span`` (0 = no month pacing).
                The span is anchored on the newest release date of this
                call's first page, so a caller that wants a budget shared
                across calls must pass the remaining allowance each time.
```

- After `self._recent_games_failed = False` inside the method, reset the new flag and add the anchor:

```python
        self._recent_games_cutoff_reached = False
        span_anchor: int | None = None
```

- Replace the early-stop block (the `if _should_stop_scan(...)` call) with:

```python
            # Resolve the effective date cutoff: the absolute floor and/or the
            # month span anchored on this call's first page, whichever is newer.
            if max_month_span > 0 and span_anchor is None:
                span_anchor = max(
                    (found for found in (month_of(g.get("release_date")) for g in games) if found is not None),
                    default=None,
                )
            effective_cutoff = cutoff_date
            if max_month_span > 0 and span_anchor is not None:
                span_cutoff = year_month_to_date(subtract_months(span_anchor, max_month_span))
                if effective_cutoff is None or span_cutoff > effective_cutoff:
                    effective_cutoff = span_cutoff

            # Record a date-bound stop before the generic stop helper runs, so a
            # cutoff stop is never confused with an exhausted listing.
            if self.sort_order == "new" and _page_is_before_cutoff(games, effective_cutoff):
                self._recent_games_cutoff_reached = True

            # Check early-stop conditions
            if _should_stop_scan(
                self.sort_order,
                games,
                effective_cutoff,
                max_pages,
                page_number,
                start_page,
            ):
                break
```

- [ ] **Step 4: Run the client tests**

Run: `uv run pytest tests/unit/test_metacritic.py -v`
Expected: PASS — new class passes and the existing scan tests are unchanged (both `cutoff_date` and `max_pages` semantics are preserved; the only behaviour change is the extra flag).

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/metacritic.py tests/unit/test_metacritic.py
git commit -m "feat: add month-span pace and cutoff-reached flag to browse scan"
```

---

### Task 5: Pipeline mode resolution and date-mode browse loop

**Files:**

- Modify: `src/gamarr/pipeline.py` (`AcquisitionConfig`, `run_acquisition`, `_make_fitgirl_entry`, `_build_source`,
  `_run_discovery_phases`, progress log, `_month_span` helper, utils import)
- Test: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_pipeline.py`:

```python
class TestDateModeResolution:
    """max_date switches sort_order "new" to date mode; score modes ignore it."""

    @staticmethod
    def _run(
        *,
        tmp_path: Path,
        max_date: int = 0,
        max_cycle_months: int = 0,
        sort_order: str = "new",
        max_pages: int = 500,
        max_cycle_pages: int = 0,
        scan_games: list[dict[str, Any]] | None = None,
        cutoff_reached: bool = False,
        seed_pages: dict[int, int] | None = None,
        db_path: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Run one cycle and return (scan calls, log lines)."""
        from loguru import logger as loguru_logger

        calls: list[dict[str, Any]] = []
        mock_mc = MagicMock()

        def fake_scan(*_args: Any, **kwargs: Any) -> list:
            calls.append(dict(kwargs))
            mock_mc._recent_games_last_page = kwargs.get("start_page", 1)
            mock_mc._recent_games_exhausted = False
            mock_mc._recent_games_failed = False
            mock_mc._recent_games_cutoff_reached = cutoff_reached
            return scan_games or []

        mock_mc._recent_games_last_page = 0
        mock_mc._recent_games_exhausted = False
        mock_mc._recent_games_failed = False
        mock_mc._recent_games_cutoff_reached = False
        mock_mc.scan_recent_games.side_effect = fake_scan

        path = db_path or str(tmp_path / "date-mode.db")
        if seed_pages:
            from gamarr.database import Database

            seed_db = Database(path)
            for year, page in seed_pages.items():
                seed_db.set_last_scanned_page("pc", year, page)
            seed_db.close()

        logs: list[str] = []
        sink_id = loguru_logger.add(lambda msg: logs.append(str(msg)), level="INFO", format="{message}")
        try:
            with (
                patch("gamarr.pipeline.MetacriticClient") as mock_mc_cls,
                patch("gamarr.pipeline.QBittorrentClient") as mock_qbt_cls,
                patch("gamarr.pipeline._check_scrape_health", return_value="metacritic_broken"),
            ):
                mock_mc_cls.return_value = mock_mc
                mock_qbt = MagicMock()
                mock_qbt.is_connected.return_value = True
                mock_qbt_cls.return_value = mock_qbt
                run_acquisition(
                    platform="pc",
                    db_path=path,
                    qbt_host="localhost",
                    qbt_port=8080,
                    max_pages=max_pages,
                    max_cycle_pages=max_cycle_pages,
                    max_date=max_date,
                    max_cycle_months=max_cycle_months,
                    sort_order=sort_order,
                )
        finally:
            loguru_logger.remove(sink_id)
        return calls, logs

    def test_date_mode_passes_floor_cutoff_and_no_page_limit(self, tmp_path: Path) -> None:
        """Date mode bounds the scan by the floor date, not a page count."""
        calls, _logs = self._run(tmp_path=tmp_path, max_date=202401, max_pages=500)
        assert calls, "Expected at least one browse call"
        for call in calls:
            assert call["cutoff_date"] == "2024-01-01"
            assert call["max_pages"] == 0
            assert isinstance(call["year"], int) and call["year"] >= 2024

    def test_date_mode_year_range_starts_at_floor_year(self, tmp_path: Path) -> None:
        """The sweep never walks older than the floor year."""
        calls, _logs = self._run(tmp_path=tmp_path, max_date=202401)
        years = [c["year"] for c in calls]
        assert min(years) == 2024

    def test_score_mode_ignores_max_date(self, tmp_path: Path) -> None:
        """criticscore has no year dimension, so the date keys are inert."""
        calls, logs = self._run(tmp_path=tmp_path, max_date=202401, sort_order="criticscore", max_pages=40)
        assert calls
        for call in calls:
            assert call["cutoff_date"] is None
            assert call["year"] is None
            assert call["max_pages"] == 40
        assert any("max_date/max_cycle_months ignored" in line for line in logs)

    def test_date_mode_logs_ignored_page_keys(self, tmp_path: Path) -> None:
        """Page keys set alongside max_date are reported as ignored."""
        _calls, logs = self._run(
            tmp_path=tmp_path,
            max_date=202401,
            max_pages=500,
            max_cycle_pages=4,
        )
        assert any("max_pages ignored in date mode" in line for line in logs)
        assert any("max_cycle_pages ignored in date mode" in line for line in logs)

    def test_date_mode_passes_month_span(self, tmp_path: Path) -> None:
        """The month allowance reaches the client as max_month_span."""
        calls, _logs = self._run(tmp_path=tmp_path, max_date=202401, max_cycle_months=2)
        assert calls[0]["max_month_span"] == 2

    def test_no_month_span_when_pacing_disabled(self, tmp_path: Path) -> None:
        """max_cycle_months 0 sweeps to the floor with no span limit."""
        calls, _logs = self._run(tmp_path=tmp_path, max_date=202401, max_cycle_months=0)
        assert calls[0]["max_month_span"] == 0

    def test_date_mode_skips_budget_reset(self, tmp_path: Path) -> None:
        """The page budget and its reset branch must not run in date mode."""
        import datetime

        year = datetime.datetime.now(tz=datetime.UTC).year
        _calls, logs = self._run(
            tmp_path=tmp_path,
            max_date=202401,
            max_pages=2,
            seed_pages={year: 500},
        )
        assert not any("Budget exhausted" in line for line in logs)

    def test_date_mode_resets_progress_at_floor(self, tmp_path: Path) -> None:
        """Reaching the floor resets cursors so the next sweep starts newest."""
        import datetime

        from gamarr.database import Database

        year = datetime.datetime.now(tz=datetime.UTC).year
        path = str(tmp_path / "floor.db")
        self._run(
            tmp_path=tmp_path,
            max_date=202401,
            sort_order="new",
            cutoff_reached=True,
            scan_games=[{"title": "Old", "slug": "old", "release_date": "2023-12-15"}],
            seed_pages={year: 7},
            db_path=path,
        )
        db = Database(path)
        assert db.get_last_scanned_page("pc", year) == 0, "Floor reached must reset the year cursor"
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py -k "DateModeResolution" -v`
Expected: FAIL — `TypeError: run_acquisition() got an unexpected keyword argument 'max_date'`.

- [ ] **Step 3: Rename the cache parameters and add the date parameters**

In `src/gamarr/pipeline.py`:

- `AcquisitionConfig`: `cache_pages_hours: int = 6` → `cache_hours: int = 6`; after `max_cycle_pages: int | None =
  None` add:

```python
    max_date: int = 0
    max_cycle_months: int = 0
```

- `run_acquisition` signature: `cache_pages_hours: int = 6` → `cache_hours: int = 6`; `fitgirl_cache_pages_hours: int
  = 6` → `fitgirl_cache_hours: int = 6`; after `max_cycle_pages: int | None = None,` add `max_date: int = 0,` and
  `max_cycle_months: int = 0,`.

- In the `AcquisitionConfig(...)` construction: `cache_pages_hours=cache_pages_hours` → `cache_hours=cache_hours`; add
  `max_date=max_date,` and `max_cycle_months=max_cycle_months,`.

- `_make_fitgirl_entry`: `entry.cache_pages_hours = fitgirl_cache_pages_hours` → `entry.cache_hours = fitgirl_cache_hours`.

- `_build_source`: keep the constructor kwarg name, read the renamed field:

```python
            kwargs: dict[str, Any] = {
                "platform": entry.platform,
                "db": db,
                # Source classes still take cache_pages_hours (their sitemap TTL);
                # only the config field was renamed.
                "cache_pages_hours": entry.cache_hours,
            }
```

- Remaining `cfg.cache_pages_hours` uses (the scrape-health `cached_exists` block and the `scan_recent_games` call)
  become `cfg.cache_hours`.

- [ ] **Step 4: Add the `_month_span` helper and the utils import**

In `src/gamarr/pipeline.py`, extend the existing `from gamarr.utils import (...)` block with `month_of` and
`year_month_to_date` (keep alphabetical order inside the block).

Add next to `_is_older_than`:

```python
def _month_span(newer: int | None, older: int | None) -> int:
    """Return whole months between two ``YYYYMM`` values.

    Args:
        newer: The newer ``YYYYMM`` month, or ``None``.
        older: The older ``YYYYMM`` month, or ``None``.

    Returns:
        Whole months from *older* to *newer*; 0 when either side is unknown.
    """
    if not newer or not older:
        return 0
    return max(0, (newer // 100) * 12 + newer % 100 - ((older // 100) * 12 + older % 100))
```

- [ ] **Step 5: Replace the browse-phase setup and year loop**

In `_run_discovery_phases`, replace the region from the line `browse_games: list[dict[str, Any]] = []` through the end of the year loop (the line before `# A full pass that found no pages anywhere means the range is fully` … i.e. keep the drained-reset block but rewrite it in Step 6) with:

```python
        browse_games: list[dict[str, Any]] = []
        new_pending: int = 0
        scan_year_anchor = datetime.datetime.now(tz=datetime.UTC).year

        # Mode resolution: a date bound needs the per-year browse dimension,
        # which only sort_order "new" has.  Everywhere else the page budget and
        # its pace stay in charge.
        date_mode = bool(cfg.max_date) and cfg.sort_order == "new"
        floor_month = cfg.max_date if date_mode else 0

        pages_this_cycle = 0
        min_month_seen: int | None = None
        max_month_seen: int | None = None
        cutoff_stopped = False

        if cfg.enabled:
            # Detect sort_order change and reset progress if needed
            previous_sort_order = db.get_last_sort_order(platform)
            if previous_sort_order is not None and previous_sort_order != cfg.sort_order:
                logger.info(
                    "Sort order changed from '{}' to '{}'",
                    previous_sort_order,
                    cfg.sort_order,
                )
                db.clear_browse_cache(platform)
                db.reset_progress(platform, cfg.sort_order)

            mc.sort_order = cfg.sort_order

            if cfg.max_date and not date_mode:
                logger.info(
                    "Config: max_date/max_cycle_months ignored because sort_order is '{}'",
                    cfg.sort_order,
                )
            if date_mode and cfg.max_pages:
                logger.info("Config: max_pages ignored in date mode (max_date={})", floor_month)
            if date_mode and cfg.max_cycle_pages:
                logger.info(
                    "Config: max_cycle_pages ignored in date mode (max_cycle_months={})",
                    cfg.max_cycle_months,
                )

            # Calculate year range
            if cfg.sort_order == "new":
                if date_mode:
                    cutoff_year = min(scan_year_anchor, floor_month // 100)
                else:
                    years_back = max(0, math.ceil((cfg.max_pages if cfg.max_pages else 500) / 52))
                    cutoff_year = scan_year_anchor - years_back
                current_year = scan_year_anchor
            else:
                # sort_order != "new" (criticscore/userscore): no year dimension, use year=0 sentinel
                cutoff_year = 0
                current_year = 0

            max_pages_cfg = cfg.max_pages if cfg.max_pages else 0
            if date_mode:
                # Date mode has no page budget: the floor bounds the sweep and
                # the month allowance paces it.
                remaining_pages = 0
                cycle_allowance = 0
            else:
                total_scanned = db.sum_scanned_pages(platform, cutoff_year, current_year)

                # Budget check — auto-reset when exhausted
                if max_pages_cfg > 0 and total_scanned >= max_pages_cfg:
                    logger.info(
                        "Budget exhausted — {} of {} pages scanned, restarting from page 1",
                        total_scanned,
                        max_pages_cfg,
                    )
                    db.reset_progress(platform, cfg.sort_order)
                    total_scanned = 0

                remaining_pages = max_pages_cfg - total_scanned if max_pages_cfg > 0 else 0
                # Page allowance shared by the whole cycle (0 = unlimited).  It is
                # spent on one year at a time, so a cycle drains the newest year
                # instead of taking max_cycle_pages from every year in the range.
                cycle_allowance = cfg.max_cycle_pages if cfg.max_cycle_pages and cfg.max_cycle_pages > 0 else 0

            fetch_failed = False
            drained_probes = 0

            # Year loop — newest year first, one year at a time: a year is
            # drained until it has no pages left before the next year down is
            # touched, and the cycle stops once its allowance is spent.
            for scan_year in range(current_year, cutoff_year - 1, -1):
                if is_cancelled(cancel_event):
                    break
                if date_mode:
                    if cfg.max_cycle_months and _month_span(max_month_seen, min_month_seen) >= cfg.max_cycle_months:
                        break
                else:
                    if max_pages_cfg > 0 and remaining_pages <= 0:
                        break
                    if cycle_allowance > 0 and pages_this_cycle >= cycle_allowance:
                        break
                start_page = db.get_last_scanned_page(platform, scan_year) + 1
                try:
                    if date_mode:
                        per_call_max = 0
                        cutoff_date = year_month_to_date(floor_month)
                        month_span_left = (
                            max(0, cfg.max_cycle_months - _month_span(max_month_seen, min_month_seen))
                            if cfg.max_cycle_months
                            else 0
                        )
                    else:
                        # Allowance for this call: the smaller of the remaining
                        # budget and what is left of the cycle allowance (0 = no
                        # limit on either).
                        allowance_left = cycle_allowance - pages_this_cycle if cycle_allowance > 0 else 0
                        per_call_max = remaining_pages if max_pages_cfg > 0 else 0
                        if cycle_allowance > 0 and (per_call_max == 0 or allowance_left < per_call_max):
                            per_call_max = allowance_left
                        cutoff_date = None
                        month_span_left = 0

                    year_games = mc.scan_recent_games(
                        platform,
                        # The client keeps its own parameter name (cache_pages_hours);
                        # only config keys and the pipeline API were renamed.
                        cache_pages_hours=cfg.cache_hours,
                        cutoff_date=cutoff_date,
                        cancel_event=cancel_event,
                        start_page=start_page,
                        show_progress=True,
                        year=scan_year if cfg.sort_order == "new" else None,
                        max_pages=per_call_max,
                        max_month_span=month_span_left,
                    )
                    browse_games.extend(year_games)
                    last_page = mc._recent_games_last_page if isinstance(mc._recent_games_last_page, int) else 0
                    pages_scanned = last_page - start_page + 1 if last_page >= start_page else 0

                    # Only real pages advance the year's progress row.  An
                    # exhausted year keeps its last real page so the next cycle
                    # probes the same page instead of creeping forward and
                    # spending the budget on empty fetches.
                    if pages_scanned > 0:
                        db.set_last_scanned_page(platform, scan_year, last_page)

                    # Only a probe the client confirmed as an empty listing counts
                    # as drained; an outright fetch failure must never be read as
                    # the end of a year's listings, and a zero-page probe that is
                    # neither (scan stopped early) must not block the drained
                    # reset either.
                    if pages_scanned == 0:
                        if getattr(mc, "_recent_games_exhausted", None) is True:
                            drained_probes += 1
                        elif getattr(mc, "_recent_games_failed", None) is True:
                            fetch_failed = True

                    if date_mode:
                        for game in year_games:
                            game_month = month_of(game.get("release_date"))
                            if game_month is None:
                                continue
                            if min_month_seen is None or game_month < min_month_seen:
                                min_month_seen = game_month
                            if max_month_seen is None or game_month > max_month_seen:
                                max_month_seen = game_month
                        if getattr(mc, "_recent_games_cutoff_reached", False) is True:
                            cutoff_stopped = True
                            break

                    pages_this_cycle += pages_scanned
                    if not date_mode and max_pages_cfg > 0:
                        remaining_pages -= pages_scanned
                except Exception:
                    fetch_failed = True
                    logger.exception("Scan failed for year {} — will retry next cycle", scan_year)
```

- [ ] **Step 6: Replace the drained-reset block**

Immediately after the year loop, replace the existing drained-reset block with:

```python
            if date_mode:
                # A date sweep is complete when this cycle reached the floor (the
                # pace allowance covered the whole span) or when every year in the
                # range probed empty.  A fetch failure never counts as complete.
                sweep_complete = False
                if not fetch_failed and not is_cancelled(cancel_event):
                    covered = _month_span(max_month_seen, min_month_seen)
                    span_to_floor = _month_span(max_month_seen, floor_month)
                    at_floor = cutoff_stopped and (not cfg.max_cycle_months or covered >= span_to_floor)
                    range_empty = pages_this_cycle == 0 and drained_probes > 0
                    sweep_complete = at_floor or range_empty
                if sweep_complete:
                    logger.info(
                        "Backlog complete — reached {} (floor {}), restarting from the newest year",
                        year_month_to_date(min_month_seen or floor_month)[:7],
                        year_month_to_date(floor_month)[:7],
                    )
                    db.reset_progress(platform, cfg.sort_order)
            else:
                # A full pass that found no pages anywhere means the range is fully
                # drained: restart from the newest year so new releases are picked up.
                if (
                    pages_this_cycle == 0
                    and drained_probes > 0
                    and not fetch_failed
                    and not is_cancelled(cancel_event)
                ):
                    logger.info(
                        "Backlog drained — no pages left in {}..{}, restarting from the newest year",
                        cutoff_year,
                        current_year,
                    )
                    db.reset_progress(platform, cfg.sort_order)
```

- [ ] **Step 7: Replace the progress log**

Replace the progress block near the end of `_run_discovery_phases`:

```python
        # Log scan progress if max_pages is configured
        if cfg.enabled and cfg.max_pages and cfg.max_pages > 0:
            total_scanned = db.sum_scanned_pages(platform, cutoff_year, current_year)
            if total_scanned > 0:
                pct = min(100, round(total_scanned / cfg.max_pages * 100))
                logger.info(
                    "Progress: {} of {} pages scanned ({}%)",
                    total_scanned,
                    cfg.max_pages,
                    pct,
                )
```

with:

```python
        # Log scan progress: date mode reports the deepest month reached, page
        # mode keeps the page-budget percentage.
        if cfg.enabled and date_mode:
            logger.info(
                "Backlog: reached {} (floor {}, {} page(s) this cycle)",
                year_month_to_date(min_month_seen)[:7] if min_month_seen else "unknown",
                year_month_to_date(floor_month)[:7],
                pages_this_cycle,
            )
        elif cfg.enabled and cfg.max_pages and cfg.max_pages > 0:
            total_scanned = db.sum_scanned_pages(platform, cutoff_year, current_year)
            if total_scanned > 0:
                pct = min(100, round(total_scanned / cfg.max_pages * 100))
                logger.info(
                    "Progress: {} of {} pages scanned ({}%)",
                    total_scanned,
                    cfg.max_pages,
                    pct,
                )
```

- [ ] **Step 8: Update the existing pipeline tests for the renames**

Run: `grep -n "cache_pages_hours" tests/unit/test_pipeline.py`
Expected hits: the two `fitgirl_entry.cache_pages_hours` / `freegog_entry.cache_pages_hours` assignments near line
1065-1073 — change both to `.cache_hours`. No other hits should remain (`run_acquisition` calls use `cache_*` kwargs
only where the renamed params apply).

- [ ] **Step 9: Run the pipeline tests**

Run: `uv run pytest tests/unit/test_pipeline.py -v`
Expected: PASS — the whole file, including `TestYearDrainOrder` (page mode untouched) and the new `TestDateModeResolution`.

- [ ] **Step 10: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: browse to a max_date floor with max_cycle_months pacing"
```

---

### Task 6: Scheduler key mapping

**Files:**

- Modify: `src/gamarr/scheduler.py` (`_build_kwargs`, ~lines 313-356)
- Test: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_scheduler.py`:

```python
class TestBuildKwargsDateKeys:
    """_build_kwargs must forward the date keys and the renamed cache keys."""

    def test_forwards_max_date_and_max_cycle_months(self) -> None:
        from gamarr.scheduler import _build_kwargs

        config = _make_config()
        pc = config.review_sites.metacritic.platform_overrides["pc"]
        pc.max_date = 202401
        pc.max_cycle_months = 3

        kwargs = _build_kwargs(config)
        assert kwargs["max_date"] == 202401
        assert kwargs["max_cycle_months"] == 3

    def test_forwards_renamed_cache_keys(self) -> None:
        from gamarr.scheduler import _build_kwargs

        config = _make_config()
        config.review_sites.metacritic.platform_overrides["pc"].cache_hours = 4
        fitgirl = next(e for e in config.download_sites if e.name == "fitgirl")
        fitgirl.cache_hours = 5

        kwargs = _build_kwargs(config)
        assert kwargs["cache_hours"] == 4
        assert kwargs["fitgirl_cache_hours"] == 5
        assert "cache_pages_hours" not in kwargs
        assert "fitgirl_cache_pages_hours" not in kwargs
```

Reuse the existing config factory already used by the neighbouring tests in the file (the tests near line 300 build `config` via a helper/fixture — use the same construction that `test_build_kwargs_includes_max_pages_and_max_cycle_pages` uses; if it builds `Config()` inline, do the same instead of inventing `_make_config`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_scheduler.py -k "BuildKwargsDateKeys" -v`
Expected: FAIL — `KeyError: 'max_date'`.

- [ ] **Step 3: Update `_build_kwargs`**

In `src/gamarr/scheduler.py`, in the `_build_kwargs` return dict:

- `"cache_pages_hours": mc_cfg.cache_pages_hours,` → `"cache_hours": mc_cfg.cache_hours,`
- after `"max_cycle_pages": mc_cfg.max_cycle_pages,` add:

```python
        "max_date": mc_cfg.max_date,
        "max_cycle_months": mc_cfg.max_cycle_months,
```

- `"fitgirl_cache_pages_hours": fitgirl_entry.cache_pages_hours if fitgirl_entry else 6,` → `"fitgirl_cache_hours": fitgirl_entry.cache_hours if fitgirl_entry else 6,`

- [ ] **Step 4: Update the existing scheduler tests**

Run: `grep -n "cache_pages_hours" tests/unit/test_scheduler.py`
Expected hits at ~147, 388, 443 (`config.review_sites.metacritic.platform_overrides["pc"].cache_pages_hours = N`) → change to `.cache_hours`.

- [ ] **Step 5: Run the scheduler tests**

Run: `uv run pytest tests/unit/test_scheduler.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/gamarr/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat: forward date-mode keys through the scheduler"
```

---

### Task 7: README and README-accuracy tests

**Files:**

- Modify: `README.md` (feature bullet ~line 25; example blocks ~147/153; per-source table ~164; platform-overrides
  table ~178-186; phases narrative ~304-311; troubleshooting ~434)
- Test: `tests/unit/test_readme_accuracy.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_readme_accuracy.py` (reuse the existing `_find_config_block` / `_parse_readme_defaults` helpers):

```python
class TestReadmeDateModeRows:
    """README must document max_date / max_cycle_months and their defaults."""

    def _readme_defaults(self) -> dict[str, str]:
        with open(README_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        block = _find_config_block(lines, "review_sites.metacritic.platform_overrides.<platform>")
        assert block is not None, "platform_overrides table missing from README"
        return _parse_readme_defaults(block)

    def test_max_date_default_row(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        defaults = self._readme_defaults()
        assert defaults.get("max_date", "MISSING") == str(cfg.max_date)

    def test_max_cycle_months_default_row(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        defaults = self._readme_defaults()
        assert defaults.get("max_cycle_months", "MISSING") == str(cfg.max_cycle_months)

    def test_cache_hours_rows_renamed(self) -> None:
        """Both cache_hours rows exist and no legacy row survives."""
        with open(README_PATH, encoding="utf-8") as f:
            readme = f.read()
        assert "cache_pages_hours" not in readme, "Legacy key must not remain in the README"
        assert readme.count("| `cache_hours` |") >= 2, "Expected the Metacritic and per-source cache_hours rows"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_readme_accuracy.py -k "DateModeRows" -v`
Expected: FAIL — `max_date` row is `MISSING`, and `cache_pages_hours` is still in the README.

- [ ] **Step 3: Update the README tables**

In `README.md`:

- In the `download_sites` YAML example, change both `cache_pages_hours: 6` lines to `cache_hours: 6`.
- Per-source fields table: `| \`cache_pages_hours\` | How long to cache the source index before re-fetching. | \`6\` |` → `| \`cache_hours\` | How long to cache the source index before re-fetching. | \`6\` |`.
- Platform-overrides table, after the `max_cycle_pages` row, add:

```markdown
| `max_date` | Date floor for the `new` sort order: sweep back to and including this month (`YYYYMM`, e.g. `202401`). `0` = disabled. When set, it takes precedence over `max_pages` and is ignored for `criticscore`/`userscore`. | `0` |
| `max_cycle_months` | Pace in date mode: how many months of backlog one cycle may cover. The floor set by `max_date` always wins — this value can only shorten a cycle, never extend past it. `0` = no month pacing. | `0` |
```

- Platform-overrides table: `| \`cache_pages_hours\` | Hours to cache Metacritic browse-page results. | \`6\` |` → `| \`cache_hours\` | Hours to cache Metacritic browse-page results. | \`6\` |`.

- [ ] **Step 4: Update the README narrative**

- Feature bullet (replacing the "Relative date cutoff" bullet):

```markdown
- **Date-based backlog cutoff** — set the oldest month to review with
  `max_date: 202401` and how much of the backlog one cycle covers with
  `max_cycle_months: 2`. Pages remain available as `max_pages` /
  `max_cycle_pages` for `criticscore`/`userscore`, whose browse lists are
  score-ordered and have no date dimension.
```

- Phase 1/2 narrative: after the sentence ending "Games are collected within the `max_pages` browse window.", append:

```markdown
   In date mode (`max_date` set with `sort_order: new`) the window is a month
   floor instead: the sweep stops inside the boundary year as soon as a page's
   games are all older than `max_date`, and `max_cycle_months` limits how many
   months of backlog a single cycle covers. `max_pages` and `max_cycle_pages`
   are then unused. For `criticscore`/`userscore` the date keys are ignored,
   because those browse lists mix years and no page corresponds to a date.
```

- Troubleshooting bullet: after "- Increase `max_pages` to catch more recently released games", add:

```markdown
- Or set `max_date` with `sort_order: new` to state the oldest month you want
  reviewed, so the cutoff is a date instead of a page count
```

- [ ] **Step 5: Run the README tests and the markdown lint**

Run: `uv run pytest tests/unit/test_readme_accuracy.py -v`
Expected: PASS.

Run: `markdownlint -c <(printf '{"default": true, "MD013": {"line_length": 120, "tables": false}}') README.md 2>&1 | grep -c "max_date\|cache_hours" || true`
Expected: `0` — no new lint errors on the touched lines. (The repo's `.markdownlint-cli2.jsonc` sets the same rules; `markdownlint-cli2` is not installed, so pass the config inline as shown.)

- [ ] **Step 6: Commit**

```bash
git add README.md tests/unit/test_readme_accuracy.py
git commit -m "docs: document max_date, max_cycle_months and cache_hours"
```

---

### Task 8: Full quality gate

**Files:** none (verification only)

- [ ] **Step 1: Lint and format**

```bash
uv run ruff check --fix . && uv run ruff format .
```

Expected: no remaining errors; any reported issue is one introduced by this change and must be fixed.

- [ ] **Step 2: Type check**

```bash
uv run mypy .
```

Expected: `Success: no issues found`.

- [ ] **Step 3: Full test suite with coverage**

```bash
uv run pytest --cov=gamarr --cov-fail-under=80 -v
```

Expected: all tests pass, coverage ≥ 80%.

- [ ] **Step 4: Pre-commit**

```bash
pre-commit run --all-files
```

Expected: all hooks pass.

- [ ] **Step 5: Confirm the config migration end to end**

```bash
uv run python -c "
from gamarr.config import _migrate_config
raw = {
    'review_sites': {'metacritic': {'platform_overrides': {'pc': {'cache_pages_hours': 8, 'max_pages': 500}}}},
    'download_sites': [{'fitgirl': {'cache_pages_hours': 6}}],
}
print(_migrate_config(raw))
print(raw['review_sites']['metacritic']['platform_overrides']['pc'])
print(raw['download_sites'])
"
```

Expected: `True`, then `pc` shows `{'cache_hours': 8, 'max_pages': 500}`, then the fitgirl entry shows `{'cache_hours': 6}` — and `max_pages` is **untouched** (no value conversion).

- [ ] **Step 6: Commit any gate fixes**

```bash
git add -A
git commit -m "chore: satisfy lint, type and coverage gates"
```

(Skip when the gate produced no changes.)

---

## Self-review

### 1. Spec coverage

| Spec requirement | Task |
| ------------------ | ------ |
| `max_date` field, `0` = disabled, `YYYYMM` validation, future clamp | Task 2 |
| `max_cycle_months` field, `0` = no pacing | Task 2 |
| `cache_hours` rename on both models | Task 3 |
| In-place `cache_pages_hours` migration (metacritic + all sources), `cache_hours` wins, values preserved | Task 3 |
| FreeGOG injected defaults use the new key | Task 3 |
| Default config dict carries the new keys | Task 2 Step 5 |
| Mode resolution predicate + inert-key logging | Task 5 Steps 5 |
| Precise month floor via `cutoff_date` | Task 5 Steps 5-6 |
| Month span pacing shared across years | Task 4 + Task 5 |
| No page budget / no budget reset in date mode | Task 5 Step 5, tested in Step 1 |
| `_recent_games_cutoff_reached` flag | Task 4 |
| Drained reset retargeted to the floor | Task 5 Step 6, tested in Step 1 |
| Progress log in months | Task 5 Step 7 |
| Unparsable release dates do not stop the cycle | Task 5 (`month_of` returns `None` → skipped; span stays 0) |
| Scheduler forwards new/renamed keys | Task 6 |
| README rows, narrative, troubleshooting | Task 7 |
| Page mode unchanged | Tasks 5 Step 9 (`TestYearDrainOrder` still passes) |
| Lint/type/coverage gates | Task 8 |

### 2. Placeholder scan

 no "TBD", "handle edge cases", "similar to Task N", or unspecified code steps. Every code step shows the code; every
 run step shows the command and the expected result.

### 3. Type consistency

 `max_date: int`, `max_cycle_months: int`, `cache_hours: int`, `fitgirl_cache_hours: int`, `max_month_span: int`, `_recent_games_cutoff_reached: bool`, `_month_span(newer: int | None, older: int | None) -> int`, `subtract_months(int, int) -> int`, `month_of(str | None) -> int | None`, `year_month_to_date(int) -> str`. Names are used identically in every task, and the client keyword `cache_pages_hours` is deliberately left alone wherever it names a constructor argument (Task 5 Step 3, item 5).
