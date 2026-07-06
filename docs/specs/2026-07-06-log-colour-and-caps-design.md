# Log Phase Colour & Source Name Capitalisation

**Date:** 2026-07-06
**Status:** Approved

## Motivation

Current INFO-level log output has two readability issues:

1. **Phase banners are invisible.** The five pipeline stages
   (`--- Phase 1/5: ... ---`) blend in with surrounding log lines. On a
   scrolling terminal, a user cannot quickly spot where each phase starts.

2. **Source names use inconsistent capitalisation.** The internal `source_name`
   parameter (`"fitgirl"`, `"freegog"`) leaks into user-facing log messages
   as lowercase or `str.title()` variants (`"Freegog"`, `"Fitgirl"`). The
   correct brand capitalisation is **FreeGOG** and **FitGirl**.

## Design

### Colour Palette — Phase Banners Only

Only the five `--- Phase N/5: ... ---` banner lines receive colour and border
treatment. All other log lines (scan summaries, match results, delivery
confirmations) remain the default colour.

The `---` separators are replaced with Unicode box-drawing horizontal rules
(U+2501 × 3 each side) for visual weight. The banner message is wrapped in
Loguru `<color>` markup:

| Phase | Loguru Markup | Description |
|-------|-------------|-------------|
| 1/5 | `<yellow>━━━ Phase 1/5: Discovering games on Metacritic ━━━</>` | Metacritic scan |
| 2/5 | `<cyan>━━━ Phase 2/5: Indexing FreeGOG ━━━</>` | FreeGOG sitemap |
| 3/5 | `<light-red>━━━ Phase 3/5: Indexing FitGirl ━━━</>` | FitGirl sitemap |
| 4/5 | `<magenta>━━━ Phase 4/5: Matching games to download sources ━━━</>` | Game matching |
| 5/5 | `<blue>━━━ Phase 5/5: Delivering to qBittorrent ━━━</>` | Torrent delivery |

Loguru's built-in colour names are used for simplicity: `yellow`, `cyan`,
`light-red` (the closest available to orange), `magenta`, `blue`. No hex or
ANSI escape codes are needed.

### Source Name Capitalisation

A small lookup function maps internal lowercase names to display names:

```python
_SOURCE_DISPLAY = {"fitgirl": "FitGirl", "freegog": "FreeGOG"}

def _source_display(name: str) -> str:
    """Return the display-cased form of a source name."""
    return _SOURCE_DISPLAY.get(name, name.title())
```

This replaces raw `source_name` / `source_entry.name` usage at four log
sites (see Implementation section). It does **not** replace the internal
`source_name` variable — the database and matching logic continue to use
lowercase (`"fitgirl"`, `"freegog"`).

### Scope Boundary

- **In:** `src/gamarr/pipeline.py` — phase banners and source name display
- **Out:** `src/gamarr/metacritic.py` — no changes (phase 1 sub-messages stay plain)
- **Out:** `src/gamarr/notifications.py` — already fixed in commit `7af760a`
- **Out:** `src/gamarr/sources/` — already fixed in commit `3754af8`

## Implementation

### Phase Banners

Replace the five `logger.info("--- Phase N/5: ... ---")` calls with
colour-wrapped equivalents:

```python
# Line ~565: Phase 1
logger.info("<yellow>━━━ Phase 1/5: Discovering games on Metacritic ━━━</>")

# Line ~526: Phase 2
logger.info("<cyan>━━━ Phase 2/5: Indexing FreeGOG ━━━</>")

# Line ~528: Phase 3
logger.info("<light-red>━━━ Phase 3/5: Indexing FitGirl ━━━</>")

# Line ~533: Phase 4
logger.info("<magenta>━━━ Phase 4/5: Matching games to download sources ━━━</>")

# Line ~550: Phase 5
logger.info("<blue>━━━ Phase 5/5: Delivering to qBittorrent ━━━</>")
```

### Source Name Capitalisation

Add the `_source_display()` helper (module-level, near other helpers), then
fix the four affected log sites:

**Site 1** (line ~1841-1843) — no-match message:
```python
# Before
logger.info(
    "'{}' passed Metacritic checks but has no {} match \u2014 staying in queue",
    game_title,
    source_name,
)
# After
logger.info(
    "'{}' passed Metacritic checks but has no {} match \u2014 staying in queue",
    game_title,
    _source_display(source_name),
)
```

**Site 2** (line ~1848-1850) — match message:
```python
# Before
logger.info(
    "{} match: '{}' \u2192 '{}' ({})",
    source_name.title(),
    game_title, best["title"], best["url"],
)
# After
logger.info(
    "{} match: '{}' \u2192 '{}' ({})",
    _source_display(source_name),
    game_title, best["title"], best["url"],
)
```

**Site 3** (line ~547) — per-source match count:
```python
# Before
logger.info("{} queued games found on {}", len(source_matched), source_entry.name)
# After
logger.info("{} queued games found on {}", len(source_matched), _source_display(source_entry.name))
```

**Site 4** (line ~1993) — result details for no-downloader mode:
```python
# Before
result_details=f"Matched on {source_name}: {best['url']}",
# After
result_details=f"Matched on {_source_display(source_name)}: {best['url']}",
```

## Testing

### Test Impact

`tests/unit/test_pipeline.py` — update assertions that match phase banner or
source name text in captured log output:

- `"--- Phase 1/5:"` → `"<yellow>━━━ Phase 1/5:"`
- `"--- Phase 2/5:"` → `"<cyan>━━━ Phase 2/5:"`
- `"no fitgirl match"` → `"no FitGirl match"`
- `"no freegog match"` → `"no FreeGOG match"`

Tests in `test_metacritic.py` and `test_notifications.py` are unaffected —
Metacritic messages didn't change, and notification formatting was already
fixed in a prior commit.

### TDD Approach

1. **RED** — update test assertions to expect the new coloured/capitalised
   format. Verify tests fail against current code.
2. **GREEN** — apply the code changes (phase banners + `_source_display`).
3. **VERIFY** — run `uv run pytest -v` to confirm all tests pass.

## File Output

The log file sink does not have `colorize=True`, so Loguru markup tags
(`<yellow>`, `<cyan>`, …) render literally in the log file. This is existing
behaviour (the format string itself contains `<green>{time}</green>`) and is
not addressed by this change. The file remains grep-able and the phase
delimiters (`━━━`) are plain Unicode.