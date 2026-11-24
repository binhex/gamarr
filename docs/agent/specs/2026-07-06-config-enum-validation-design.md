# Config Enum Validation

**Date:** 2026-07-06
**Status:** Approved

## Motivation

Several config fields accept a fixed set of valid values but are typed as bare
`str`, allowing invalid values to pass Pydantic validation silently. This caused
a bug where `sort_order: newest` (an incorrect value, not a real Metacritic
URL parameter) produced broken browse URLs that returned empty results,
triggering a phase skip with no error message.

Adding `Literal` types to enum-like fields catches typos and invalid values at
startup with clear Pydantic `ValidationError` messages.

## Design

### Fields to harden

Four fields in `src/gamarr/config.py` gain `Literal` annotations:

| Config model | Field | Current | New |
|---|---|---|---|
| `MetacriticPlatformConfig` | `sort_order` | `str = "new"` | `Literal["new", "metascore"] = "new"` |
| `GeneralConfig` | `log_level_console` | `str = "INFO"` | `Literal["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"] = "INFO"` |
| `GeneralConfig` | `log_level_file` | `str = "INFO"` | Same set as `log_level_console` |
| `TorrentClientConfig` | `selected` | `str = "qbittorrent"` | `Literal["qbittorrent"] = "qbittorrent"` |

Import: `from typing import Literal` is added to the existing imports.

### Fields intentionally not changed

- **`daemon_mode`** — deprecated. The `_migrate_daemon_mode` function converts
  `"background"` to `schedule.enabled`. Adding `Literal["foreground"]` would
  reject old configs before the migration runs. Left as plain `str`.
- **Numeric fields** — already have `Field(ge=0)` or `Field(gt=0)` constraints.
- **`apprise_urls`, `paths`, `reject_keywords`, etc.** — open-ended lists/strings
  with no fixed value set.

### Error behavior

Invalid values produce Pydantic `ValidationError` at `load_config()` time:

```
1 validation error for MetacriticPlatformConfig
sort_order
  Input should be 'new' or 'metascore' [type=literal_error, input_value='newest']
```

This surfaces on startup (both scheduled and `--test` mode), making it
immediately obvious when a config value is wrong.

### No CLI or loader changes

- No new CLI flags. No changes to `--test` behavior.
- `load_config()` and `model_validate` already reject invalid `Literal` values
  automatically — no extra validation code needed.

## Testing

One new test in `tests/unit/test_config.py` under `TestSortOrder`:

- `test_sort_order_rejects_invalid` — parametrized or explicit assertions that
  `MetacriticPlatformConfig(sort_order="newest")` raises `ValidationError`,
  along with other invalid values like `"score"`, `""`.

Existing tests for `log_level_console`, `log_level_file`, and `selected`
already construct models with valid values and will continue to pass unchanged.

## Scope boundary

- Config enum validation only — no CLI flags, no enhanced `--test` loop.
- No config migration for existing bad values (this is preventive; no known
  deployed configs use invalid enum values).
- No `daemon_mode` changes.
