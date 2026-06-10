# Steam discovery source

**Date:** 2026-06-10
**Status:** Approved design

## Problem

gamarr currently only supports Metacritic as a game discovery source:
it browses Metacritic pages for games that meet score thresholds, then
matches them against the FitGirl repacks sitemap. There is no way to
discover games from Steam, which has a vastly larger catalogue and its
own rich review/quality signals.

## Requirements

- Add Steam as an alternative discovery source alongside (but mutually
  exclusive with) Metacritic
- Support multiple discovery modes configured via a list so users can
  pick one or combine several
- Filter discovered games by Steam user review score/percentage and
  minimum review count
- Use a completely separate cache from Metacritic — no shared state
- FitGirl matching and qBittorrent delivery remain unchanged

## Solution

### 1. Source selector in config

Add a top-level config switch and a new `SteamConfig` Pydantic model in
`config.py`. The existing `MetacriticConfig` is untouched; it is simply
ignored when `source: steam`.

```yaml
general:
  source: metacritic      # "metacritic" (default) or "steam"

steam:
  api_key: ""             # optional — needed for discovery_queue, trending
  modes: ["new_releases"] # one or more from the supported mode list
  min_review: very_positive  # label threshold (overwhelming/very_positive/
                             # positive/mostly_positive/mixed)
  min_review_percent: 80     # alternative numeric threshold (0-100)
  min_reviews: 50
  days_since_release: 90
  max_games: 1000
  pending_days: 30
  cache_ttl_hours: 6
  reject_genre: []
  reject_title: []
```

When `general.source` is `"steam"`, the `AcquisitionConfig` and pipeline
use Steam review data instead of Metacritic score data for filtering.

### 2. SteamClient module — `src/gamarr/steam.py`

A new class `SteamClient` responsible for discovering games via Steam's
store API endpoints and filtering by review score thresholds.

```python
class SteamClient:
    def __init__(
        self,
        api_key: str | None,
        cache: SteamCache,
    ) -> None: ...

    def discover_games(
        self,
        modes: list[str],
        cfg: AcquisitionConfig,
    ) -> list[dict[str, Any]]:
        """Run each configured mode, collect app IDs, deduplicate,
        fetch review summaries + app details, filter by thresholds.

        Returns a list of game dicts with the same shape as the
        Metacritic verify-phase output, so the downstream FitGirl
        matching and qBittorrent delivery code is unchanged.
        """
        ...

    # --- Mode methods ---

    def fetch_new_releases(self) -> list[int]:
        """Call store.steampowered.com/api/featuredcategories
        and extract app IDs from the 'new_releases' section.
        No API key required."""

    def fetch_top_sellers(self) -> list[int]:
        """Same endpoint, 'top_sellers' section.
        No API key required."""

    def fetch_coming_soon(self) -> list[int]:
        """Same endpoint, 'coming_soon' section.
        No API key required."""

    def fetch_discovery_queue(self) -> list[int]:
        """Call Store.GetDiscoveryQueue via steam-next's WebAPI.
        Requires api_key to be set. If not configured, logs a
        warning and returns empty."""

    def fetch_trending(self) -> list[int]:
        """Call Store.GetTrendingAppsAmongFriends via steam-next's
        WebAPI. Requires api_key to be set. If not configured,
        logs a warning and returns empty."""

    # --- Review & detail fetch ---

    def fetch_review_summary(self, app_id: int) -> dict | None:
        """Call store.steampowered.com/appreviews/<id>?json=1
        &num_per_page=0. Returns {review_score, review_score_desc,
        total_positive, total_negative, total_reviews}.
        No API key required. Results cached in SteamCache."""

    def fetch_app_details(self, app_id: int) -> dict | None:
        """Call store.steampowered.com/api/appdetails?appids=<id>
        with filters=basic. Returns {name, genres, release_date,
        platforms, developers, ...}.
        No API key required. Results cached in SteamCache."""
```

#### Data flow

```
Configured modes list
       │
       ▼ (run sequentially, deduplicate app_ids)
fetch_new_releases() ───► app_ids ──┐
fetch_top_sellers()  ───► app_ids ──┤
fetch_coming_soon()  ───► app_ids ──┼──► unique app_ids
fetch_discovery_queue() ──► app_ids ─┤
fetch_trending()     ───► app_ids ──┘
       │
       ▼ (for each unique app_id)
fetch_review_summary(app_id) ──► {review_score, total_reviews, ...}
fetch_app_details(app_id)    ──► {name, genres, release_date, ...}
       │
       ▼ (filter)
_score_passes_thresholds(steam_result, cfg)
       │
       ▼ (survivors returned)
list[dict]  ──► pipeline (match → qbt)
```

