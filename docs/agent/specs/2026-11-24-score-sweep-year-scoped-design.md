# Design: year-scoped score sweep

Date: 2026-11-24
Status: Draft for review
Supersedes: the `page_depth` half of ADR 0005's score block; extends ADR 0007 with a measured
constant.

## Goal

A score-sorted backlog could only state how far it reaches as a **page count** (`page_depth`),
because a score-ordered listing mixes release years. That number is not a reach: measured against
the live PC index (870 pages, 43,500 games, Nov 2024 - Nov 2026) the catalogue releases **33.4 pages
per month**, rising to 52 in the last quarter, so any fixed budget means a different date span every
month and roughly 2,200 pages are needed to reach 2016 today, ~400 more each year.

The operator's actual request is: *sweep back to 2016, highest user score first, a bounded amount of
work per cycle*. That is inexpressible today - a date floor is ignored by a score sort, and a page
budget cannot say "2016".

This design makes the score sweep **year-scoped**: it walks release years newest to oldest, sorted by
score inside each year, bounded by a date floor and paced by pages. Reach becomes a value stated
once; pages become a workload unit that never needs re-tuning. Metacritic exposes
`releaseYearMin`/`releaseYearMax` and accepts a score-sort slug on the same request, so this is one
request shape the index already serves (verified live for `userscore` on 2025 and 2016).

## Settled decisions

Carried in from the grill (ADR 0008, ADR 0009, glossary). Each row names the alternative that was
rejected, so this spec stands alone in a new session.

| # | Decision | Rejected alternative | Record |
| - | -------- | -------------------- | ------ |
| 1 | A score-sorted backlog walks **release years newest to oldest**, score-sorted inside each year | Keep the all-time score sweep; pace in games instead of pages | ADR 0008 |
| 2 | Reach is `oldest_month`, read at **year grain** (the index filters by whole year, so a mid-year value reads the whole of that year) | A new `oldest_year` key; accepting either key | ADR 0008, glossary: Window floor |
| 3 | `page_depth` is **removed**; the floor is the only reach bound and `pages_per_cycle` is the only page key | Keep `page_depth` as an optional safety cap | ADR 0008, glossary: Pace |
| 4 | Page-boundary drift is **accepted and documented**: dedup prevents duplicate work, a cursor reset re-covers a skipped title | No cursor for the current year (the newest year would stall the sweep) | ADR 0008 |
| 5 | Reaching the floor **restarts from the newest year** | Hold at the floor and stop | ADR 0008 |
| 6 | Legacy `page_depth` **migrates to an equivalent floor** | Drop it and write no floor; make it a load error | ADR 0007, ADR 0009 |
| 7 | `pages_per_cycle: 0` **coerces to 20** with an INFO line | Reject 0; keep 0 = unpaced | ADR 0008 |
| 8 | `PAGES_PER_MONTH` is **33** (measured), drift documented as intrinsic | 52 (today's rate); leave 24 | ADR 0009 |
| 9 | Titles with no release date are **outside the sweep** (a year filter cannot return them) | An all-time catch-all pass after the floor | ADR 0008 |

Unchanged by this design: `latest` mode (ADR 0003), `backlog.new` (month-paced, date floor),
`sort_order` still selects the block (ADR 0005), `pending_order` (ADR 0006), the all-time listing
itself (no longer read by discovery).

## Config surface

Before:

```yaml
discovery:
  mode: backlog
  backlog:
    sort_order: criticscore
    score:
      page_depth: 500        # total page budget, no date meaning
      pages_per_cycle: 20
```

After:

```yaml
discovery:
  mode: backlog
  backlog:
    sort_order: criticscore
    score:
      oldest_month: 201601   # reach: nothing older than 2016 (read at year grain)
      pages_per_cycle: 20    # workload per cycle, never re-tuned
```

`backlog.score` now holds exactly the same two ideas as `backlog.new`: a floor and a pace. Only the
pace unit differs (pages, because a score-sorted sweep's cost is requests, not calendar months).

## Behaviour

**Traversal.** One cycle walks `scan_year` from the current calendar year down to
`floor_month // 100` (floor `0` = the earliest browse year), one year at a time. The newest year is
read first and is drained before the next year down is touched, so recent high-score titles are
verified and queued earliest.

**Requests.** Each year is requested as the year-scoped listing with the configured score slug
(`.../browse/game/pc/all/<year>/userscore/?releaseYearMin=<year>&releaseYearMax=<year>&page=<n>`).
`year` is now passed for every sort, not only `new`.

**Pace.** `pages_per_cycle` is one budget for the whole cycle, spent across year boundaries; a cycle
may end mid-year and its cursor keeps the page. A fetched page counts whether or not it yields a
game. There is no total budget.

**Cursors.** `backlog_progress(platform, year)` records the last page reached in each year, exactly
as the date-sorted sweep uses it; the sweep resumes there next cycle. All years' cursors for the
sort are wiped on completion (existing `reset_progress`), so the next pass starts at page 1 of the
newest year.

**Completion.** The sweep is complete when the floor year's listing is exhausted (an empty probe the
client confirmed as drained) or an empty floor year was probed, and no fetch failed and the cycle was
not cancelled. Completion logs `Backlog complete — reached <floor year> (floor <year>), restarting
from the newest year` and resets the cursors. A fetch failure never completes a sweep.

**Year grain.** A floor of `201601` reads all of 2016 and is not filtered inside that year. This
differs from the date-sorted branch, which drops games older than `oldest_month` from the floor
month's pages; the score sweep applies no per-game floor filter at all.

**Drift.** A score-sorted page shifts when a title is added above the cursor, so a boundary page can
skip or repeat a title between cycles. The processed-games table prevents duplicate work, and
re-running the sweep from a reset cursor re-covers a skipped title. There is no compensation code.

**Undated titles.** A year filter cannot return a title with no release date, so the score sweep
never sees one (59 of 170 cached detail pages had no resolvable date). This is a documented limit,
not a bug to work around: the date-sorted listing and `latest` mode still reach those titles.

## Component changes

### `src/gamarr/config.py`

- `BacklogScoreConfig` becomes `oldest_month: int = 0` (validator `validate_oldest_month`, shared with
  the other blocks) plus `pages_per_cycle: int = Field(default=20, ge=0)`. `page_depth` is deleted,
  so `extra="forbid"` rejects it in a new-shape file.
- `_migrate_backlog_score_block` converts instead of copying: a legacy `max_pages` value `N > 0`
  becomes `oldest_month = _clamp_floor(subtract_months(current_year_month(),
  pages_to_months(N)))`, logged `(approximate)` like the `backlog.new` conversion; `N == 0` becomes
  `oldest_month: 0` (no floor). `max_cycle_pages` still copies into `pages_per_cycle` unchanged.
- New migration for a **block-shaped** file: a `page_depth` key already inside
  `discovery.backlog.score` converts by the same rule and the key is removed. This is the path the
  current release's own generated config takes (the live file holds
  `score: {page_depth: 500, pages_per_cycle: 20}`); without it the file fails validation after
  upgrade.
