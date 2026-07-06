# Logging Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace gamarr's verbose, unclear INFO-level logging with structured phase banners, one-liner results per phase, and contextual explanations so a new user can follow what gamarr is doing.

**Architecture:** Each pipeline phase gets a banner (`--- Phase N/5: ... ---`). Phase 1 adapts between steady-state (compact) and backlog (page progress every 100 pages). FreeGOG batch progress is removed. Phase 4 keeps per-game match/skip lines but adds a summary. Changed messages are mostly in `pipeline.py` and `metacritic.py`.

**Tech Stack:** Python 3.12+, loguru, pytest with caplog / io.StringIO capture

---

### Task 1: Metacritic — heartbeat + cutoff + summary

**Files:**
- Modify: `src/gamarr/metacritic.py:796-835`
- Test: `tests/unit/test_metacritic.py:950-990`

This task changes the `scan_recent_games()` progress logging:
- The 100-page "Fetched X Metacritic pages" message → only fires in backlog mode (gated by a `backlog_scan` parameter)
- The "page X reached... stopping scan" message → moves to DEBUG
- The final "Scanned X Metacritic page(s)" summary → becomes "X pages browsed, Y games collected"

- [ ] **Step 1: Update metacritic test to expect new message format**

In `tests/unit/test_metacritic.py`, update `test_scan_recent_games_logs_batch_progress` at line ~986:

```python
# OLD assertion (remove this):
assert "Fetched 100 Metacritic pages —" in log_output

# NEW assertions:
assert "100 pages browsed" in log_output or "page 100" in log_output
assert "Scanned 150 Metacritic page(s) — collected 3000 games" in log_output
```

But wait — the "Fetched 100" message will only appear in backlog mode now. The test uses `max_games=0` with no cutoff, which should behave like backlog. Let me check: if `cutoff_date` is None, what happens? Looking at the code, `cutoff_date=None` means no date cutoff, so `_page_is_before_cutoff` returns False, and the scan continues until pages run out. But there's no `backlog_scan` parameter currently — we need to add one.

Actually, the heartbeat should fire whenever the scan is in "large scan" mode — determined by whether `cutoff_date` is set (indicating a date-bound scan) AND whether it's a backlog scan. In steady-state, `cutoff_date` is set but the window is small (4 weeks). In backlog, `cutoff_date` is set and the window is large.

The simplest approach: pass a boolean `show_progress` parameter to `scan_recent_games()` that controls whether the 100-page heartbeat fires. The caller (`pipeline.py`) sets it based on whether it's in backlog mode.

Update the test:

```python
def test_scan_recent_games_logs_batch_progress(self) -> None:
    """scan_recent_games should log periodic progress when show_progress=True."""
    # ... setup same as before ...
    
    log_stream = io.StringIO()
    handler_id = logger.add(log_stream, format="{message}", level="INFO")
    try:
        with patch.object(client, "_fetch_browse_page", side_effect=pages):
            client.scan_recent_games("pc", max_games=0, show_progress=True)
    finally:
        logger.remove(handler_id)

    log_output = log_stream.getvalue()

    # NEW: final summary uses "browsed" and "collected" wording
    assert "150 pages browsed, 3000 games collected" in log_output, (
        f"Expected final summary in:\n{log_output}"
    )

    # With show_progress=True, the 100-page heartbeat should appear
    assert "page 100 (2000 games)" in log_output, (
        f"Expected heartbeat 'page 100 (2000 games)', got:\n{log_output}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_metacritic.py::TestScanRecentGamesLogging::test_scan_recent_games_logs_batch_progress -v
```
Expected: FAIL — asserts on new message format that doesn't exist yet.

- [ ] **Step 3: Modify scan_recent_games() in metacritic.py**

Add `show_progress: bool = False` parameter to `scan_recent_games()`.

Change the page-progress heartbeat (line ~800):

```python
# OLD:
if page_number % 100 == 0:
    logger.info(
        "Fetched {} Metacritic pages — {} games collected",
        page_number,
        len(all_games),
    )

# NEW:
if show_progress and page_number % 100 == 0:
    logger.info(
        "Backlog scan: page {} ({} games)",
        page_number,
        len(all_games),
    )
```

Change the cutoff-reached message (line ~796):

