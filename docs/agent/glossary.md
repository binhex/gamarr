# gamarr discovery

gamarr scrapes Metacritic release lists, verifies scores against detail pages, and queues
games for repack matching. These terms name the parts of that pipeline a user configures.

## Discovery

**Discovery mode**:
The stated choice of how a cycle walks the release list: `latest` re-reads the newest end
every cycle, `backlog` sweeps backwards through it with per-year cursors.

_Avoid_: search mode, scan mode

**Window floor**:
The oldest month a cycle may read, configured as `oldest_month`.

_Avoid_: max_date, cutoff, date limit

**Pace**:
How much of the window one cycle reads, counted in months (`months_per_cycle`) or in pages
(`pages_per_cycle`).

_Avoid_: max_cycle_months, max_cycle_pages, throttle, cycle limit

**Page depth**:
How many browse pages a cycle reads from the top of the window when no pace applies,
configured as `page_depth`. Only `backlog.score` uses it.

_Avoid_: max_pages, page budget, depth limit

**Sort order**:
Which list order discovery reads: `new` (newest release first), `criticscore`, or `userscore`. A
`latest` cycle is always `new`.

_Avoid_: sort mode, ordering

**Pending order**:
The order queued games are processed in, configured as `pending_order` and independent of how
discovery read them.

_Avoid_: sort_order, queue order

**Inert key**:
A configured key the active mode never reads. It is ignored, and one INFO line names it.

## Queue

**Source-matching window**:
How long a verified queued game waits for a matching repack before it is dropped, configured
as `source_matching_days` and overridable per download source.

_Avoid_: max_queue_days, pending days, queue expiry
