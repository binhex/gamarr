# Latest mode date-driven — review residuals (round-cap)

Branch: `main`
Commit reviewed: uncommitted working tree (`BASE_REF=HEAD`, `4d8b745` v3.4.1)
Round reached: **10 of 10** (`CIRCUIT_BREAKER_ROUNDS`)
Reviewers: `opencode-go/deepseek-v4.1-flash:max` (A), `opencode-go/glm-5.3-flash:max` (B)
Marker emitted: `REVIEW_GATE_STATUS: UNRESOLVED` (completion with residuals — downstream steps may proceed)

## What was reviewed

The change makes `latest` mode date-driven: `max_date` is the floor and `max_cycle_months` the
per-cycle pace, `max_pages` becomes the fallback used only when neither date key is set, latest
still never reads or writes cursors, and backlog semantics are unchanged. Files: `README.md`,
`docs/agent/specs/2026-11-06-discovery-modes-and-queue-hygiene-design.md`,
`docs/agent/plans/2026-11-06-discovery-modes-and-queue-hygiene.md`, `src/gamarr/config.py`,
`src/gamarr/pipeline.py`, `src/gamarr/utils.py`, `tests/unit/test_pipeline.py`.

## Why this ledger exists

Round 10 was the last round the loop allows. It reported **no HIGH and no MEDIUM**, only LOW
findings. Per the round-cap rule every cap-round finding is recorded here whether or not it was
fixed, and the fixes below were applied **after** the final reviewer pass, so they were **not**
independently re-reviewed.

## Findings from round 10

| # | Severity | Location | Description | Status |
| --- | ---------- | ---------- | ------------- | -------- |
| 1 | LOW | `README.md:32-34`, `README.md:62-65` | Two feature bullets promised games are re-checked "immediately in `latest` mode", which the new narrow-pace scope makes false (a game older than the pace window is only re-checked in backlog). (A) | applied, not independently re-reviewed — both bullets now say "every cycle inside the `latest` window" |
| 2 | LOW | `src/gamarr/pipeline.py` (`undated_listing` comment) | The comment claimed a dated year below an undated one is "unaffected", but the latch is never cleared, so the descent does stop. (A) | applied, not independently re-reviewed — comment now states the latch is never cleared and records the cost as accepted |
| 3 | LOW | `src/gamarr/pipeline.py` (effectively-unpaced warning) | The warning fired for a one-month window (nothing to re-read), and for the floorless already-paced case advised "set max_cycle_months" when the key was already set. (A, B) | applied, not independently re-reviewed — now requires a non-zero floor span and says "lower max_cycle_months" when a pace is set |
| 4 | LOW | `src/gamarr/pipeline.py` (narrow-pace INFO) | The INFO compared the pace against today's month rather than the cycle anchor, so it could assert a scope that was not actually read. (A) | applied, not independently re-reviewed — reworded as a statement about the configured pace, not the read window |
| 5 | LOW | `src/gamarr/pipeline.py` (no-dates warning comment) | The comment said "the listing depth alone stopped the scan", but date mode always passes `max_pages=0`, so the scan stopped on a drain. (A) | applied, not independently re-reviewed — comment corrected |
| 6 | LOW | `tests/unit/test_pipeline.py` (`test_a_floor_covering_pace_is_not_called_unpaced`) | Fixed floor `201001` with pace `240` goes silent from 2030-02: the span outgrows the pace, the other branch runs, and the negative assertion keeps passing without exercising its branch. (A) | applied, not independently re-reviewed — floor now derived from the clock, plus an assertion that the narrow-pace INFO is absent |
| 7 | LOW | `tests/unit/test_pipeline.py` (`test_a_pace_shortens_the_window_without_crossing_the_floor`) | Name said "wider than the floor" for a 24-month pace against a ~83-month window, and `min(years) >= 2020` was vacuous. (A) | applied, not independently re-reviewed — renamed (renaming only; the floor-crossing rule is pinned by `test_the_year_bound_is_the_floor_not_a_derived_bound`) |
| 8 | LOW | `tests/unit/test_pipeline.py` (unpaced-warning tests) | The round-9 claim that the warning "names the real bound" was not pinned. (A) | applied, not independently re-reviewed — the floor (`202401`) and the floorless bound (`1958`) are now asserted in the message |
| 9 | LOW | `src/gamarr/pipeline.py` (`_floorless_latest_bound`) | The docstring precondition "always positive here" was violated by an eager call with `0` for non-date cycles. (A) | applied, not independently re-reviewed — the call is now guarded by `bool(cfg.max_cycle_months)` |
| 10 | LOW | `src/gamarr/pipeline.py` (`undated_listing` guard) | A listing that is undated **and** hits the client's internal 2000-page cap does not set the drain flag, so the latch misses it and the descent continues; cost is bounded (bound × 2000 pages, then the acquisition watchdog) and the post-loop warning still reports it. (B) | not fixed — documented in the code comment; detecting it needs a client-side "hard cap reached" flag, which is outside this change |
| 11 | LOW | `tests/unit/test_pipeline.py` | No latest-mode test pins the mis-indexed carve-out (`floor_stop` reported in a non-floor year → descend instead of break). The shared branch is pinned for backlog only. (B) | not fixed — optional pin; the carve-out is shared date-mode logic already covered on the backlog side |

