# Every legacy page count converts to months

`latest` is paced only in months and `backlog.new` is bounded only by a floor, so a legacy config
that bounded discovery by pages had no home for those numbers. Rather than stop loading, every
legacy page count converts to a month figure at load: a total budget becomes `oldest_month`, a
per-cycle pace becomes `months_per_cycle`, and a page depth destined for a block that still uses
pages (`backlog.score`) is left alone. Each conversion is logged as an approximation.

The conversion uses one constant for the whole codebase: about 24 games per browse page and about
24 games released per day, so one page is about one day and a month is about 24 pages. The same
constant replaces the existing `max_pages / 52` year derivation, which disagreed with the measured
figure by a factor of about 5.5; a 500-page budget therefore reaches roughly 21 months, not the ten
years the old derivation implied.

Arithmetic rounds up, with a minimum of 1. One rule for every conversion, so a converted pace never
reads less than the page budget it replaced and a page count can never become a 0-month pace; the
overshoot is at most one month.

## Considered Options

- **Fail loudly where the new shape has no home.** Chosen in an earlier round and then reversed: no
  approximation is applied, but every legacy page-mode deployment stops until its config is edited.
- **Convert in `latest` only, error in `backlog.new`.** The same number honoured in one block and
  rejected in another, for a difference the file itself cannot show.

## Consequences

No legacy config stops loading, so the compatibility promise holds for every shape, at the cost of
a documented approximation in the two conversions that cross units. The factor is empirical; if
release density changes, converted configs drift from what their author meant. A converted value is
written back to the file only in the same way the existing migrations do, and is always logged.

Because the same constant now derives the score-sorted year window, the effective reach of a
page-budgeted sweep shrinks for existing configs. That is a behaviour change, and it is the honest
one: the old ten-year reach was never what 500 pages covered.
