# Rename discovery keys to say what they do

> The key list below is the 2026-06 rename. `page_depth` has since been removed from the score block
> by ADR 0008 (a score sweep reaches back to `oldest_month`); the other names still stand.

`max_date` is a date and `max_pages` is a page count, but both read as "a bound", and the
`max_cycle_*` pair named pacing quantities without saying so. We renamed them `oldest_month`,
`months_per_cycle`, `pages_per_cycle` and `page_depth`. The legacy flat shape is migrated on load
(ADR 0001, ADR 0007), so the old spellings keep working in an existing file without ever becoming
aliases: an old name written inside a new block is an unknown-key error.

## Considered Options

- **Keep the current names**, changing only the grouping. Rejected: a reader still maps five names
  onto two ideas, and `max_date` still sounds like a page bound.
- **Permanent aliases for the old names.** Initially chosen, then dropped: the migration already
  covers every real config, so aliases would only serve a hand-written typo, at the cost of two
  names per key forever.
- **Rename with no migration path.** Rejected: every existing deployment breaks on upgrade.
