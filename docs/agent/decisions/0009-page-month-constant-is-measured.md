# The page-to-month constant is the measured figure, not 24

ADR 0007 converts every legacy page count into months using one constant, which was set from a July
estimate of about 24 games per browse page and 24 releases per day. Measured against 870 pages of
the live PC index (43,500 games, Nov 2024 - Nov 2026) a page holds **50** games and the index
releases **33.4 pages per month** across 24 complete months, climbing to 52 over the last quarter.
The constant becomes 33, with the drift stated in the ADR and README so a conversion is understood
as an approximation that ages.

## Considered Options

- **Use the current rate (~52).** Matches today's cost, but over-states reach for configs converted
  from a budget written years ago.
- **Leave 24.** Changing the constant silently changes what an already-converted config means; the
  existing figure is wrong by ~40% on the 2-year average and ~2.2x at today's volume.

## Consequences

`PAGES_PER_MONTH` governs migrations and every page-to-month conversion only; no sweep re-reads the
index differently. Because the index is rising (38.7 pages/month over the last year, 52 over the last
quarter), any converted figure is a snapshot: a reader must treat it as approximate by design, and a
future measurement supersedes this ADR rather than restoring the old number.
