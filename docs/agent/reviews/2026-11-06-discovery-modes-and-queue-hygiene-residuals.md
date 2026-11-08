# Residuals — discovery modes and queue hygiene

Cap round reached, so the loop ends here with the findings below recorded.

| Field | Value |
| --- | --- |
| Branch | `main` (uncommitted working tree) |
| `BASE_REF` | `origin/main` (`0a5c742`, v3.3.0) |
| Commit | working tree (`d384c94` + review fixes) |
| Round reached | 10 |
| Cap (`CIRCUIT_BREAKER_ROUNDS`) | 10 |
| Reviewer models | `opencode-go/deepseek-v4.1-flash:max` (A), `opencode-go/glm-5.3-flash:max` (B) |

## Findings

Every finding the cap round reported, all severities.

| Severity | `file:line` | Description | Status |
| --- | --- | --- | --- |
| MEDIUM | `src/gamarr/pipeline.py` (`_run_discovery_phases`) | A lapsed pending row blocked same-cycle re-queue: `get_known_slugs` omitted it, but `record_pending` still saw the row via its primary key, so browse wasted a detail lookup and the game re-queued one cycle late. Both reviewers reported this (A1, B1). | applied, not independently re-reviewed — the expiry sweep now runs at the top of the cycle, before the browse phase; end-to-end test `TestLapsedRowRequeueSameCycle` (RED-verified against the old ordering) |
| MEDIUM | `src/gamarr/database.py` (`_migrate_pending_queue_hygiene`) | Mixed legacy shape (stale browse-scale `metascore` such as `1998.0` **plus** a real `user_score`) was false on both adoption branches, so a genuinely verified row was deleted by the one-time cleanup. (A2) | applied, not independently re-reviewed — adoption predicate widened to `metascore BETWEEN 0 AND 100 OR user_score BETWEEN 0 AND 10`; test `TestHygieneKeepsMixedShapeRows` (RED-verified) |
| LOW | `src/gamarr/scheduler.py` (`_matching_window_days`) | A **disabled** FitGirl entry still won the matching window over an enabled source, contradicting the docstring and the match phase's own `enabled` filter. (A3, B4) | applied, not independently re-reviewed — disabled entries skipped; test `TestMatchingWindowDisabledFitgirl` |
| LOW | `src/gamarr/pipeline.py` (`_is_fitgirl_url`) | `url.startswith("https://fitgirl-repacks.site")` also accepted `https://fitgirl-repacks.site.<attacker>/…`, which is then fetched with TLS verification disabled. (A5) | applied, not independently re-reviewed — exact parsed-host comparison in one helper used by both call sites; tests `TestFitgirlUrlHostCheck` pin the lookalike case |
| LOW | `src/gamarr/pipeline.py` (`_should_process_by_age`) | Dead helper (no production caller) reading the removed `age_recheck_weeks` knob. (B2) | applied, not independently re-reviewed — deleted with its test class |
| LOW | `src/gamarr/pipeline.py`, `src/gamarr/config.py` | `max_queue_days` is threaded into `run_acquisition`/`AcquisitionConfig` but never read; the effective window flows through `fitgirl_max_queue_days`. A misleading knob. (B3) | not fixed — kept deliberately as the documented last-resort input (`_matching_window_days` returns it when no source sets a window); removing it would change the public `run_acquisition` signature |
| LOW | `src/gamarr/database.py`, `src/gamarr/pipeline.py` | The expire → re-queue flap appends one `Expired` history row per game per window with no brake, because `_process_aged_games` is inert (`age_recheck_weeks` no longer exists in the config). (A4) | not fixed — accepted design: `pending_games` stays bounded (PK on slug), re-discovery is intentional, and the dead age path is pre-existing rather than introduced by this change |

### Reported as residuals by the reviewers, recorded here

| Severity | `file:line` | Description | Status |
| --- | --- | --- | --- |
| LOW | `src/gamarr/database.py` (`is_pending`) | `is_pending` still counts lapsed rows, unlike `get_pending`/`get_known_slugs`. Currently unreachable in the production browse flow (the known set is always supplied), but the predicates disagree. | not fixed — no production caller reaches it; documented for whoever adds one |
| LOW | `src/gamarr/database.py` (`get_known_slugs`) | The history query filters on `result` with no index, so it is a full scan per browse phase. | not fixed — acceptable at realistic history sizes |
| LOW | `src/gamarr/pipeline.py` (`_latest_call_depth`) | `max_pages: 1` in latest mode scans only the current year (the reserve is dropped). | not fixed — documented on the helper ("`max_pages` should be >= 2"); scanning nothing would be worse |
| LOW | `src/gamarr/pipeline.py` (`_reject_scan_text`) | A features bullet spelled with a canonical stop heading (`"Included DLCs: …"`) truncates the block, so an HV marker listed after it is not scanned. | not fixed — documented as a known limit on the extractor; a boundary rule is not viable because FitGirl's collapsed text puts real headings straight after a word |

## Verification gap

The fixes in the table above were applied **after** the last reviewer pass, so they were
**never independently re-reviewed**. Each behavioural fix was verified locally by the
supervisor: a RED run that fails against the previous behaviour, then a GREEN run, followed
by the full suite (`1230+ passed`, run twice), `ruff check`, `ruff format`, `mypy`, and the
CRAP report (no function at or above 9.0).
