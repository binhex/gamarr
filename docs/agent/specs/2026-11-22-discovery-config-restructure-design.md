# Design: per-mode discovery config

Date: 2026-11-22
Status: Approved (grilled in this session; decisions recorded in ADR 0001-0007)

## Goal

Make the discovery config answer "what will this cycle do?" from the file alone. Three complaints
drove it: the same seven keys mean different things per mode, keys silently do nothing, and two of
the names (`max_pages`, `max_date`) do not say what they bound.

The change splits the flat discovery keys into a `latest` block and a `backlog` block, renames each
key after the unit it works in, removes the page-depth fallback from `latest`, and migrates every
existing config on load.

## What was measured

- **The live config** (`review_sites.metacritic.platform_overrides.pc`) sets `mode: latest`,
  `max_date: 202601`, `max_cycle_months: 1`, `max_pages: 1000`, `max_cycle_pages: 16`,
  `sort_order: new`, `max_queue_days: 30`. Four of the six numeric keys are inert: the two page keys
  because `max_date`/`max_cycle_months` make the cycle date-driven, and the metacritic
  `max_queue_days` because both configured sources set their own window.
- **Three `max_queue_days` keys** exist in one file (fitgirl, freegog, metacritic), and the
  metacritic one is a documented fallback that only applies when no source sets a window.
- **The `max_pages` README row is about 90 words** because it must describe four cases: backlog page
  mode, backlog date mode, latest depth mode, latest date mode.
- **Two contradictory page-to-year figures** exist in the repo: `pipeline.py` derives the score-sort
  year window as `max_pages / 52` (line 670), while the July spec measured roughly 24 games per
  browse page (about 24 pages per month). They disagree by about 5.5x.

## Target shape

```yaml
source_matching_days: 60          # default for the whole queue; a source may override

download_sites:
  - fitgirl:
      enabled: true
      source_matching_days: 90    # optional override

review_sites:
  metacritic:
    pending_order: new            # orders the pending queue, both modes
    platform_overrides:
      pc:
        discovery:
          mode: latest            # the only selector
          latest:                 # month-paced; no page keys exist here
            oldest_month: 202601
            months_per_cycle: 1
          backlog:                # ignored, with one INFO line, when mode is latest
            sort_order: score     # selects one of the two blocks below
            new:
              oldest_month: 202001
              months_per_cycle: 0
            score:
              page_depth: 500
              pages_per_cycle: 0
```

The live config above would be written as `mode: latest`, `latest: {oldest_month: 202601,
months_per_cycle: 1}` and a `backlog` block it never reads; the two page keys are dropped by
migration with an INFO line because they were already inert.

What the shape guarantees:

- Every key in the selected block is read. An inert key cannot exist inside a selected block, so the
  only inert keys in a file sit in a block kept for a later switch, and each is reported once per
  load.
- An absent or empty selected block is filled from the model defaults, so `mode: latest` alone is a
  valid config.
- The block for the non-selected mode, and the non-selected sort block inside `backlog`, may stay in
  the file, so switching mode or sort is a one-line edit. Both follow the same reporting rule.

## Key mapping

| Legacy key (flat, per platform) | New key | New location |
| --- | --- | --- |
| `mode` | `mode` | `discovery.mode` |
| `max_date` | `oldest_month` | `discovery.latest`, or `discovery.backlog.new` |
| `max_cycle_months` | `months_per_cycle` | same block as the floor |
| `max_pages` | `page_depth` | `discovery.backlog.score` |
| `max_cycle_pages` | `pages_per_cycle` | `discovery.backlog.score` |
| `sort_order` | `pending_order` | `review_sites.metacritic.pending_order` |
| `sort_order` | `sort_order` | `discovery.backlog.sort_order` |
| `metacritic.max_queue_days` | `source_matching_days` | top level, beside `download_sites` |
| `download_sites.<src>.max_queue_days` | `source_matching_days` | inside the same source entry |

## Defaults

| Key | Default | Meaning of 0 |
| --- | --- | --- |
| `mode` | `latest` | n/a |
| `latest.oldest_month` | `0` | no floor: read back `months_per_cycle` months from the newest release |
| `latest.months_per_cycle` | `1` | read to the floor (whole window), warned once per cycle |
| `backlog.sort_order` | `new` | n/a |
| `backlog.new.oldest_month` | `0` | no floor: sweep as far back as the listing goes (1958 clamp) |
| `backlog.new.months_per_cycle` | `0` | no pacing: one cycle covers the whole sweep |
| `backlog.score.page_depth` | `500` | no page budget: sweep to the earliest year |
| `backlog.score.pages_per_cycle` | `0` | unlimited pages per cycle |
| `pending_order` | `new` | n/a |
| `source_matching_days` | `60` | never expire a queued game |

## Validation