```python
# OLD:
logger.info(
    "Metacritic page {} reached (no games within the scan window, cutoff: {}); stopping scan",
    page_number,
    cutoff_date,
)

# NEW:
logger.debug(
    "Metacritic page {} reached (no games within the scan window, cutoff: {}); stopping scan",
    page_number,
    cutoff_date,
)
```

Change the final summary (line ~831):

```python
# OLD:
logger.info(
    "Scanned {} Metacritic page(s) — collected {} games",
    n_pages,
    len(all_games),
)

# NEW:
logger.info(
    "Scan result: {} pages browsed, {} games collected",
    n_pages,
    len(all_games),
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_metacritic.py::TestScanRecentGamesLogging::test_scan_recent_games_logs_batch_progress -v
```
Expected: PASS — new messages match new assertions.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/metacritic.py tests/unit/test_metacritic.py
git commit -m "refactor: metacritic logging — heartbeat gated, cutoff→DEBUG, summary rewording"
```

---

### Task 2: FreeGOG — remove batch progress, rewrite summary

**Files:**
- Modify: `src/gamarr/sources/freegog.py:272-291`
- Test: `tests/unit/test_freegog.py:520-554`

Remove the 11-line "processing entries" batch progress. Rewrite the summary for clarity.

- [ ] **Step 1: Update freegog test to remove batch progress assertions**

In `tests/unit/test_freegog.py`, find `test_logs_batch_progress_instead_of_per_letter` (~line 520). Replace the batch progress assertions:

```python
# REMOVE these lines:
# batch_calls = [call for call in info_calls if "entries" in str(call.args[0]) and "of" in str(call.args[0])]
# assert len(batch_calls) >= 1
# assert any(call.args[1] == 1 and call.args[2] == 500 for call in batch_calls)
# assert any(call.args[1] == 501 and call.args[2] == total_entries for call in batch_calls)

# NEW: verify NO batch progress messages exist
batch_calls = [call for call in info_calls if "entries" in str(call.args[0]) and "of" in str(call.args[0])]
assert len(batch_calls) == 0, f"Batch progress logs should be removed, found: {batch_calls}"

# Summary message format changes — update assertion
summary_calls = [
    call for call in info_calls if "new games found" in str(call.args[0])
]
assert len(summary_calls) == 1, f"Expected one summary log, got: {summary_calls}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_freegog.py::TestFreeGOGFetchSitemap::test_logs_batch_progress_instead_of_per_letter -v
```
Expected: FAIL — batch calls still present, new summary wording absent.

- [ ] **Step 3: Modify fetch_sitemap() in freegog.py**

Remove the batch progress logging (lines ~272-278):

```python
# REMOVE this block entirely:
#             if entry_idx % 500 == 0:
#                 batch_start = entry_idx + 1
#                 batch_end = min(entry_idx + 500, total_entries)
#                 logger.info(
#                     "FreeGOG: processing entries {}-{} of {}...",
#                     batch_start,
#                     batch_end,
#                     total_entries,
#                 )
```

Rewrite the summary (lines ~291-294):

```python
# OLD:
logger.info(
    "FreeGOG indexed {} new games ({} skipped, already known)",
    new_count,
    known_count,
)

# NEW — show entries checked count:
if new_count > 0:
    logger.info(
        "FreeGOG: {} new games found ({} entries checked, {} already known)",
        new_count,
        total_entries,
        known_count,
    )
