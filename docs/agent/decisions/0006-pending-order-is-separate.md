# Pending-queue order is its own key

`sort_order` did two jobs: it chose the discovery list order and it reprioritised the pending
queue. Once `latest` reads newest-first and has no `sort_order` at all, the queue would silently
lose the ability to be processed by score in that mode. Queue order is now `pending_order`,
defaulting to `new`; `sort_order` is discovery-only. `pending_order` sits at the metacritic level,
not inside the per-platform `discovery` block, because the pending queue is shared while discovery
is per platform - the same reasoning that put `source_matching_days` on `download_sites`.

## Considered Options

- **`sort_order` keeps both jobs.** Fewer keys, but the key's meaning would depend on the mode
  again, which is the problem this change exists to remove.
- **Queue is always newest-first.** Simplest surface, but it removes score-ordered processing,
  which is a documented capability.

## Consequences

Two keys to keep consistent in the README, and a `latest` cycle can discover by date while
processing the queue by score. A config that wants score ordering only touches `pending_order`.
