# max_date backlog cutoff — review residuals (round-cap)

Branch: `main`
Commit reviewed: `3925f82` plus the uncommitted working-tree change (base `origin/main`, `1f09be0`)
Round reached: **10 of 10** (`CIRCUIT_BREAKER_ROUNDS`)
Reviewers: `opencode-go/deepseek-v4.1-flash:max` (A), `opencode-go/glm-5.3-flash:max` (B)
Marker emitted: `REVIEW_GATE_STATUS: UNRESOLVED` (completion with residuals — downstream steps may proceed)

## Why this ledger exists

Round 10 was the last round the loop allows. It reported findings, so the fixes below were
applied after the final reviewer pass and were **not** re-reviewed. Reviewer B reported no
findings at any severity in round 10; Reviewer A reported the four items recorded here.

## Findings from round 10

| # | Severity | Location | Description | Status |
| --- | ---------- | ---------- | ------------- | -------- |
| 1 | MEDIUM | `src/gamarr/pipeline.py:521-530` | A floor year whose listing has no data at all starts at page 1, so the client can never report `_recent_games_exhausted` (`page_number > 1`). `floor_year_finished` therefore never fired for that shape, and date-mode completion fell back to a later cycle's all-empty re-probe — with `max_cycle_months: 0` the whole range was re-swept every second cycle and "Backlog complete" never logged on the cycle that reached the floor. | applied, not independently re-reviewed — `empty_floor_year` now counts a clean zero-page floor year as finished; pinned by `test_data_free_floor_year_still_completes_the_sweep` (RED-verified before the ledger was written) |
| 2 | LOW | `src/gamarr/metacritic.py:623-636` | `_classify_page` set `_recent_games_exhausted` before the cancellation check, so a shutdown that landed on an empty page skipped the confirming re-probe and still recorded an unconfirmed drain — contradicting the documented "twice in a row" invariant. Harmless only because every current reader gates on `is_cancelled`. | applied, not independently re-reviewed — the cancel branch now runs before the empty-page branch; pinned by `test_cancelled_empty_page_is_not_recorded_as_a_drain` (RED-verified) |
| 3 | LOW | `src/gamarr/pipeline.py:613-617` | The comment above the scrape-health probe claimed the full `_cache_scope_matches` rule while only its emptiness half is applied; a non-empty, scope-mismatched row would still count as cached browse data (unreachable today because the probed keys cannot hold such a row). | applied, not independently re-reviewed — comment narrowed to the rule actually applied |
| 4 | LOW | `src/gamarr/metacritic.py:902-906` | The `max_month_span` docstring said "first page month", but the anchor is the newest month of the first *dated* page (page 1 may be entirely undated). | applied, not independently re-reviewed — docstring now says "first page that carries any dated game" |

## Verification gap

Round 10's fixes were never re-reviewed. What *was* verified after them, in the same session:

- `uv run pytest -q` → **1126 passed** (exit 0)
- `uv run ruff check .` → all checks passed; `uv run ruff format .` → no changes
- `uv run mypy .` → success, no issues in 39 source files
- Each behavioural fix was RED-checked by reverting it and observing the specific new test fail,
  then restoring it and observing it pass.

Not re-verified by a reviewer after the cap: the interaction of `empty_floor_year` with
`fetch_failed`, `range_empty` and `cutoff_stopped`; the reordered cancellation branch's effect on
the page-mode drained reset.

## Review history summary

| Round | HIGH | MEDIUM | LOW | Notable outcome |
| ------- | ------ | -------- | ----- | ----------------- |
| 1 | 2 | 4 | 5 | Pipeline sent a renamed kwarg the client did not accept (silent total discovery loss); a cutoff stop could wipe progress mid-sweep |
| 2 | 1 | 4 | 4 | `max_cycle_months: 0` produced an accidental pace cutoff; page-ceiling exhaustion unreachable for a January floor |
| 3 | 1 | 2 | 3 | Pace stops inside the floor year were read as sweep completion |
| 4 | 0 | 4 | 6 | Mid-year fetch failure not propagated; two round-3 test claims were vacuous |
| 5 | 0 | 3 | 5 | Floor-year completion deferred when the year was resumed mid-listing |
| 6 | 0 | 2 | 8 | Fresh drain never reported exhaustion (every-other-cycle sweeps); boundary page could queue below-floor games |
| 7 | 0 | 4 | 5 | Transient empty page read as a drain; pace-stop test asserted an unreachable state |
| 8 | 0 | 4 | 5 | Stale empty cached row made the drain confirmation a phantom; unparsable span discarded the floor |
| 9 | 0 | 2 | 6 | Scrape-health probe trusted an empty cached row; cancellation gate on the completion reset untested |
| 10 | 0 | 1 | 3 | Data-free floor year never completed (recorded above) |

Loop exited at the cap, not on a severity-clean round: round 10 carried one MEDIUM, so the
marker is `UNRESOLVED` rather than `CLEAN`.
