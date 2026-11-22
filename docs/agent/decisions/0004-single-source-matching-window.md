# One `source_matching_days`, overridable per source

Three keys named the same queue window: `download_sites.fitgirl.max_queue_days`,
`download_sites.freegog.max_queue_days`, and a metacritic-side fallback the live config never
reached because a source always supplied a window. They collapse to one `source_matching_days`,
defaulting to 60, which a download source may override under the same name; the metacritic-side
fallback is deleted. The key sits at the top level as a sibling of `download_sites` rather than in
the discovery block, because the queue is shared while `discovery` is per-platform.

## Considered Options

- **Delete only the metacritic fallback** and keep a window per source. Fewest new keys, but a
  source-less config then never expires a queued game, which is a silent trap rather than a stated
  default.
- **Leave all three keys and document the precedence.** Smallest change, but the ambiguity stays.
- **Wrap `download_sites` in a mapping** (`{source_matching_days, sources: [...]}`) to contain the
  default inside the section it governs. Rejected: it restructures every existing block for a
  placement preference, so the key is a sibling of the list instead.

## Consequences

One place states the default and one place per source states an exception, under the same key name,
so a reader cannot mistake the two and "which key wins" is answerable from the file. The fallback's
real job - supplying a window when no source sets one - is now the default value instead of a
second key.
