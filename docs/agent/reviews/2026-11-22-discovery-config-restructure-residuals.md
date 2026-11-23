# Per-mode discovery config — review residuals (open rows)

Reduced by the finalising run on 2026-11-23: every other row from the round-10 cap was verified resolved.

| Severity | Location | Description | Status |
| --- | --- | --- | --- |
| LOW | `src/gamarr/pipeline.py` (`_run_discovery_phases`) | A config that sets only `discovery.mode: backlog` uses the model defaults (`oldest_month: 0`, `months_per_cycle: 0`), so one cycle walks the whole catalogue per year listing, bounded only by the acquisition watchdog. | **not fixed** — accepted limit; the "no floor and no month pace — one cycle walks the whole catalogue" warning names the cost |
