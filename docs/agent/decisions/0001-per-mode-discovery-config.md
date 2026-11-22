# Split discovery config into per-mode blocks

The discovery keys (`mode`, `max_pages`, `max_date`, `max_cycle_months`, `max_cycle_pages`,
`sort_order`) changed meaning depending on the mode and on which other keys were set, so in the
live config three of them silently did nothing and the `max_pages` doc had to explain four cases.
We split them into a `latest` block and a `backlog` block, each holding only the keys its mode
reads; `mode` stays the explicit selector and the non-selected block is ignored with one INFO
line; legacy flat keys are auto-migrated into the blocks so existing configs keep loading.

## Considered Options

- **Flat keys with strict validation.** Smallest diff, no YAML churn. Rejected: the reader still
  has to learn the mode matrix to read the file, which is the reported problem.
- **Prefixed flat keys** (`latest_oldest_month`, `backlog_pages_per_cycle`, ...). Rejected: long
  names, and every config file repeats both modes' keys.

`sort_order` moved into the `backlog` block and became a block selector there, because `latest`
must read newest-first and the old warn-and-coerce rule existed only to repair an impossible
combination that the split now makes unwritable.

## Consequences

An ignored key becomes impossible by construction rather than reported after the fact. Where both
shapes appear in one file the new block wins and an INFO line reports the ignored legacy key.

Migrating a legacy key always has a path: page counts convert to month figures (ADR 0007) rather
than failing, so an existing config keeps loading across the upgrade. An absent or empty selected
block is filled from the model defaults, so the smallest valid config states only `mode`, and the
default mode is `latest`, so an untouched config does bounded work each cycle.

Config migration must map each legacy key into the block of the mode it was written for, and cannot
know that for `max_pages`, which was valid in both modes.
