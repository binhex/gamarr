# Discovery modes and queue hygiene — review residuals (open rows)

Reduced by the finalising run on 2026-11-23: `is_pending`, the unindexed `get_known_slugs` scan and the unbraked `Expired` rows were fixed; `max_queue_days` and `_latest_call_depth` no longer exist after the per-mode discovery restructure.

| Severity | Location | Description | Status |
| --- | --- | --- | --- |
| LOW | `src/gamarr/pipeline.py` (`_reject_scan_text`) | A features bullet spelled with a canonical stop heading (`"Included DLCs: ..."`) truncates the block, so an HV marker listed after it is not scanned. | **not fixed** — accepted limit of the extractor; a boundary rule is not viable with FitGirl's collapsed text |