- The deprecated-key message that names `discovery.backlog.score.page_depth` (around line 443)
  names `discovery.backlog.score.oldest_month` instead.
- `page_depth` stays in `_STRAY_DISCOVERY_KEYS`: a legacy stray key must still be popped rather than
  rejected.

### `src/gamarr/discovery.py`

- `ScanPlan.page_depth` is removed.
- The score branch builds `ScanPlan(mode="backlog", scan_order=backlog.sort_order, date_mode=False,
  floor_month=backlog.score.oldest_month, pace_months=0, pages_per_cycle=<pace>)`, where `<pace>` is
  `backlog.score.pages_per_cycle or 20`. When the configured value was `0`, one INFO line names the
  coercion and the reason (one cycle must not crawl the catalogue).
- Docstrings restated: `floor_month` is read by both branches, and `date_mode` now selects **month
  pacing only**, not "has a floor".

### `src/gamarr/pipeline.py` (discovery loop, `_run_discovery_phases`)

- Delete the page-budget plumbing: `max_pages_cfg`, `remaining_pages`, the
  `db.sum_scanned_pages` call and the `Budget exhausted` reset, the `remaining_pages <= 0` stop, and
  the per-year `remaining_pages -= pages_scanned` decrement. `pages_per_cycle` is the only page
  bound.
- Year range for a score sort: `cutoff_year = floor_month // 100 if floor_month else
  EARLIEST_BROWSE_YEAR`, `current_year = scan_year_anchor` - the same range the date-sorted sweep
  uses, replacing the `0` sentinel that made the loop a single all-time pass.
- `scan_recent_games(..., year=scan_year)` for every sort.
- `_games_at_or_after_floor` is applied only when `scan_order == "new"`.
- Floor-year completion is computed for any non-latest sweep: `floor_year_finished` when
  `scan_year == cutoff_year` and the year drained (empty probe, client-reported exhausted listing, or
  an empty floor year), guarded by `not fetch_failed` and `not is_cancelled`. The existing
  `sweep_complete` block then runs for the score sweep too, logging the floor **year** for a score
  sort and resetting the cursors. The date-sorted branch's floor/pacing logic is untouched.
- The progress line for a score sweep reports the year reached and the floor instead of a
  percentage of `page_depth` (`Progress: N of M pages scanned` is deleted with the budget).

### `src/gamarr/utils.py`

- `PAGES_PER_MONTH = 33`, with the measurement and ADR 0009 in the docstring/comment. No change to
  `pages_to_months`' round-up rule.

### Not changed

`metacritic.py` already builds a year-scoped URL for any sort slug and already caches browse pages
keyed by `(platform, page, year)`; `database.py` already stores per-year cursors. No schema change,
no new table, no new dependency.

## Migration, worked