#### Steam review score mapping

Steam's `appreviews` endpoint returns `review_score` as an integer 1-9.
The config `min_review` label maps to the following scores:

| Config value | Score | Approx % positive |
|---|---|---|
| `overwhelming_positive` | 9 | ≥95% |
| `very_positive` | 8 | ≥85% |
| `positive` | 7 | ≥80% |
| `mostly_positive` | 6 | ≥70% |
| `mixed` | 5 | ≥40% |

When `min_review_percent` is set, the filter uses the actual positive
percentage (`total_positive / total_reviews * 100`) instead of the
score label.

Both checks can be configured but `min_review_percent` takes precedence
when present (provides finer control).

### 3. SteamCache wrapper — `src/gamarr/steam_cache.py`

A dedicated cache wrapper using its own database table, completely
separate from MetacriticCache. The table is defined in `database.py`
and `SteamCache` is a thin wrapper that reads/writes it.

```python
class SteamCache:
    """Caches Steam review summaries and app details in a dedicated
    SQLite table to avoid redundant HTTP requests."""

    def __init__(self, db: Database) -> None: ...
    def get_review_summary(self, app_id: int) -> dict | None: ...
    def set_review_summary(self, app_id: int, data: dict, ttl_hours: int) -> None: ...
    def get_app_details(self, app_id: int) -> dict | None: ...
    def set_app_details(self, app_id: int, data: dict, ttl_hours: int) -> None: ...
```

#### Database table — added in `database.py`

A new table `steam_cache` (new SQLAlchemy ORM class `SteamCacheRow`):

| Column | Type | Purpose |
|--------|------|---------|
| `cache_key` | TEXT (PK) | `"{app_id}_reviews"` or `"{app_id}_details"` |
| `cache_value` | TEXT (JSON) | Serialised response data |
| `cached_at` | TEXT (ISO timestamp) | When cached |

```python
class SteamCacheRow(Base):
    __tablename__ = "steam_cache"
    cache_key: Mapped[str] = mapped_column(String, primary_key=True)
    cache_value: Mapped[str] = mapped_column(Text, nullable=False)
    cached_at: Mapped[str] = mapped_column(String, nullable=False)
```

TTL checking is done at query time: `SteamCache` compares `cached_at +
ttl_hours` against `datetime.now()` and returns `None` for stale entries.
This table has no relationship to Metacritic's `GameDetailCache` or
`sitemap_cache` tables.

### 4. Pipeline changes — `src/gamarr/pipeline.py`

#### `run_acquisition()` gains a `source` parameter

```python
def run_acquisition(
    *,
    source: str = "metacritic",  # ← new
    ...
) -> list[dict[str, Any]]:
```

At the top, after creating the database but before game discovery:

```python
if source == "steam":
    steam_cfg = ...  # extract steam-specific thresholds
    steam_client = SteamClient(api_key, SteamCache(db))
    browse_games = steam_client.discover_games(steam_cfg.modes, cfg)
else:
    mc = MetacriticClient(cache=MetacriticCache(db))
    browse_games = mc.scan_recent_games(...)
    # ... existing browse processing ...
```

After this branch, the code path converges:
- Games that passed the discovery + filtering phase (whether from
  Steam or Metacritic) enter the same pending queue
- FitGirl sitemap matching runs identically
- qBittorrent delivery runs identically
- History deduplication runs identically

#### Score evaluation adapts to source

The existing `_evaluate_scores()` function checks Metacritic-specific
fields (`metascore`, `user_score`, `metascore_review_count`). When
source is steam, a separate evaluation path is used that checks
Steam review fields (`review_score`, `total_reviews`).

The cleanest design: create a new function `_evaluate_steam_scores()`
with the same return type (`"Passed"` or a failure reason string).
The verify-phase code calls the appropriate function based on source.

### 5. Config model — `src/gamarr/config.py`

New Pydantic models:

