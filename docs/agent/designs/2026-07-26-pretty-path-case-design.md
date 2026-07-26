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

### Config Migration

A migration function must be added to `config.py` to add the
`path_case` field with default `"pretty"` to existing configs that
do not yet have it. Follows the existing migration pattern: a new
function in the migration list inside `_migrate_config()`.

```python
def _migrate_add_post_process_path_case(raw: dict[str, Any]) -> bool:
    """Add path_case to post_process if missing."""
    pp = raw.get("post_process")
    if isinstance(pp, dict) and "path_case" not in pp:
        pp["path_case"] = "pretty"
        logger.info("Config: added post_process.path_case = 'pretty'")
        return True
    return False
```

Registered in the `_migrations` list inside `_migrate_config()`,
ordered after any migration that creates the `post_process` section.

### Default Config

`PostProcessConfig` defines the default:

```python
class PostProcessConfig(BaseModel):
    ...
    path_case: Literal["pretty", "lowercase"] = "pretty"
```

The YAML config file written by `create_default_config()` will
include the field automatically through Pydantic's `model_dump()`.
No manual YAML template change is needed — Pydantic handles the
serialization.

### Files changed

| File | Change |
|------|--------|
| `src/gamarr/config.py` | Add `path_case` field to `PostProcessConfig`. Add `_migrate_add_post_process_path_case()` migration function. |
| `src/gamarr/post_processor.py` | Add `_PRETTY_SOURCE` table. `_build_destination_path()` accepts `path_case` and applies formatting per-key. Caller in `_run_copy_phase()` passes `pp.path_case`. |
| `tests/unit/test_post_processor.py` | Add tests: `_PRETTY_SOURCE` lookups, `_build_destination_path()` with both values, lowercasing, edge cases |

### Testing

- Unit tests for `_build_destination_path()`:
  - `pretty` with known source name → display name applied
  - `pretty` with unknown source name → pass-through
  - `pretty` with platform/genre/title → pass-through
  - `lowercase` → all values downcased
- Migration test: config without `path_case` field → auto-added with value `"pretty"`
- Verify all 35 existing post_processor tests still pass
