# `backlog` splits its keys by sort order

`backlog` was the last place where a key's meaning depended on the value of another key: with
`sort_order: new` it used dates (`oldest_month`, `months_per_cycle`), and with a score sort it used
pages (`page_depth`, `pages_per_cycle`), because score-ordered lists mix years and no page
corresponds to a date. The `backlog` block therefore splits into a block per sort, and `sort_order`
selects which one runs, exactly as `mode` selects between `latest` and `backlog`.

## Considered Options

- **Sort decides which pair applies, in one block.** One INFO line names the pair not in use. Less
  nesting, but the reader still has to hold two key meanings in one block.
- **Drop score sorts from discovery.** `backlog` becomes date-only and the page keys disappear
  entirely. Rejected: it removes a documented capability rather than clarifying it.

## Consequences

Three levels of nesting (`platform_overrides.<platform>.discovery.backlog.<block>`). The two score
sorts share one `score` block, because they read identical page keys and only the sort value
differs, so the block names are `new` and `score` rather than one block per sort value. A config
that runs `new` never shows the page keys at all.

A legacy file may hold a page pair written for a `new` sort, which the new shape has no place for;
those page counts convert to month figures during migration instead (ADR 0007).
