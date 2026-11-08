# Design: explicit discovery modes, verify-before-queue, and an honest keyword scope

Date: 2026-11-06
Status: Approved (user agreed each decision in session)

## Goal

Make discovery predictable and stop the queue accumulating games that can never pass.

Three symptoms were reported and then measured against the live instance (867 cycles of
`gamarr.log`, the live `gamarr.db`, and live fetches of FitGirl pages):

1. No new games are delivered, and older games appear only sporadically.
2. The user cannot tell what the daemon is doing, because "mode" is an emergent
   consequence of `max_date` / `max_pages` / `max_cycle_*` rather than a stated choice.
3. The pending queue holds 1,555 rows, 1,534 of which can never pass verification
   (16,896 rows have expired all-time against 63 deliveries).

The intended behaviour is two explicit modes: a **latest** mode that looks at the newest
releases, and a **backlog** mode that sweeps back through older ones, with critic/user
score sorting applying to both.

## What was measured (and what corrected the original diagnosis)

- **The `hv` keyword accounts for most match rejections**: 7,821 skips on `hv`, 1,817 on
  `emulator`, 771 on `hypervisor` in the current log. All 21 games that passed Metacritic
  verification were blocked — Wukong, Metaphor, RE Requiem, Forza Horizon 6, Crimson Desert,
  Jurassic World Evolution 3 and others.
- **Most of those rejections are correct.** FitGirl's "Repack Features" block for Black Myth:
  Wukong reads *"Hypervisor Bypass (INTEL & AMD) by DenuvOwO + Goldberg emu added … Custom
  launcher added for easier HV steps processing"*. Metaphor and Crimson Desert carry the
  same bypass line. These are genuinely HV repacks.
