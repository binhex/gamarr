# `latest` is paced by months, never by page depth

> The page-to-month conversion named below uses the measured 33 pages per month of ADR 0009, not the
> "roughly 24 games per browse page" premise recorded here.

A `latest` cycle used to fall back to a page-depth budget whenever neither date key was set, so the
same mode could be bounded two different ways and a config could hold a page count that silently
did nothing. `latest` now reads a month pace (`months_per_cycle`) back from the newest release and
never reads a page count; the page-depth fallback is gone from that mode.

## Considered Options

- **Keep the page-depth fallback.** No behaviour change and no conversion in the migration, but the
  two-bounding-routes problem that caused the confusion would survive the whole restructure.
- **Keep a fallback but reject a config that sets both.** Nearly the same config surface, and it
  turns a silent ignore into a load error for existing files.

## Consequences

A cycle's cost is stated in the unit the user thinks in (months of releases), and the page-count
knob cannot reappear in `latest`. A legacy `latest` config that only set `max_pages` has to be
converted to an equivalent month pace during migration, using the repo's documented figure of
roughly 24 games per browse page; that conversion is an approximation and is logged when applied.
