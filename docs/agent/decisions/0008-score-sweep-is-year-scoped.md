# A score-sorted backlog is bounded by a date floor, not a page depth

A score sort mixes release years, so a page of that listing has no date meaning; `page_depth` was
therefore the only way to say how far a sweep reaches, and the number had to be re-measured as the
catalogue grew (33.4 pages per month over 24 months, ~2,200 pages to reach 2016, ~400 more per
year). The sweep instead walks release **years** newest to oldest, score-sorted inside each year,
down to a floor month; pages bound only the per-cycle pace. Metacritic exposes `releaseYearMin` /
`releaseYearMax` and no month filter, so a year is the finest scope the sweep can ask for, and a
score-sorted year-scoped listing is confirmed to work on the live index. Reach becomes a value the
operator states once; pace becomes a workload budget whose meaning does not drift.

## Considered Options

- **Keep the all-time sweep and re-tune `page_depth`.** No code change, but the number you write is
  wrong within months and no value of it expresses "stop at 2016".
- **Pace in games instead of pages.** Removes page drift, but the sweep still has no date meaning, so
  the floor the operator actually wants stays inexpressible.

## Consequences

The sweep runs newest year first, so recent titles are verified and queued earliest. Its floor is
`oldest_month` read at year grain, so a value mid-year reads the whole of that year; `page_depth` is
removed from the score block, leaving one way to state reach and one meaning for pages. The per-year
cursor machinery the date-sorted backlog already uses applies unchanged, so a sweep resumes mid-year
across cycles. Page order inside a year shifts as titles are added, so a sweep is best-effort at the
page boundary: the processed-games database prevents duplicate work, and re-running the sweep from a
reset cursor picks up a skipped title. Completing the floor restarts from the newest year, the rule
the date-sorted backlog already follows, so a new pass begins rather than discovery going idle. A
legacy `page_depth` becomes an equivalent `oldest_month` (ADR 0009's constant, rounded by year
grain), and `pages_per_cycle: 0` is coerced to 20 with an INFO line so one cycle can never crawl the
catalogue. A year filter cannot return a title with no release date, so such titles are outside the
sweep; the limit is documented rather than worked around with a second all-time traversal.
