# Every legacy page count converts to months

> The conversion rule below stands; its constant is superseded by ADR 0009 (33 pages per month,
> measured, not 24). One sentence below is also superseded by ADR 0008: a page budget destined for
> `backlog.score` is no longer "left alone" - it now converts to `oldest_month` like every other
> budget, and the score block keeps pages only as its per-cycle pace.

`latest` is paced only in months and `backlog.new` is bounded only by a floor, so a legacy config
that bounded discovery by pages had no home for those numbers. Rather than stop loading, every
legacy page count converts to a month figure at load: a total budget becomes `oldest_month`, a
per-cycle pace becomes `months_per_cycle`, and a page depth destined for a block that still uses
pages (`backlog.score`) is left alone. Each conversion is logged as an approximation.

The conversion uses one constant for the whole codebase: 33 measured pages per month (ADR 0009).
The original premise recorded here was about 24 games per browse page and 24 releases a day, i.e.
about 24 pages a month; that premise is superseded, and only the 33-page figure is used. The same
constant replaces the existing `max_pages / 52` year derivation, which disagreed with the measured
figure by a factor of about 7.5; a 500-page budget therefore reaches roughly 16 months, not the ten
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

A legacy config that expressed no bound at all (no floor, no pace, no page key) keeps the reach
the old default page budget gave it, so an upgrade does not silently narrow or widen the sweep: the
migration writes a 16-month pace for `latest` and a 16-month floor for `backlog.new` and
`backlog.score` (including a score block that stated no bound at all, which the old runtime ran on
the default page budget). A model default therefore never applies to an upgraded config, only to a
new one, and a block this release writes always states its floor, so it is never mistaken for one of
these. An explicit `page_depth: 0` still means "no budget" and stays floorless, and a `page_depth`
the migration cannot read falls back to the default reach like an absent one. A file that still
carries flat legacy keys is exempt: its own budget converts first.

The constant governs the migrations only. A page-budgeted score sweep no longer derives a year
window from its budget at all, so the effective reach of a converted config comes from the floor the
migration writes, not from a page-to-year calculation. That is a behaviour change, and it is the
honest one: the old ten-year reach was never what 500 pages covered.