| Legacy file | Becomes |
| ----------- | ------- |
| `score: {page_depth: 500, pages_per_cycle: 20}` | `score: {oldest_month: 202507, pages_per_cycle: 20}` (500 / 33 = 16 months back from the load month) |
| `max_pages: 500`, `max_cycle_pages: 16` (flat) | `score: {oldest_month: 202507, pages_per_cycle: 16}` |
| `score: {page_depth: 0, pages_per_cycle: 0}` | `score: {pages_per_cycle: 0}` - nothing is written for a legacy `0`, because `oldest_month` already defaults to 0 (no floor) and the pace is coerced to 20 at plan time |

Each conversion is logged as `(approximate)`, per ADR 0007. The default `pages_per_cycle: 20`
appears in `_default_config_dict`, so a config lacking the key is rewritten once by the existing
`_needs_config_update` path with a version bump - the same one-time rewrite previous releases
performed, not new behaviour.

The migrated floor is a compatibility shim, not advice: an operator who wants the back catalogue sets
`oldest_month: 201601` themselves.

## Error handling and edge cases

- Fetch failure anywhere in a cycle: no `floor_year_finished`, no cursor reset, cursors keep their
  last real page so the failed year is retried.
- Cancellation mid-cycle: no completion, no reset.
- Floor year equals the current year: one year is read and drained; the same cycle can complete it.
- Floor `0`: the range reaches `EARLIEST_BROWSE_YEAR`; still paced, still completes on the earliest
  year, still restarts.
- Floor in the future: clamped by `validate_oldest_month` (existing behaviour).
- A year whose listing is empty: counts as a drained probe; if it is the floor year, the sweep
  completes.
- A mis-indexed year (all pages older than a floor) cannot happen for a score sort: no per-page date
  cutoff is applied there.
- A config that sets both `page_depth` and `oldest_month` inside `score`: `page_depth` converts only
  when `oldest_month` is absent (`_place` semantics); the legacy key is then removed, and the
  operator's explicit floor wins.

## Testing

`uv run pytest -q` after each task; the suite is the gate (`uv run ruff check --fix .`,
`uv run ruff format .`, `uv run mypy .` per the project standard).

- `tests/unit/test_config.py`: score defaults (`oldest_month == 0`, `pages_per_cycle == 20`);
  `page_depth` rejected as an unknown key; `oldest_month` validation and future clamp; flat
  `max_pages`/`max_cycle_pages` migration; **block-shaped `page_depth` migration using the live
  config verbatim**; `page_depth: 0` to no floor; the existing score-block assertions rewritten.
- `tests/unit/test_discovery.py`: score plan carries the floor and the pace, has no `page_depth`, and
  a `0` pace becomes 20 with the INFO line.
- `tests/unit/test_pipeline.py`: `year=<year>` is passed for a score sort; the sweep starts at the
  current year and steps down; a cycle's allowance spans years; a per-year cursor is written;
  reaching the floor year resets the cursors and logs the restart; a fetch failure in the floor year
  does not complete; the whole floor year is kept (no intra-year floor filter). Existing
  `page_depth` tests are rewritten against `oldest_month`.
- `tests/unit/test_utils.py`: `PAGES_PER_MONTH == 33`, `pages_to_months(500) == 16`.
- `tests/unit/test_readme_accuracy.py`: the defaults table row for the score block.

## Documentation

- `README.md`: replace the `page_depth` row with `discovery.backlog.score.oldest_month` (year-grain
  wording), set the `pages_per_cycle` row's default to `20` and its meaning to "workload, 0 pays the
  default", correct the "24 pages is about one month" claim to the measured 33, rewrite the
  score-sort paragraph (the sweep walks years, restarts at the floor, accepts page-boundary drift and
  cannot see undated titles), and update the sample config block.
- `docs/agent/glossary.md` and ADR 0008/0009 are already written; no further doc changes.

## Decisions made while writing the spec

Implementation-level gaps the grill left open, decided from the code:

1. **`date_mode` keeps its name but narrows its meaning** to "month-paced". The score branch keeps
   `date_mode=False` because it must not use month span, while `floor_month` is now read by both
   branches for the year range and completion.
2. **The coercion logs from `build_scan_plan`** (loguru, one line per plan build), mirroring the
   ledgered gap where a silent `0 -> 1` month coercion was accepted but never named.
3. **The intra-year floor filter is skipped for score sorts** so the year grain is real rather than
   accidental.
4. **Completion for a score sweep is listing-exhaustion only**: there is no date cutoff to detect, so
   `_recent_games_cutoff_reached` is not consulted.
5. **Migration keeps `pages_per_cycle` verbatim**, including `0`, because the coercion is a plan-time
   concern with a log line; converting it at load would hide the operator's original value.
6. **`page_depth` converts only when `oldest_month` is absent** so an explicit floor always wins.

## Out of scope

- A month-granular score sweep (the index has no month filter).
- A catch-all pass for undated titles.
- Any change to `latest`, `backlog.new`, `sort_order` semantics or the pending queue.
- Retuning the default pace per platform or deriving it from the index at runtime.
