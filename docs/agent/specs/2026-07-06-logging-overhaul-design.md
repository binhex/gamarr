# gamarr Logging Overhaul Design

**Date:** 2026-07-06
**Status:** Approved — awaiting implementation

## Motivation

Current INFO-level logging is verbose and confusing. A new user reading the log output
cannot easily tell what gamarr is doing in each pipeline stage, how far it has progressed,
or whether it's stuck. Key problems:

- No phase structure — the pipeline stages (scan → index → match → deliver) are invisible
- Batch progress spam: FreeGOG prints 11 "processing entries" lines; Metacritic prints
  per-page cutoffs in steady-state
- Off-by-one confusion: "page 80 reached; stopping scan" then "scanned 79 pages"
- Unexplained large numbers: "6835 pending games" with no context of where they came from
- Backlog vs steady-state: no visual distinction during long backlog scans

## Design Principles

1. **Phase-level only** — each pipeline stage gets a clear banner and a one-liner result
   at INFO level. Implementation detail moves to DEBUG.
2. **Numbered phases** — sequential phase numbering ("Phase 1/5") shows overall progress
3. **Batch progress suppressed** — FreeGOG "processing entries" lines and Metacritic
   per-100-page progress in steady-state are removed from INFO
4. **Backlog heartbeat** — during backlog scans, one concise progress line every 100 pages.
   No page-level output in steady-state
5. **Per-game match/skip kept** — one INFO line per game during source matching, since
   this is the most important output (what gamarr actually found)

## Phase Structure

Five numbered phases replace the current unstructured log output:

| Phase | Banner | Summary |
|-------|--------|---------|
| 1/5 | `--- Phase 1/5: Discovering games on Metacritic ---` | Scan window, pages browsed, games collected, pending queue, score check |
| 2/5 | `--- Phase 2/5: Indexing FreeGOG ---` | Entries checked, new games found (or "all already known") |
| 3/5 | `--- Phase 3/5: Indexing FitGirl ---` | Cache status, new titles indexed (or "skipping, cache valid") |
| 4/5 | `--- Phase 4/5: Matching games to download sources ---` | Per-game match/skip lines, end-of-phase summary |
| 5/5 | `--- Phase 5/5: Delivering to qBittorrent ---` | Per-game delivery lines, or "No matches to deliver" |

## Phase 1: Steady-State vs Backlog

Phase 1 adapts its output based on whether the scan is in steady-state (small sliding window)
or backlog mode (many weeks to cover).

### Steady-State

```
--- Phase 1/5: Discovering games on Metacritic ---
Scan window: last 4 weeks (2026-06-08 → 2026-07-06)  [max_weeks=52, cycle=4]
Scan result: 79 pages browsed, 1896 games collected
Pending queue: 0 new + 6835 from previous cycles = 6835 total
Score check: 6835 verified (6833 from cache, 2 from Metacritic API)
```

- No page-level progress output during scan (steady-state is fast enough)
- `Scan window` line includes config context so users understand what controls the range
- `Pending queue` explains where the number comes from (new + carried over)
- `Score check` shows cache hit ratio so users know if Metacritic API was called

### Backlog

```
--- Phase 1/5: Discovering games on Metacritic ---
Backlog scan: cycle 12/26, window 2025-04-08 → 2025-05-13 (14 cycles remaining)
Backlog scan: page 100 (304 games)
Backlog scan: page 200 (582 games)
...
Backlog scan: page 450 (1896 games)
Scan result: 450 pages browsed, 1896 games collected
Pending queue: 3 new + 1893 from previous cycles = 1896 total
Score check: 1896 verified (1800 from cache, 96 from Metacritic API)
```

- Cycle counter shows progress toward catching up
- Heartbeat line every 100 pages ensures the user knows gamarr is still working
- Heartbeat shows cumulative game count for a rough progress estimate

## Phase 2: FreeGOG

```
--- Phase 2/5: Indexing FreeGOG ---
FreeGOG: 10 new games found (5460 entries checked, 5450 already known)
```

Or when nothing new:

```
FreeGOG: all 5460 entries already known — nothing new
```

Removed from INFO: all `"FreeGOG: processing entries X-Y of N..."` lines.
The individual entry processing (with `% 500` batching) already writes no per-entry output,
so the only change is removing the batch-progress lines.

## Phase 3: FitGirl

```
--- Phase 3/5: Indexing FitGirl ---
FitGirl: cache valid (expires 09:30) — skipping fetch
```

Or when fresh fetch:

```
FitGirl: 125 new game titles indexed
```

## Phase 4: Match

```
--- Phase 4/5: Matching games to download sources ---
NBA The Run — no FreeGOG match (staying in queue)
NBA The Run — no FitGirl match (staying in queue)
Venus Vacation PRISM — matched on FreeGOG!
Match summary: 1 delivered, 6834 no match, 0 rejected by keywords
```

Per-game match/skip lines are kept at INFO. Source names use the properly-capitalised
form (`_format_source_name`).

The end-of-phase summary adds up totals so users don't need to count lines.

## Phase 5: Deliver

```
--- Phase 5/5: Delivering to qBittorrent ---
✓ Venus Vacation PRISM sent to qBittorrent (tag: gamarr-abc)
```

Or when nothing matched:

```
No matches to deliver this cycle.
```

## Log Level Changes

| Message | Old Level | New Level |
|---------|-----------|-----------|
| Phase banners | *(none)* | INFO |
| Periodic heartbeat (backlog, every 100 pages) | INFO | INFO |
| Page-100 progress (steady-state) | INFO | **removed** |
| Page cutoff hit ("stopping scan") | INFO | DEBUG |
| FreeGOG batch "processing entries" lines | INFO | **removed** |
| Per-game match/skip | INFO | INFO (unchanged) |
| Pending queue breakdown | *(not logged)* | INFO |
| Match summary | *(not logged)* | INFO |
| Score check cache hit ratio | INFO | INFO (rewritten) |
| Detailed score verification (per-game) | DEBUG | DEBUG (unchanged) |

## Files Changed

| File | Changes |
|------|---------|
| `src/gamarr/pipeline.py` | Phase banners before each major section; rewritten scan/pending/score messages; match summary line |
| `src/gamarr/metacritic.py` | Conditional heartbeat logging; page-cutoff message → DEBUG; fixed off-by-one summary; scan-window config context |
| `src/gamarr/sources/freegog.py` | Remove batch processing lines; rewritten summary wording |
| `src/gamarr/sources/fitgirl.py` | Align summary wording with FreeGOG style |
| `tests/unit/` | Update any tests that assert on log message text |

## Non-Goals

- No config schema changes (no `log_verbosity` option, no new config keys)
- No behavioural changes — only messages change
- No new dependencies
- No change to log file format (same loguru pipe-delimited format)
- No structural refactoring beyond what's needed for cleaner messages

## Risks

- Tests that assert on exact log message text will need updating (low risk, mechanical)
- Removing batch progress may make very slow FreeGOG fetches appear hung
  (mitigation: the phase banner + final result line bracket the operation)