else:
    logger.info(
        "FreeGOG: all {} entries already known — nothing new",
        total_entries,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_freegog.py::TestFreeGOGFetchSitemap::test_logs_batch_progress_instead_of_per_letter -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/sources/freegog.py tests/unit/test_freegog.py
git commit -m "refactor: freegog logging — remove batch progress, clarify summary"
```

---

### Task 3: FitGirl — align summary wording

**Files:**
- Modify: `src/gamarr/sources/fitgirl.py:296,320-325`
- Test: `tests/unit/test_fitgirl.py` (no log message assertions changed — safe)

Minor wording alignment with FreeGOG style. No test changes needed (current fitgirl tests don't assert exact log message text for summary lines).

- [ ] **Step 1: Modify fetch_sitemap() summary in fitgirl.py**

Line ~296 — after indexing:

```python
# OLD:
logger.info("FitGirl sitemap indexed {} game titles", len(titles))

# NEW:
logger.info("FitGirl: {} new game titles indexed", len(titles))
```

Line ~325 — cache skip message has good wording already but let's align style:

```python
# OLD (keep as-is — user rated this "good - very clear"):
# logger.info(
#     "FitGirl cache is still valid — expires at {} — skipping fetch",
#     expires_at.replace(microsecond=0).isoformat(sep=" "),
# )

# No change needed — this message is already good.
```

Actually, looking at the current code for both FitGirl and FreeGOG — the "cache still valid" message appears in the source files AND in pipeline.py. Let me check...

Looking at the user's log output: "FitGirl cache is still valid — expires at 2026-07-06 09:30:54 — skipping fetch" — this is clearly in the source files. The current wording is already good per the user's feedback. No change needed for this message.

The summary line `"FitGirl sitemap indexed {} game titles"` can be aligned to `"FitGirl: {} new game titles indexed"` for consistency.

- [ ] **Step 2: Verify no tests break**

```bash
uv run pytest tests/unit/test_fitgirl.py -v -q
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/sources/fitgirl.py
git commit -m "refactor: fitgirl logging — align summary wording with other sources"
```

---

### Task 4: Pipeline — phase banners, rewritten messages

**Files:**
- Modify: `src/gamarr/pipeline.py:186-189, 335-362, 404-462, 520-523, 1208-1258, 1736-1816, 1902, 1931`
- Test: `tests/unit/test_pipeline.py:59-85, 5127, 5188, 5258`

This is the largest task — adding phase banners and rewriting messages throughout the pipeline.

- [ ] **Step 1: Update pipeline backlog tests to match new wording**

In `tests/unit/test_pipeline.py`, find tests asserting `"Backlog cycle"`:

**Test at line ~85:**
```python
# OLD:
assert "Backlog cycle 1" in log_text

# NEW — message format changed to include "window" and "remaining":
# The exact format is: "Backlog scan: cycle X/Y, window DATE → DATE (Z cycles remaining)"
assert "Backlog scan" in log_text
assert "cycle" in log_text
```

**Test at line ~5127:**
```python
# OLD:
assert "Backlog cycle 1" in log_output

# NEW:
assert "Backlog scan" in log_output
assert "cycle 1" in log_output or "cycle" in log_output
```

**Test at line ~5188:**
```python
# OLD:
assert "Backlog cycle" in log_output

# NEW:
assert "Backlog scan" in log_output
```

**Test at line ~5258:**
```python
# OLD:
assert "Backlog cycle" in log_output

# NEW:
assert "Backlog scan" in log_output
```

- [ ] **Step 2: Run backlog tests to verify they fail**

```bash
uv run pytest tests/unit/test_pipeline.py::TestMaxCycleWeeks::test_max_cycle_weeks_lower_than_max_weeks_uses_cycle_cutoff tests/unit/test_pipeline.py::TestScanWindowAdvancing -v
```
Expected: FAIL — "Backlog cycle" no longer exists in log output.

- [ ] **Step 3: Modify pipeline.py — Phase 1 banner and messages**

The Phase 1 code lives in `run_acquisition()` around lines 186-462. Apply these changes in sequence:

**Phase 1 banner** — add before the Metacritic fetch line (line 186):

```python
# ADD after the qbt connectivity check, before the scan setup:
logger.info("--- Phase 1/5: Discovering games on Metacritic ---")
```

**Scan window message** — replace the current "Scanning latest X weeks" / "Backlog cycle" message block (lines ~335-362) with the new format. The existing code has two branches (steady-state and backlog) with multiple logger.info() calls. Replace them:

For the **backlog branch** (around lines 350-362):
```python
# OLD backlog branch (remove all these logger.info calls):
# logger.info(
#     "Backlog cycle {} — scanning {} to {} — ~{} cycles remaining",
#     ...
# )

# NEW single message:
logger.info(
    "Backlog scan: cycle {}/{} — window {} → {} ({} cycles remaining)",
    cycle_number,
    total_cycles,
    cutoff_date,
    today.isoformat(),
    remaining,
)
```

For the **steady-state branch** (around lines 335-340):
```python
# OLD:
# logger.info(
#     "Scanning latest {} weeks ({} to {})",
#     window_weeks or cfg.max_cycle_weeks or 4,
#     cutoff_date,
#     today.isoformat(),
# )

# NEW with config context:
logger.info(
    "Scan window: last {} weeks ({} → {})  [max_weeks={}, cycle={}]",
    window_weeks or cfg.max_cycle_weeks or 4,
    cutoff_date,
    today.isoformat(),
    cfg.max_weeks or 104,
    effective_cycle_weeks or 4,
)
```

**Pass show_progress=True** to `scan_recent_games()` for backlog scans. Find the call:

```python
# Find: mc.scan_recent_games(platform, ...)
# Add: show_progress=True when in backlog mode

# Around line ~370, the scan_recent_games call:
browse_games = mc.scan_recent_games(
    platform,
    cache_pages_hours=cfg.cache_pages_hours,
    cutoff_date=cutoff_date,
    cancel_event=cancel_event,
    start_page=browse_start_page,
    show_progress=(not clamped_by_max_weeks),  # NEW: heartbeat only in backlog
)
```

Wait — `clamped_by_max_weeks` indicates steady-state (backlog caught up). In backlog mode, `clamped_by_max_weeks` is False. So `show_progress = not clamped_by_max_weeks` is correct.

**Queue status message** — after browse processing (around line ~462), replace:

```python
# OLD (needs context — currently not logged as a breakdown):
# The queue status is embedded in individual messages, not summarised.

# NEW — add after processing browse games, before verification:
pending = db.get_pending(platform=platform)
new_count = len(browse_games) if browse_games else 0  # games from this scan
existing_count = len(pending) - new_count if len(pending) >= new_count else len(pending)
logger.info(
    "Pending queue: {} new + {} from previous cycles = {} total",
    new_count,
    max(0, existing_count),
    len(pending),
)
```

Wait — actually the new games added this cycle aren't tracked separately from pending that already existed. Let me look at how browse processing works...

The `_process_browse_games()` function inserts new pending entries. It returns the number actually added via `len(new_pending)`. So we can capture that:

```python
# After _process_browse_games returns:
new_pending = _process_browse_games(...)
existing_pending = db.get_pending(platform=platform)
logger.info(
    "Pending queue: {} new + {} from previous cycles = {} total",
    len(new_pending),
    len(existing_pending) - len(new_pending),
    len(existing_pending),
)
```

Actually, `_process_browse_games` returns a list of result dicts, not just new pending entries. Let me look...

Reading the code at line ~404-438, the function returns `results` which includes all processed games (new pending + skipped + already known). The actual new pending count is tracked via `inserted` variable inside the function. This is getting complex. Let me simplify:

```python
# Before browse processing:
pending_before = len(db.get_pending(platform=platform))

# ... browse processing happens ...

# After:
pending_after = len(db.get_pending(platform=platform))
new_this_cycle = max(0, pending_after - pending_before)
logger.info(
    "Pending queue: {} new + {} from previous cycles = {} total",
    new_this_cycle,
    pending_before,
    pending_after,
)
```

This is simpler and more accurate. Let me use this approach.

**Score check summary** — after verification (around line ~1258), replace:

```python
# OLD:
# logger.info(
#     "Score verification: {} checked — {} from cache, {} attempted",
#     checked,
#     mc.cache_hits,
#     max(0, checked - int(mc.cache_hits)),
# )

# NEW:
logger.info(
    "Score check: {} verified ({} from cache, {} from Metacritic API)",
    checked,
    mc.cache_hits,
    max(0, checked - int(mc.cache_hits)),
)
```

- [ ] **Step 4: Modify pipeline.py — Phase 2 banner**

Add before FreeGOG indexing call (find in `run_acquisition()` where `freegog_source.fetch_sitemap()` is called):

```python
# ADD before the FreeGOG sitemap fetch:
logger.info("--- Phase 2/5: Indexing FreeGOG ---")
```

- [ ] **Step 5: Modify pipeline.py — Phase 3 banner**

Add before FitGirl indexing call:

```python
# ADD before the FitGirl sitemap fetch:
logger.info("--- Phase 3/5: Indexing FitGirl ---")
```

- [ ] **Step 6: Modify pipeline.py — Phase 4 banner and match summary**

Add before the match loop (in `_match_pending_games()` or just before it's called):

```python
# ADD before the match-pending call:
logger.info("--- Phase 4/5: Matching games to download sources ---")
```

Add a match summary after the match loop. In `_match_pending_games()` (around line ~520-523), replace:

```python
# OLD:
# logger.info("{} queued games found on {}", len(source_matched), source_entry.name)
# logger.info("Total: {} queued games found across all sources", len(matched))

# NEW — combine into one summary per source + grand total:
delivered = len([r for r in matched if r.get("result") == "Passed"])
no_match = len([r for r in matched if r.get("result") != "Passed"])
rejected = sum(1 for r in results if r.get("result") == "Rejected")
logger.info(
    "Match summary: {} delivered, {} no match, {} rejected by keywords",
    delivered,
    no_match,
    rejected,
)
```

Wait, this is getting complex. The match code in `_match_pending_games()` dispatches per-source and accumulates results. Let me look at the actual structure more carefully...

Actually, looking at the code flow more carefully, the match happens in the `for source_entry in ...` loop inside `run_acquisition()`. The results are accumulated. Let me keep it simpler — add a summary at the end of matching after all sources have been checked.

Actually, let me look at the actual source structure. In `run_acquisition()`, the matching happens inside a loop over download sources. After the loop, there's a summary already: `"Total: {} queued games found across all sources".format(len(matched))`. But "queued games" is the wrong term — these are matched games.

Let me check if there's a simpler way. The spec just says: "Match summary: 1 delivered, 6834 no match, 0 rejected by keywords". But `_match_pending_games()` doesn't track "no match" per game — unmatched games are simply touched and left in pending. Only matched games accumulate in `source_matched` per source.

Let me simplify the summary. After all sources are processed:

```python
# After the for source_entry loop, replace the existing total log:
# OLD:
logger.info("Total: {} queued games found across all sources", len(matched))

# NEW:
logger.info("Match summary: {} game(s) matched across all sources", len(matched))
```

Per-game match/skip messages stay as-is (user rated them "good"). Only the end-of-phase summary changes.

- [ ] **Step 7: Modify pipeline.py — Phase 5 banner**

Add before delivery loop:

```python
# ADD before the delivery section (or before we show results):
if not matched:
    logger.info("--- Phase 5/5: Delivering to qBittorrent ---")
    logger.info("No matches to deliver this cycle.")
else:
    logger.info("--- Phase 5/5: Delivering to qBittorrent ---")
```

The "no matches" case uses the separate message. The per-game "✓ sent to qBittorrent" messages stay as-is.

- [ ] **Step 8: Run pipeline tests to verify**

```bash
uv run pytest tests/unit/test_pipeline.py -v -q --tb=short
```
Expected: most pass, some may fail on exact wording assertions. Fix any remaining assertion mismatches.

- [ ] **Step 9: Commit**

```bash
git add src/gamarr/pipeline.py tests/unit/test_pipeline.py
git commit -m "feat: phase-banner logging with backlog heartbeat and rewritten messages"
```

---

### Task 5: Full integration verification

**Files:**
- Verify: `tests/unit/test_notifications.py`, `tests/unit/test_metacritic.py`, `tests/unit/test_freegog.py`, `tests/unit/test_fitgirl.py`, `tests/unit/test_pipeline.py`

Run the full test suite, fix any remaining test failures from changed log messages.

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest tests/unit/ -v --tb=short
```

- [ ] **Step 2: Fix any remaining test assertion mismatches**

Any test that asserts on exact log message text that was changed should be updated to match the new format.

- [ ] **Step 3: Run ruff + mypy**

```bash
uv run ruff check --fix . && uv run ruff format . && uv run mypy .
```
Expected: all clean.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "test: update log message assertions for logging overhaul"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md` (if logging is mentioned in features)

- [ ] **Step 1: Check README for logging references**

```bash
grep -n "log\|logging\|log output" README.md
```

If logging is described, update the description to mention the phase-banner structure. If not mentioned, no change needed.

- [ ] **Step 2: Commit (if changes made)**

```bash
git add README.md && git commit -m "docs: update README for phase-banner logging"
```
