# Pretty Path Case for Library Template Variables

## Overview

Add a `path_case` config field to `PostProcessConfig` that controls how
`{site}`, `{platform}`, `{genre}`, and `{title}` are formatted in the
library destination path. Two modes: `"pretty"` (default) uses
display-friendly names; `"lowercase"` downcases everything.

## Motivation

Currently `{site}` and `{platform}` use raw internal values:
`fitgirl`/`freegog` and `pc`. Users want display-friendly directory
names like `FitGirl`/`FreeGOG` and `PC`.

Platform, genre, and title values from Metacritic are already
properly capitalized (`PC`, `Action,RPG`, `Elden Ring`). Only the
source name needs explicit mapping.

## Design

### Config

New field on `PostProcessConfig`:

```python
path_case: Literal["pretty", "lowercase"] = "pretty"
```

```yaml
post_process:
  library_path: "/library/{site}/{platform}/{genre}/{title}"
  path_case: "pretty"   # "pretty" | "lowercase" — default: "pretty"
```

### Pretty-formatting logic

A single module-level lookup table in `post_processor.py` maps source
identifiers to their display names. Platform, genre, and title pass
through unchanged in pretty mode (already correctly capitalized from
Metacritic / user config).

```python
# Module-level table — maps internal source names to display names
_PRETTY_SOURCE = {"fitgirl": "FitGirl", "freegog": "FreeGOG"}
```

Formatting is applied per-key inside `_build_destination_path()`:

| `path_case` | site | platform | genre | title |
|---|---|---|---|---|
| `"pretty"` | Lookup table (`_PRETTY_SOURCE`); pass-through if unknown | Pass-through | Pass-through | Pass-through |
| `"lowercase"` | `.lower()` | `.lower()` | `.lower()` | `.lower()` |

Paths are sanitized by the existing `_safe_path_component()` after
formatting.

### Example

```
Template: /library/{site}/{platform}/{genre}/{title}

path_case: "pretty"
  fitgirl + PC + Action,RPG + Elden Ring  →  /library/FitGirl/PC/Action/RPG/Elden Ring
  freegog  + PC + Strategy  + Zelda       →  /library/FreeGOG/PC/Strategy/Zelda

path_case: "lowercase"
  fitgirl + PC + Action,RPG + Elden Ring  →  /library/fitgirl/pc/action/rpg/elden ring
  freegog  + PC + Strategy  + Zelda       →  /library/freegog/pc/strategy/zelda
```

### Files changed

| File | Change |
|------|--------|
| `src/gamarr/config.py` | Add `path_case: Literal["pretty", "lowercase"] = "pretty"` to `PostProcessConfig` |
| `src/gamarr/post_processor.py` | Add `_PRETTY_SOURCE` table. `_build_destination_path()` accepts `path_case` parameter and applies formatting per-key. |
| `tests/unit/test_post_processor.py` | Add tests: `_PRETTY_SOURCE` lookups, `_build_destination_path()` with both values, lowercasing, edge cases |

### Migration

The default changes from current raw-lowercase to `"pretty"`. This is
acceptable because post-processing is a new beta feature with early
adopters. Users who prefer lowercase set `path_case: "lowercase"`.

The `path_case` field is auto-migrated into existing configs by the
existing config migration system on first startup.

### Testing

- Unit tests for `_build_destination_path()`:
  - `pretty` with known source name → display name applied
  - `pretty` with unknown source name → pass-through
  - `pretty` with platform/genre/title → pass-through
  - `lowercase` → all values downcased
- Verify all 35 existing post_processor tests still pass
