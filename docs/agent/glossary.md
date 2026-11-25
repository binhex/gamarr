# gamarr discovery

gamarr scrapes Metacritic release lists, verifies scores against detail pages, and queues
games for repack matching. These terms name the parts of that pipeline a user configures.

## Discovery

**Discovery mode**:
The stated choice of how a cycle walks the release list: `latest` re-reads the newest end
every cycle, `backlog` sweeps backwards through it with per-year cursors.

_Avoid_: search mode, scan mode

**Window floor**:
The oldest month a cycle may read, configured as `oldest_month`. A score-sorted sweep reads its
floor at year grain, because the index filters by whole release year only, so a value mid-year reads
the whole of that year.

_Avoid_: max_date, cutoff, date limit, oldest_year

**Pace**:
How much of the window one cycle reads, counted in months (`months_per_cycle`) for a date-sorted
sweep or in pages (`pages_per_cycle`) for a score-sorted one. A page count is a workload unit only;
it never says how far back a sweep reaches.

_Avoid_: max_cycle_months, max_cycle_pages, throttle, cycle limit, page budget, page depth

**Sort order**:
Which list order discovery reads: `new` (newest release first), `criticscore`, or `userscore`. A
`latest` cycle is always `new`. A score-sorted backlog walks release years newest to oldest, because
a score-ordered list mixes years and no page of it corresponds to a date.

_Avoid_: sort mode, ordering

**Pending order**:
The order queued games are processed in, configured as `pending_order` and independent of how
discovery read them.

_Avoid_: sort_order, queue order

**Inert key**:
A configured key the active mode never reads. It is ignored, and one INFO line names it when
its value differs from the model default; a generated default stays silent.

## Queue

**Source-matching window**:
How long a verified queued game waits for a matching repack before it is dropped, configured
as `source_matching_days` and overridable per download source.

_Avoid_: max_queue_days, pending days, queue expiry
