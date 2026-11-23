# Per-mode discovery config — review residuals (round-cap)

Branch: `main`
Base reviewed: `origin/main` (`BASE_REF`), HEAD `59d5c66` plus the uncommitted working tree
Round reached: **10 of 10** (`CIRCUIT_BREAKER_ROUNDS`)
Reviewers: `opencode-go/deepseek-v4.1-flash:max` (A), `opencode-go/glm-5.3-flash:max` (B)
Marker emitted: `REVIEW_GATE_STATUS: UNRESOLVED` (completion with residuals — downstream steps may proceed)

## Why this ledger exists

Round 10 was the last round the loop allows. It reported findings (two MEDIUM, six LOW, one of the
MEDIUMs found by both reviewers), so per the round-cap rule every one is recorded here. The fixes
below were applied **after** the final reviewer pass and were therefore **not independently
re-reviewed**.

## Findings from round 10

| # | Severity | Location | Description | Status |
| --- | --- | --- | --- | --- |
| 1 | MEDIUM | `src/gamarr/config.py` (`_rename_source_window`, `_migrate_platform_windows`, `_migrate_metacritic_window`) | A legacy source's default `max_queue_days: 60` is dropped as "the old default", and a stale platform/metacritic fallback (e.g. `45`) was then hoisted, so the effective queue window changed on upgrade (60 → 45). Found by A and B. | applied, not independently re-reviewed — the fallback is now hoisted only when **no** source states a window; otherwise it is dropped with an INFO naming the reason. Covered by the updated `TestSourceMatchingDaysMigration` cases and `test_a_stock_legacy_config_keeps_a_sixty_day_window` |
| 2 | MEDIUM | `tests/unit/test_readme_accuracy.py` | The new top-level `source_matching_days` default was outside the README accuracy net: nothing compared `README.md`'s `60` with `Config().source_matching_days`, and the section heading is not backticked so the existing table parser could not see it. | applied, not independently re-reviewed — `TestReadmeTopLevelDefaults::test_source_matching_days_default_row` asserts the row's default cell against the model |
| 3 | LOW | `src/gamarr/config.py` (`_stray_discovery_keys`) | A platform-level `pending_order` was logged inside the getter but never appended to the returned list, so the helper's contract and its caller disagreed. | applied, not independently re-reviewed — it is returned now, and the reporter keeps the specific "belongs at `review_sites.metacritic.pending_order`" wording |
| 4 | LOW | `src/gamarr/config.py` (`_migrate_platform_discovery`, `_resolve_selector`) | With an existing `discovery` block and no flat `mode`/`sort_order` in the file, the popped defaults were passed as "legacy" values, so the log reported a discarded key the operator never wrote. | applied, not independently re-reviewed — `_resolve_selector(..., report=...)` is skipped when the legacy key was absent |
| 5 | LOW | `src/gamarr/config.py` (`_migrate_source_entry`) | The flat `{"name": …, "max_queue_days": …}` branch is unreachable through `_migrate_config` because the keyed-list migration consumes `name` first; only a direct helper test covered it. | applied, not independently re-reviewed — branch and its direct test deleted |
| 6 | LOW | `src/gamarr/discovery.py`, `src/gamarr/pipeline.py` | `SCAN_ORDER` was reused as the type of `pending_order`, reintroducing in the type names the conflation the glossary and ADR 0006 remove. | applied, not independently re-reviewed — `PENDING_ORDER` is now a distinct alias used by `AcquisitionConfig.pending_order` and `run_acquisition` |
| 7 | LOW | `src/gamarr/pipeline.py` (`_run_discovery_phases`) | A config that sets only `discovery.mode: backlog` uses the model defaults (`oldest_month: 0`, `months_per_cycle: 0`), so one cycle walks the whole catalogue per year listing, bounded only by the acquisition watchdog. | **not fixed** — the defaults are the documented behaviour (`0` = no floor / no pace); round 8 added the explicit "no floor and no month pace — one cycle walks the whole catalogue" warning that names the cost |
| 8 | LOW | `tests/unit/test_config.py` | Guards added by this change had no tests: the stray-key reports for a top-level / `review_sites` / `metacritic`-level `discovery`, a platform-level `pending_order`, and `Config(source_matching_days=-1)` rejection. | applied, not independently re-reviewed — `TestInertDiscoveryKeys::test_every_stray_placement_is_reported` and `TestQueueConfig::test_the_top_level_window_rejects_a_negative_value` |

## Verification gap

The fixes in the table were applied **after** the last reviewer pass, so they were **never
independently re-reviewed**. What was verified after them, in the same session:

- `uv run pytest --cov=gamarr --cov-fail-under=80 -q` → **1308 passed**, coverage **96.81%** (exit 0)
- `uv run ruff check .` → all checks passed; `uv run ruff format --check .` → 41 files already formatted
- `uv run mypy .` → success, no issues in 41 source files
- `markdownlint` under the project rule set → clean for `README.md`
- Behavioural spot checks after the fixes: an upgraded config with `fitgirl.max_queue_days: 60` and a
  stale platform/metacritic `45` resolves to **60**; a platform-level `pending_order` is returned by
  `_stray_discovery_keys`; a mixed file with a `mode: backlog` block keeps it without a phantom
  "legacy value ignored" line.

Not re-verified by a reviewer after the cap: finding 1's interaction with a metacritic-level fallback
in a file that also has a `source_matching_days` source override, and finding 6's type rename across
every annotation site.

## Review history summary

| Round | HIGH | MEDIUM | LOW | Notable outcome |
| --- | --- | --- | --- | --- |
| 1 | 0 | 5 | 4 | Legacy page budgets ≥ ~19.8k crashed config load; `max_pages: 0` inverted to a one-month bound; inert-key reporting moved to load time |
| 2 | 0 | 3 | 7 | Negative legacy window refused startup; `days_since_release` floor unclamped; two migration branches untested |
| 3 | 0 | 4 | 6 | Explicit `max_pages: 0` dropped for score sorts; mixed files skipped per-key migration; glossary `page_depth` contradicted the budget semantics |
| 4 | 0 | 1 | 7 | Legacy `mode`/`sort_order` overwrote an existing block (the mode flip that hit the live config); page counts double-converted |
| 5 | 0 | 4 | 5 | Stock upgrade halved the queue window 60 → 30; explicit `0` overwritten by a legacy key; `days_since_release` could abort startup |
| 6 | 0 | 5 | 2 | Floored `latest` without a pace lost its reach; unusable counts became unbounded sweeps; exotic strings rolled back every migration |
| 7 | 0 | 5 | 6 | Invented bounds replaced junk values; a minimal legacy config swept the catalogue silently; README example contradicted the inherit rule |
| 8 | 0 | 3 | 3 | A leftover flat key could still flip the mode of a block; explicit zero in `latest` became 21 months |
| 9 | 0 | 2 | 6 | A pace-only legacy backlog config migrated floorless; the pace-0 write fired after the legacy floor lost to a block |
| 10 | 0 | 2 | 6 | Queue window still changed when a source's 60 met a stale fallback; top-level default outside the accuracy net (recorded above) |

The loop exited at the cap, not on a severity-clean round: round 10 carried one shared MEDIUM, so the
marker is `UNRESOLVED` rather than `CLEAN`. No HIGH finding was reported in any round.