```python
class GeneralConfig(BaseModel):
    config_version: str = _CONFIG_VERSION
    source: str = "metacritic"   # ← NEW: "metacritic" or "steam"
    ...

class SteamConfig(BaseModel):
    """Steam discovery source settings."""
    api_key: str = ""
    modes: list[str] = Field(default_factory=lambda: ["new_releases"])
    min_review: str = "very_positive"
    min_review_percent: int | None = None
    min_reviews: int = 50
    days_since_release: int = 90
    max_games: int = 1000
    pending_days: int = 30
    cache_ttl_hours: int = 6
    reject_genre: list[str] = Field(default_factory=list)
    reject_title: list[str] = Field(default_factory=list)

class Config(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    metacritic: MetacriticConfig = Field(default_factory=MetacriticConfig)
    steam: SteamConfig = Field(default_factory=SteamConfig)  # ← NEW
    ...
```

Validation: `min_review` must be one of the valid labels. `modes` must
contain only recognised mode names. A warning is logged if a mode
that requires `api_key` is configured without one.

### 6. Scheduler wiring — `src/gamarr/scheduler.py`

The scheduler already passes `db_path`, `fitgirl_rss_url`, and all the
Metacritic/acquisition parameters to `run_acquisition()` via keyword
arguments. A new entry in the `_run_daemon` method reads
`cfg.general.source` and passes `source="steam"` along with the steam-
specific parameters when appropriate.

The existing `Config` model already flows through `_run_daemon` /
`SchedulerConfig`, so the wiring is straightforward — just extract
`steam` fields from `cfg.steam` and pass them to `run_acquisition()`.

## Cache invalidation

- SteamCache entries expire based on `cache_ttl_hours` (same pattern as
  Metacritic's cache TTL)
- App details can be cached longer (24h) since they rarely change
- Review summaries cache for `cache_ttl_hours` (default 6h)
- No cross-contamination with Metacritic cache tables

## Error handling

| Scenario | Behaviour |
|---|---|
| Steam API unreachable (featuredcategories) | Log warning, return empty list for that mode |
| Review fetch fails for individual app | Log debug, skip that app, continue with others |
| API key needed but not configured | Log warning, skip that mode, continue with others |
| Mode name unrecognised | Log error, skip that mode, continue with others |
| All modes fail | Return empty list (no games discovered this cycle) |

## Testing

### Unit tests for `steam.py`

| Test | What it verifies |
|---|---|
| `test_fetch_new_releases_returns_app_ids` | Parses featuredcategories response correctly |
| `test_fetch_review_summary_parses_score` | Maps review_score 1-9 correctly |
| `test_discover_games_deduplicates` | Same app ID from two modes appears once |
| `test_discover_games_filters_low_score` | Games below min_review are excluded |
| `test_discover_games_filters_few_reviews` | Games below min_reviews are excluded |
| `test_discover_games_filters_by_age` | Games older than days_since_release excluded |
| `test_discover_games_handles_missing_key` | Modes needing key silently skipped when empty |
| `test_discover_games_handles_empty_mode_list` | Returns empty list |
| `test_review_score_mapping` | Config labels map to correct score values |
| `test_min_review_percent_takes_precedence` | Percent threshold used when set |

### Unit tests for `steam_cache.py`

| Test | What it verifies |
|---|---|
| `test_set_and_get_review_summary` | Round-trip works |
| `test_cache_expiry` | Entry beyond TTL returns None |
| `test_get_missing_key` | None returned for uncached key |
| `test_cache_separate_from_metacritic` | Metacritic cache tables unaffected |

### Pipeline integration tests

| Test | What it verifies |
|---|---|
| `test_run_acquisition_with_steam_source` | Pipeline completes with source=steam |
| `test_run_acquisition_steam_returns_correct_shape` | Output list[dict] matches expected schema |

### Existing tests — no regressions

All existing Metacritic tests continue to pass unchanged. The new
`source` parameter defaults to `"metacritic"`, preserving existing
behaviour.

## Implementation order

1. Add `SteamCache` class and database table to `database.py`
2. Add `SteamCache` module at `src/gamarr/steam_cache.py`
3. Add `SteamConfig` model to `config.py`
4. Add `SteamClient` class to `src/gamarr/steam.py`
5. Wire source branching into `pipeline.py` (`run_acquisition`)
6. Wire source into `scheduler.py` parameter extraction
7. Add unit tests for SteamClient
8. Add unit tests for SteamCache
9. Add pipeline integration tests
10. Run full test suite — verify no regressions