- **A minority are false positives.** Resident Evil Requiem and Forza Horizon 6 have no
  `hv`/`hypervisor`/`emulator` in their Repack Features block; they are rejected on
  download-mirror boilerplate at the top of the page (*"Only updated files for a new non-HV
  repack"*, *"convert your INSTALLED HV version"*).
- **The scope is also upside down.** On the Wukong page the boilerplate sits at characters
  476 and 1027, `Backwards Compatibility` (today's truncation point) at 2221, and
  `Repack Features` at 4914. The current code discards the authoritative section and keeps
  the text that causes false positives.
- **"Latest" is a deep crawl.** The 2026 listing is 866 cached pages; a cycle advances about
  16 pages at `max_cycle_months: 1` and mostly re-sees known slugs (*"3 of 800 collected
  games passed title/age filters"*). Pages 1..N hold the newest releases; the sweep spends
  its time below them.

**Accepted consequence:** the user has confirmed the HV rejection is deliberate.
Denuvo-era AAA titles that FitGirl ships with a hypervisor bypass (Wukong, Metaphor,
Crimson Desert and most new large releases) therefore stay excluded. The keyword change
below fixes the false positives only and is **not** expected to restore AAA delivery.

## Decisions (user-approved)

1. **Latest depth = top-of-window, N pages.** Latest mode starts at page 1 every cycle,
   scans `max_pages` pages, and stops. No cursors, no reset.
2. **Pass-or-drop at queue time.** Verify the detail page before inserting into
   `pending_games`; insert only games that pass. Games that fail, or have no scores yet,
   are not inserted and are re-evaluated on the next pass (unqueued slugs are not "known").
3. **Keyword matching = whole word + negation guard**, scoped to the page `<title>` and the
   **Repack Features** block. The `Backwards Compatibility` truncation hack is removed.
4. **Scope refined after measuring the page layout**: matching only that scope is what
   distinguishes a real HV repack from boilerplate.
5. **`mode: latest | backlog`**, default `backlog`, and `max_pages` keeps its key name:
   total sweep budget in backlog, first-N-pages depth in latest.
6. **Latest requires `sort_order: new`.** A score sort configured with latest logs one
   warning per cycle and scans in `new` order for that cycle; backlog keeps all three sorts.
7. **One-time queue migration**: delete rows where `score_checks_passed IS NOT TRUE`,
   keeping the 21 verified candidates.
8. **One spec, sequenced implementation**: keyword matcher first (removes false positives),
   then queue migration and verify-before-insert, then `mode`, then docs.

## Current mechanics (verified)

- `MetacriticPlatformConfig` (`config.py:112-130`) holds `max_pages` (default 500),
  `max_cycle_pages`, `max_date`, `max_cycle_months`, `sort_order`, `max_queue_days`.
- Discovery is Metacritic-first: `_run_discovery_phases` (`pipeline.py:303`) browses, then
  `_process_browse_games` (`pipeline.py:962`) applies cheap filters — known-slug dedupe
  (`_is_game_eligible`, `pipeline.py:927`), `reject_title`, and
  `_reject_by_browse_review_counts` (`pipeline.py:841`) — and calls `db.record_pending`
  (`database.py:406`). No detail lookup happens here.
- Verification happens afterwards over the whole queue: `_verify_pending_scores`
  (`pipeline.py:1605`), called with `max_verify=len(pending_games)`, so every pending row is
  "checked" each cycle (cache hits via `cache_details_days`). Rows passing get
  `score_checks_passed = TRUE`; failing rows are kept until expiry.
- Matching only processes verified rows: `_match_pending_games` (`pipeline.py:1841`) skips
  `not game.score_checks_passed` (`pipeline.py:1896`).
- `date_mode = bool(cfg.max_date) and cfg.sort_order == "new"` (`pipeline.py:330`) selects
  the date/page branch; latest mode reuses that year loop.
- Keyword rejection: `_check_reject_keywords` (`pipeline.py:2178`) — FitGirl-only
  (`pipeline.py:2173`) — fetches `<title>` + `<article>` text via
  `_fetch_fitgirl_page_content` (`pipeline.py:2974`), truncates at `Backwards Compatibility`
  (`_truncate_at_backwards_compat`, `pipeline.py:2247`), and matches with
  `_title_contains_keywords` — a plain `kw.lower() in text.lower()` substring test.
- Database migrations are additive `ALTER TABLE` statements inside `Database.__init__`
  (`database.py:176`, examples at 215, 265, 270), plus data migrations elsewhere; there is a
  precedent for one-time data changes in the config layer but not yet in the DB layer.
- `scheduler._build_kwargs` (`scheduler.py:313`) forwards the per-platform config to
  `run_acquisition` by explicit key name.

## Design

### 1. Config surface (`src/gamarr/config.py`)

- `MetacriticPlatformConfig` gains `mode: Literal["latest", "backlog"] = "backlog"`.
  Default `backlog` preserves today's behaviour, so the migration only adds the key and users
  keep their current semantics until they opt in.
- No other key changes meaning at the config level. Per-mode semantics are applied in the
  pipeline and documented in the README:

  | Key | `backlog` | `latest` |
  | ----- | ----------- | ---------- |
  | `max_pages` | total sweep budget (today) | depth: first N pages (N is read as-is; only `0` is clamped, see below) |
  | `max_cycle_pages` | per-cycle pace (page mode) | inert |
  | `max_date` | floor of the sweep | floor of the window (informational; depth decides) |
  | `max_cycle_months` | per-cycle pace (date mode) | inert |
  | `sort_order` | all three values | must be `new`; otherwise warned and coerced |

- **Inert-key warnings**: latest mode logs `max_cycle_pages ignored in latest mode` and
  `max_cycle_months ignored in latest mode` once per cycle, matching the existing
  `max_pages … unused in date mode` style (`pipeline.py:311-320`).
- **Depth clamp**: `max_pages: 0` in latest mode means "unbounded", which is just backlog;
  latest clamps `0` to `10` and logs a warning naming both the clamped value and the reason.
  The clamp lives in the pipeline normalisation next to the existing `max_date` /
  `max_cycle_months` guards (`pipeline.py:225-241`), so direct API callers get it too.
- `run_acquisition` and `AcquisitionConfig` gain `mode: Literal["latest", "backlog"] =
  "backlog"`; `scheduler._build_kwargs` forwards it.

### 2. Mode resolution (`src/gamarr/pipeline.py`)

One predicate, one branch. No second copy of the year loop.

```text
use_cursors = (cfg.mode == "backlog")
depth_pages = cfg.max_pages            # latest only, already clamped
scan_order  = "new" if cfg.mode == "latest" else cfg.sort_order
              (warn once per cycle when a score sort was coerced)
```

- **Latest**: for each year in the window (newest first), read from `start_page = 1`, scan
  until `depth_pages` pages have been read across the cycle, and stop. `get_last_scanned_page`
  / `set_last_scanned_page` are not called; `reset_progress` is never called for pacing.
  The date floor from `max_date` still bounds the year range, and the floor/pacing cutoffs
  already implemented (`cutoff_date`, `max_month_span`) apply unchanged.
- **Backlog**: unchanged — cursor sweep, `max_date` floor, `max_cycle_months` /
  `max_cycle_pages` pacing, drained/floor reset.
- **Logging**: latest replaces the page-budget percentage line with
  `Latest scan: pages 1..N of <window>, M collected, K queued`; backlog keeps today's lines.
- The mode is reported once per cycle (`Discovery mode: latest (depth 10 pages)` /
  `Discovery mode: backlog`) so the log always states which mode ran.

### 3. Verify before queueing (`src/gamarr/pipeline.py`)

`_process_browse_games` gains a detail-verification step between the cheap filters and
`db.record_pending`:

```text
for game in browse_games:
    cheap filters (unchanged): eligibility, reject_title, browse review counts
    result = mc.lookup_game(title, platform, cache_details_days, cache_pages_hours)
    if result passes thresholds and has review data:
        db.record_pending(..., metascore=result.metascore, ..., score_checks_passed=True)
    else:
        # not inserted, not marked known -> re-evaluated on the next pass
        log at DEBUG with the reason
```

- **Cost bound**: the cheap filters already reduce ~800 browsed games to a handful of new
  candidates per cycle (measured: 3–11). Detail lookups also pass through
  `game_detail_cache` (`cache_details_days`, default 7), so a game that reappears every cycle
  is a cache hit, not a fresh request.
- **No schema change.** The existing `score_checks_passed` column carries the meaning it
  already has; a row in `pending_games` now always means "verified, awaiting a source match".
- **`_verify_pending_scores` stays** for defensive re-verification of rows that lose their
  verified state (score edits, cache expiry) and for games whose scores were unavailable when
  queued — but its steady-state workload drops to the few verified rows awaiting a match.
- `max_queue_days` keeps its meaning for verified rows (the source-matching window), so a
  verified game that never matches still expires. Because verify-before-queue inserts every
  row already verified, the *insert* expiry is the source-matching window
  (`fitgirl_max_queue_days`), not the Metacritic-side `max_queue_days`; that key now only
  affects legacy rows queued unverified. The window is refreshed only when a row *newly*
  qualifies (`score_checks_passed` was not already true), because re-verification runs every
  cycle and refreshing on each pass would push the expiry forward forever.

### 4. Keyword matcher (`src/gamarr/pipeline.py`)

Two changes in `_check_reject_keywords` and its helpers:

- **Scope**: extract the FitGirl page `<title>` and the **Repack Features** block only.
  The block starts at the "Repack Features" heading and ends at the first known section
  heading (`Screenshots`, `System Requirements`, `Backwards Compatibility`,
  `Game Description`, `Included DLCs`, `Download Mirrors`) or after 4000 characters,
  whichever comes first — so a page missing the following heading cannot silently scan the
  whole article. (The extracted article text has whitespace collapsed, so a
  colon-terminated heading line cannot be detected; the known-heading list replaces it.)
  The `Backwards Compatibility` truncation is removed (`_truncate_at_backwards_compat` is
  deleted with its call site), because the scope now excludes both the false-positive
  boilerplate above the block and the historical references below it.
  Pages whose features block cannot be found fall back to title-only matching and log at
  WARNING (a title-only match can miss an HV repack, so the degradation is visible); they are
  never blocked on an absent block.
- **Match rule**: whole-word, negation-aware.

  ```text
  match(keyword, text):
      for each \b<keyword>\b occurrence in text (case-insensitive):
          inspect the 25 characters immediately before the match, or the text since the
          sentence start when that is closer than 25 characters
          if those characters end with "non-", "without ", "no ", or "not " -> skip
          else -> match
  ```

  Worked examples (from the real pages): `non-HV repack` → no match;
  `Hypervisor Bypass … applied` → match; `easier HV steps processing` → match.
- The rule is implemented as one pure function (text + keywords → matched keyword or None)
  so it is unit-testable without network access.
- **Expected effect, stated honestly**: false-positive pages (RE Requiem, Forza Horizon 6,
  Sonic Racing, Shinobi) become matchable; genuine HV repacks (Wukong, Metaphor, Crimson
  Desert) stay rejected by explicit user decision.

### 5. One-time queue migration (`src/gamarr/database.py`)

- On first open of an existing database, delete rows that can never match:

  ```sql
  DELETE FROM pending_games WHERE score_checks_passed IS NOT TRUE;
  ```

  1,555 rows → 21 on the live instance (the verified candidates).
- Guarded so it runs exactly once: a `PRAGMA user_version` stamp (set to the new value after
  the delete) rather than a heuristic on row counts, so re-opening the database, or a fresh
  database, never repeats it.
- Logged once at INFO: `Queue migration: removed N unverified pending rows (M verified kept)`.
- Documented in the README's migration notes, because it is destructive and surprising:
  a user upgrading mid-sweep loses unverified queue rows (they are re-discovered by the next
  pass, since they were never marked known).

## Error handling

| Condition | Behaviour |
| ----------- | ----------- |
| Detail lookup fails (network/parse) during queueing | Not inserted; retried on the next pass (never marked known) |
| Detail lookup returns no review data yet | Not inserted; retried on the next pass |
| Features block missing on a FitGirl page | Title-only keyword matching, DEBUG log, never blocks |
| `mode: latest` with a score sort | WARN once per cycle, scan in `new` order for that cycle |
| `mode: latest` with `max_pages: 0` | WARN, clamp depth to 10 |
| `max_pages`/`max_cycle_*` set but inert in the active mode | INFO once per cycle, naming the key and the reason |
| Queue migration fails | WARNING, leave the rows, continue; retried on the next open |
| Invalid `mode` value | Config load fails validation with the Literal message |

## Testing

- `tests/unit/test_pipeline.py`
  - Keyword matcher table: `non-HV repack` (no match), `without HV` (no match),
    `Hypervisor Bypass … applied` (match), `easier HV steps processing` (match),
    `hypervisor` inside a word (no match), missing block (title-only), empty keywords.
  - Scope extraction: features block bounded by the next heading; boilerplate above the
    block is not scanned; title always scanned.
  - Mode resolution matrix: latest/backlog × page/date window; latest ignores cursors
    (`get_last_scanned_page` never called); latest stops at the depth; latest warns and
    coerces a score sort; inert-key logs.
  - Verify-before-insert: passes → inserted with `score_checks_passed=True` and real scores;
    fails → not inserted; lookup returns `None` → not inserted and re-attempted next cycle;
    a known slug is still skipped.
- `tests/unit/test_database.py`
  - Queue migration: removes unverified rows, keeps verified ones, records
    `PRAGMA user_version`, does not run twice.
- `tests/unit/test_config.py` — `mode` default and validation; per-platform override.
- `tests/unit/test_scheduler.py` — `_build_kwargs` forwards `mode`.
- `tests/unit/test_readme_accuracy.py` — new `mode` row matches the model default.

## Files

| File | Change |
| ------ | -------- |
| `src/gamarr/config.py` | `mode` field on `MetacriticPlatformConfig` |
| `src/gamarr/pipeline.py` | mode resolution and latest depth; verify-before-insert in `_process_browse_games`; keyword scope + whole-word/negation matcher; remove `_truncate_at_backwards_compat`; mode/depth logging; inert-key warnings |
| `src/gamarr/database.py` | one-time `pending_games` cleanup guarded by `PRAGMA user_version` |
| `src/gamarr/scheduler.py` | `_build_kwargs` forwards `mode` |
| `README.md` | `mode` row, per-mode key meanings, keyword-scope note, queue-migration note |
| `tests/unit/test_pipeline.py`, `test_config.py`, `test_database.py`, `test_scheduler.py`, `test_readme_accuracy.py` | see Testing |

`src/gamarr/metacritic.py` is unchanged: the latest mode reuses the existing year loop,
cutoff handling and detail cache.

## Out of scope

- **Source-first discovery** (scanning FitGirl/FreeGOG for new repacks before Metacritic).
  The user chose to keep discovery Metacritic-first.
- **Accepting HV repacks.** Rejection is deliberate; Wukong/Metaphor/Crimson Desert stay
  excluded. Revisit only as a separate decision.
- Making `sort_order: criticscore` / `userscore` a *filter* rather than an order. Score
  filtering already happens at verification.
- Per-mode scheduling intervals (latest and backlog run from whichever `mode` is configured).
- Changing `max_queue_days` semantics for verified rows.
