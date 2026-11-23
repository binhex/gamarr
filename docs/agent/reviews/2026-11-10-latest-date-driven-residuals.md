# Latest mode date-driven — review residuals (open rows)

Reduced by the finalising run on 2026-11-23: rows 1-9 were applied, and row 11 (missing latest-mode pin) is now pinned by `test_a_latest_floor_level_stop_above_the_floor_year_still_descends`.

| Severity | Location | Description | Status |
| --- | --- | --- | --- |
| LOW | `src/gamarr/pipeline.py` (`undated_listing` guard) | A listing that is undated **and** hits the client's internal 2000-page cap does not set the drain flag, so the latch misses it and the descent continues; cost is bounded (bound x 2000 pages, then the acquisition watchdog) and the post-loop warning still reports it. | **not fixed** — accepted limit; needs a client-side "hard cap reached" flag |
