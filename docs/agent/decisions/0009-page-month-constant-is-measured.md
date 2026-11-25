# The page-to-month constant is the measured figure, not 24

ADR 0007 converts every legacy page count into months using one constant, which was set from a July
estimate of about 24 games per browse page and 24 releases per day. Measured against the live PC
index a page holds **50** games: over the 24 complete months Nov 2024 - Oct 2026 the index released
40,043 games, which is 1,668 a month and **33.4 pages per month**, climbing to 52 over the last
quarter. (The 870-page scan behind the measurement reaches back to 2024-10-31, about 24.8 months.)
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
