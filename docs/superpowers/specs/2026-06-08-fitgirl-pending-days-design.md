# fitgirl_pending_days — Separate pending expiry for FitGirl matching

**Date:** 2026-06-08
**Status:** Approved design

## Problem

Currently a single `pending_days` field under `metacritic.platform_overrides.pc`
controls how long ALL games stay in the pending queue, regardless of which phase
they're in:

1. **Score-waiting phase** — game has TBD or low scores that may improve
2. **FitGirl-matching phase** — game's scores are verified and pass thresholds,
   but the game isn't available on FitGirl yet (no repack/crack released)

These two phases have fundamentally different waiting needs. A game needs
relatively few days for scores to appear, but may need weeks or months for a
crack/repack to be released. The single expiry clock forces the user to choose
one window, resulting in either expired games before a repack arrives, or a
bloated queue of score-waiting games.

## Solution

Split the single `pending_days` into two independent expiry values:

| Field | Location | Default | Purpose |
|-------|----------|---------|---------|
| `pending_days` | `metacritic.platform_overrides.pc` | `30` | How long to wait for TBD/low scores to improve |
| `pending_days` | `sources.fitgirl` | `60` | How long to wait for a FitGirl repack after scores are verified |

When a game's scores are verified and pass all thresholds, its `expires_at` is
recalculated to `now + fitgirl_pending_days`, giving it a fresh expiry window
for the FitGirl matching phase.

## Design

### 1. Config model

#### `src/gamarr/config.py` — `FitGirlSourceConfig`

```python
class FitGirlSourceConfig(BaseModel):
    """FitGirl Repacks source settings."""
    enabled: bool = True
    rss_url: str = "https://fitgirl-repacks.site/feed/"
    platform: str = "pc"
    cache_ttl_hours: int = Field(default=6, gt=0, le=168)
    exclude_keywords: list[str] = Field(default_factory=list)
    pending_days: int = 60  # ← new
```

The existing `MetacriticPlatformConfig.pending_days` (default 30) is unchanged.

#### `configs/gamarr.yml`

```yaml
sources:
  fitgirl:
    enabled: true
    rss_url: https://fitgirl-repacks.site/feed/
    platform: pc
    cache_ttl_hours: 6
    exclude_keywords:
      - hv
    pending_days: 60
```

### 2. Pipeline model + run_acquisition wiring

#### `src/gamarr/pipeline.py` — `AcquisitionConfig`

```python
pending_days: int = 30
fitgirl_pending_days: int = 60  # ← new
```

#### `src/gamarr/pipeline.py` — `run_acquisition()` signature

```python
pending_days: int = 30,
fitgirl_pending_days: int = 60,  # ← new
```

#### Construction in `run_acquisition()`

```python
pending_days=pending_days,
fitgirl_pending_days=fitgirl_pending_days,  # ← new
```

### 3. Scheduler wiring

#### `src/gamarr/scheduler.py` — `_build_kwargs()`

Add after existing fitgirl entries:

```python
"fitgirl_pending_days": config.sources.fitgirl.pending_days,
```

### 4. Database — new method

#### `src/gamarr/database.py`

Add a focused method to recalculate a pending game's expiry:

```python
def update_pending_expiry(self, slug: str, pending_days: int) -> None:
    """Recalculate expires_at to now + pending_days."""
    expires_at = (
        datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=pending_days)
    ).isoformat()
    with self._session() as session:
        row = session.get(PendingGame, slug)
        if row is not None:
            row.expires_at = expires_at
            session.commit()
```

### 5. Threading through verification pipeline

#### `_process_verify_result()` — new param + expiry update

```python
def _process_verify_result(
    db: Database,
    game: Any,
    result: Any,
    thresholds: dict[str, Any],
    *,
    max_verify_attempts: int = 6,
    reject_genre: list[str] | None = None,
    fitgirl_pending_days: int = 60,  # ← new
) -> bool:
```

In the scores-pass branch, after `update_pending_scores` and `reset_verify_attempts`:

```python
    if fitgirl_pending_days:
        db.update_pending_expiry(str(game.slug), fitgirl_pending_days)
```

#### `_verify_pending_scores()` — new param + pass-through

```python
def _verify_pending_scores(
    db: Database,
    mc: MetacriticClient,
    platform: str,
    thresholds: dict[str, Any],
    *,
    cache_ttl_days: int = 7,
    max_verify: int = 50,
    max_verify_attempts: int = 6,
    reject_genre: list[str] | None = None,
    fitgirl_pending_days: int = 60,  # ← new
) -> int:
```

Pass to `_process_verify_result`:

```python
if _process_verify_result(
    db, game, result, thresholds,
    max_verify_attempts=max_verify_attempts,
    reject_genre=reject_genre,
    fitgirl_pending_days=fitgirl_pending_days,  # ← new
):
```

#### Call site in `_run_discovery_phases()`

```python
removed = _verify_pending_scores(
    db, mc, platform, thresholds,
    cache_ttl_days=cfg.cache_ttl_days,
    max_verify=...,
    max_verify_attempts=cfg.max_verify_attempts,
    reject_genre=cfg.reject_genre,
    fitgirl_pending_days=cfg.fitgirl_pending_days,  # ← new
)
```

### 6. Testing

#### Unit tests for `_process_verify_result`

| Test | Coverage |
|------|----------|
| `test_fitgirl_pending_days_updates_expiry` | Game with passing scores → `expires_at` is recalculated to `now + fitgirl_pending_days` |
| `test_fitgirl_pending_days_zero_disabled` | `fitgirl_pending_days=0` → expiry is NOT updated (backward compatible) |
| `test_fitgirl_pending_days_does_not_affect_failure` | Game with failing scores → expiry is NOT updated |

#### Unit test for `update_pending_expiry`

| Test | Coverage |
|------|----------|
| `test_update_pending_expiry` | Direct test of the new database method |

#### Integration

Existing expiry behaviour is preserved when `fitgirl_pending_days` matches the
default — games that pass verification get their expiry extended rather than
expiring on the original `metacritic_pending_days` clock.

## Files changed

| File | Change |
|------|--------|
| `src/gamarr/config.py` | Add `pending_days: int = 60` to `FitGirlSourceConfig` |
| `src/gamarr/database.py` | Add `update_pending_expiry()` method |
| `src/gamarr/pipeline.py` | Add `fitgirl_pending_days` to `AcquisitionConfig`, `run_acquisition()`, `_verify_pending_scores()`, `_process_verify_result()`; add expiry update call in scores-pass branch |
| `src/gamarr/scheduler.py` | Add `fitgirl_pending_days` to `_build_kwargs()` return dict |
| `configs/gamarr.yml` | Add `pending_days: 60` under `sources.fitgirl` |
| `tests/unit/test_database.py` | Add `test_update_pending_expiry` |
| `tests/unit/test_pipeline.py` | Add tests for expiry update behaviour |

## Non-goals

- No changes to the existing `metacritic.platform_overrides.pc.pending_days` field
- No changes to how games are initially added to the pending queue (still uses metacritic `pending_days`)
- No retroactive expiry updates for already-verified games