## Verification gap

The fixes above were applied **after** the last reviewer pass, so they were **never independently
re-reviewed**. What was verified after them, in the same session:

- `uv run pytest --cov=gamarr --cov-fail-under=80` → **1260 passed**, coverage 96.90% (exit 0)
- `uv run ruff check .` → all checks passed; `uv run ruff format .` → no changes
- `uv run mypy .` → success, no issues in 39 source files
- `uv run pre-commit run --all-files` → every hook passed (ruff, ruff-format, mypy, autoflake)
- `markdownlint` under the project rule set (120 chars, tables exempt) → clean for `README.md`
  and the design spec. The plan file still has 24 pre-existing long lines; none of them is a
  line this change added.
- Every behavioural fix in the loop was RED-verified by reverting it and observing the specific
  test fail, then restoring it (9 such checks across the rounds, including the round-8 ordering
  HIGH).

Not re-verified by a reviewer after the cap: the round-10 warning rewording (items 3, 4) against
every config shape, the clock-derived floor in item 6, and the `bool(...)` guard in item 9.

## Review history summary

| Round | HIGH | MEDIUM | LOW | Notable outcome |
| ------- | ------ | -------- | ----- | ----------------- |
| 1 | 0 | 4 | 4 | Date-driven latest wired; pace bound derived from today (later found lossy); `max_pages` cap removal made non-silent |
| 2 | 0 | 4 | 7 | Pace bound padded; year-set test made rollover-proof; unpaced warning added |
| 3 | 1 | 2 | 3 | HIGH: floored+paced bound skipped a stale anchor's whole pace window → bound is now the floor, never today-derived |
| 4 | 0 | 3 | 8 | Legacy `max_pages` cap loss documented; floorless truncation reported; undated warning tested |
| 5 | 0 | 4 | 4 | One failed year no longer abandons a cycle (two-strike outage stop); undated drained listing stops the descent; 1958 clamp tested |
| 6 | 0 | 2 | 6 | Outage stop no longer switched off by the first dated page; summary-line pin; floor-span unpaced case |
| 7 | 0 | 1 | 9 | Exception path advances the fail streak (tested); floorless bound widened for anchor lag |
| 8 | 1 | 0 | 3 | HIGH: undated guard read `max_month_seen` before the month tracking, so the first drained year truncated the window — guard moved after tracking |
| 9 | 0 | 3 | 4 | Unpaced warning split from the narrow-pace scope INFO; positive `max_pages` cap pinned; three clock-bound tests fixed |
| 10 | 0 | 0 | 9 | Cap round: LOW only (see table above) |

The loop exited at the cap, not on a severity-clean round: round 10 carried LOW findings, so the
marker is `UNRESOLVED` rather than `CLEAN`. No HIGH or MEDIUM finding was open at the cap.