- `discovery` and its blocks set `model_config = ConfigDict(extra="forbid")`. An old key name
  written inside a new block is an unknown-key error; there are no permanent aliases (ADR 0002).
- `oldest_month` keeps today's `max_date` validator: `0` or `YYYYMM` with month 01-12 and year at or
  after `EARLIEST_BROWSE_YEAR`; a future month is clamped to the current month with a warning.
- `months_per_cycle` is `0..1200` (the existing sanity cap) in both `latest` and `backlog.new`.
- `page_depth` and `pages_per_cycle` are `>= 0`; `source_matching_days` is `>= 0`.
- `mode` is `latest | backlog`; `backlog.sort_order` is `new | criticscore | userscore` and selects
  `backlog.new` or `backlog.score`. `criticscore` and `userscore` share `score`.
- `months_per_cycle: 0` is legal in `latest` and means "read to the floor", matching today's
  behaviour; a floorless unpaced cycle warns as it does now.

## Migration

A new `_migrate_discovery_blocks(raw)` step is appended to the `_migrate_config` list, after
`_migrate_platform_overrides`, after the `download_sites` consolidation steps, and after
`_migrate_sort_order_critic_names` so a legacy `metascore` value is already `criticscore`. A second
step, `_migrate_source_matching_days`, performs the two `max_queue_days` renames. Both follow the
existing contract: mutate `raw` in place, return whether anything changed, log every action, and rely
on `_migrate_config`'s deepcopy rollback if a step raises.

Rules, in order, for each platform override that still holds flat discovery keys:

1. Read `mode` (default `backlog`, today's default) and `sort_order` (default `new`).
2. `max_date` becomes `oldest_month` in the target block (`latest`, or `backlog.new`).
3. `max_cycle_months` becomes `months_per_cycle` in the same block.
4. `max_pages`:
   - `mode: latest` - becomes `months_per_cycle` when that key is unset, via `pages_to_months`;
     otherwise dropped with INFO (it was inert).
   - `mode: backlog` and `sort_order: new` - becomes `oldest_month` when that key is unset, derived as
     `subtract_months(current_year_month(), pages_to_months(max_pages))`; otherwise dropped with INFO.
     The reference point is load time, not the newest release, because no listing is fetched during a
     config load; the derived floor is therefore an approximation and is logged as one.
   - `mode: backlog` and a score sort - becomes `backlog.score.page_depth` unchanged.
5. `max_cycle_pages`:
   - `mode: latest` - dropped with INFO (never applied).
   - `mode: backlog` and `sort_order: new` - becomes `months_per_cycle` when unset, via
     `pages_to_months`; otherwise dropped with INFO.
   - `mode: backlog` and a score sort - becomes `backlog.score.pages_per_cycle` unchanged.
6. `sort_order` is copied to `backlog.sort_order` and `review_sites.metacritic.pending_order`.
7. `metacritic.max_queue_days` becomes the top-level `source_matching_days` when that key is unset;
   otherwise it is dropped with INFO (it was unreachable).
8. A block that already exists wins: for every key above, if the new location already holds a value,
   the legacy key is dropped and one INFO line reports the ignored legacy key.
9. Date keys migrate into `backlog.new` even when the configured sort is a score sort, so a later
   switch to `new` finds them. They were ignored under the score sort before and are reported as
   inert, like any other key in a block the active sort does not read.

`pages_to_months(pages)` is one helper in `utils.py`: `max(1, ceil(pages / 24))`, with 24 as a named
module constant used by both the migrations and the year-window derivation. Rounding up never
narrows a converted bound and never produces a 0-month pace.

`_CONFIG_VERSION` moves from `1.58.0` to `1.59.0`. Migrations are idempotent: a second load finds no
flat keys and changes nothing.

## Pipeline changes

Today the mode resolution is spread across `_date_driven`, `_normalise_latest_depth`,
`_latest_call_depth` and six ad-hoc "Config: ... is unused" branches. Replace it with one resolution
step that builds an immutable scan plan from `discovery`:

| Field | latest | backlog |
| --- | --- | --- |
| `scan_order` | always `new` | the configured `sort_order` |
| `date_mode` | always true | true only for `new` |
| `floor_month` | `latest.oldest_month` | `backlog.new.oldest_month` |
| `pace_months` | `latest.months_per_cycle` | `backlog.new.months_per_cycle` |
| `page_depth` | none | `backlog.score.page_depth` |
| `pages_per_cycle` | none | `backlog.score.pages_per_cycle` |

Consequences:

- `_latest_call_depth` and `_normalise_latest_depth` are deleted with their tests; latest has no
  page depth, so the "reserve one page for the lower year" hack and the "`max_pages: 1` scans only
  the current year" residual both disappear.
- The `sort_order` warn-and-coerce branch is deleted; latest cannot hold a score sort.
- The year-window derivation at `pipeline.py:670` uses `pages_to_months` instead of `/ 52`.
- The inert-key reporting is one helper: any key present in the non-selected block is named once per
  load, replacing the six per-branch messages.
- `AcquisitionConfig` and `run_acquisition` keyword arguments take the new names, and the dead
  `max_queue_days` and `age_recheck_weeks` parameters are removed.
- `pending_order` is read where the pending queue is ordered, not from `sort_order`.

## Scheduler changes

`_matching_window_days` resolves in this order and nothing else: an enabled FitGirl entry's
`source_matching_days`, then the first enabled source's `source_matching_days`, then the top-level
`source_matching_days`. The metacritic fallback branch is deleted; the root default of 60 is the
replacement for its only real job. `_build_kwargs` passes the new names.

## Documentation

- `README.md`: replace the seven-key table with rows grouped under the mode that reads them, add a
  worked `latest` and `backlog` example, state that a page is about a day of PC releases (24 pages
  per month), and remove every mention of "date-driven latest" and the warn-and-coerce rule.
- Update the features list, the flow diagram's cutoff decision, and the troubleshooting bullets that
  reference `max_pages` or `max_date`.
- `tests/unit/test_readme_accuracy.py` is repointed at the new key names and the new default table.

## Testing

- **Model:** defaults, block selection, `extra="forbid"` rejection of legacy names inside blocks,
  `oldest_month` validation including the future clamp and the 1958 floor, the 1200 cap.
- **Migration:** a table-driven case per row above (mode latest; mode backlog with `new`; mode
  backlog with a score sort; `max_pages` alone; `max_cycle_pages` alone; both shapes present;
  `metacritic.max_queue_days` reachable and unreachable; per-source rename), plus idempotency on a
  second load and preservation of unknown keys.
- **Pipeline:** latest floor-only, pace-only, pace narrower than the floor, pace `0` (read to the
  floor, warned), floorless; backlog `new` with no floor; backlog score with a budget, with `0`, and
  with a per-cycle pace; `pending_order` independent of `sort_order`; one INFO per inert key.
- **Scheduler:** window resolution for fitgirl present, disabled fitgirl, first enabled non-fitgirl
  source, and no sources at all.
- **No new network paths.** Everything above is config resolution and scan planning.

## Out of scope

- Scraping, verification, delivery, and post-processing behaviour.
- The open residuals in `2026-11-10-latest-date-driven-residuals.md` - the undated listing that
  also hits the client's 2000-page cap, and the mis-indexed `floor_stop` pin - stay open.
- Date support for the score-ordered lists.
- Reaching back further than `page_depth` implies in a score-sorted sweep.

## Settled decisions

| Decision | Rejected alternative | Reference |
| --- | --- | --- |
| Per-mode `latest`/`backlog` blocks; `mode` stays the selector; non-selected block ignored with INFO | Flat keys plus strict validation; prefixed flat keys | ADR 0001 |
| Legacy flat configs migrate on load | Refuse to start; read both shapes forever | ADR 0001 |
| Rename keys after their unit, with no aliases | Keep the `max_*` names; permanent aliases | ADR 0002 |
| `latest` is month-paced only | Keep the page-depth fallback | ADR 0003 |
| One `source_matching_days`, per-source override, metacritic fallback deleted | Delete only the fallback; leave all three | ADR 0004 |
| `backlog` splits by sort; both score sorts share one `score` block | One block with a sort-dependent pair; drop score sorts | ADR 0005 |
| `pending_order` is a separate, shared key | `sort_order` keeps both jobs; queue always newest-first | ADR 0006 |
| Every legacy page count converts to months, rounded up, minimum 1 | Fail loudly; convert only in `latest` | ADR 0007 |
| One constant, 24 pages per month, for conversions and year windows | Keep `max_pages / 52`; no year derivation | ADR 0007 |
| Default mode `latest`; absent or empty block takes model defaults | Keep `backlog`; require `mode`; error on a missing block | ADR 0001 |
| Glossary terms: window floor, pace, page depth, sort order, pending order, inert key | - | `docs/agent/glossary.md` |

## Decisions made while writing the spec

- Python keyword arguments on `AcquisitionConfig` and `run_acquisition` take the new names. Keeping
  the old names would create the second vocabulary ADR 0002 rejects, and the facade has no external
  callers in the repo.
- The non-selected sort block is reported like the non-selected mode block, rather than dropped,
  because both are switch positions a user returns to with one key.
- `extra="forbid"` on the new models, since silent typos are the failure this design exists to
  prevent.
- One resolution step replaces the six per-branch warnings, because each branch described a
  combination the new shape cannot express.
- The dead `max_queue_days` and `age_recheck_weeks` parameters are removed rather than carried.
- `_CONFIG_VERSION` bumps to `1.59.0`; a minor package bump follows at release.
