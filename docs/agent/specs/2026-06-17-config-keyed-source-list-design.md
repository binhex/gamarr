# Config Format Redesign — Keyed Source List

**Date:** 2026-06-17
**Status:** Approved design

## Overview

Redesign the `download_sites` YAML config format so source names become
YAML keys instead of a `name` field. Also renames `rss_url` to `feed_url`
for consistency across sources (since DODI uses a scraped page URL, not
RSS).

---

## 1. YAML Config Format

**Current:**
```yaml
download_sites:
  - name: fitgirl
    enabled: true
    rss_url: https://fitgirl-repacks.site/feed/
    ...
  - name: dodi
    enabled: true
    ...
```

**New:**
```yaml
download_sites:
  - fitgirl:
      enabled: true
      feed_url: https://fitgirl-repacks.site/feed/
      platform: pc
      cache_pages_hours: 6
      reject_keywords: []
      max_queue_days: 60
  - dodi:
      enabled: true
      feed_url: https://1337x.to/user/DODI/
      platform: pc
      cache_pages_hours: 6
      reject_keywords: []
      max_queue_days: 60
```

- Sources remain an **ordered list** — position determines priority
- Each entry is a single-key dict where the key IS the source name
- `rss_url` renamed to `feed_url` for all sources
- `name` field disappears from user-facing YAML

---

## 2. Pydantic Model Changes

### SourceConfigEntry

Rename `rss_url` → `feed_url`. Keep `name` field but exclude it from
serialization so it doesn't appear in YAML output.

```python
class SourceConfigEntry(BaseModel):
    """A single download source entry."""

    name: str = Field(exclude=True)  # hidden from YAML, populated from dict key
    enabled: bool = True
    platform: str = "pc"
    cache_pages_hours: int = Field(default=6, gt=0, le=168)
    reject_keywords: list[str] = Field(default_factory=list)
    max_queue_days: int = Field(default=60, ge=0)
    feed_url: str | None = None
```

### DownloadSitesConfig

Add a `@field_validator("root", mode="before")` that pre-processes the
list of single-key dicts into the internal model format.

```python
class DownloadSitesConfig(RootModel[list[SourceConfigEntry]]):
    root: list[SourceConfigEntry] = [
        SourceConfigEntry(name="fitgirl", feed_url="https://fitgirl-repacks.site/feed/"),
        SourceConfigEntry(name="dodi"),
    ]

    @field_validator("root", mode="before")
    @classmethod
    def _parse_keyed_list(cls, v: Any) -> Any:
        """Convert [{'fitgirl': {...}}, {'dodi': {...}}] into populated list."""
        if not isinstance(v, list):
            return v
        result = []
        for item in v:
            if isinstance(item, dict):
                for key, val in item.items():
                    if isinstance(val, dict):
                        val["name"] = key
                        if "feed_url" not in val and "rss_url" in val:
                            val["feed_url"] = val.pop("rss_url")
                        result.append(val)
                    else:
                        result.append({"name": key})
            else:
                result.append(item)
        return result
```

The validator handles three input formats:

| Format | Example | Case |
|--------|---------|------|
| New keyed | `- fitgirl: {enabled: true, feed_url: ...}` | Standard |
| Legacy field | `- {name: fitgirl, rss_url: ...}` | Migration input |
| Shorthand | `- dodi:` | All defaults |

---

## 3. Migration

A new migration function `_migrate_download_sites_to_keyed_list` handles:

1. **Flat dict → keyed list**: Convert `{fitgirl: {rss_url: ...}}` to
   `[{fitgirl: {feed_url: ...}}]` — renames `rss_url` → `feed_url`
2. **Fielded list → keyed list**: Convert
   `[{name: fitgirl, rss_url: ...}]` to `[{fitgirl: {feed_url: ...}}]`
3. **Rename `rss_url` → `feed_url`**: For any entries still using old
   field name

Runs at the end of the migration pipeline so it sees the final config
state after all other migrations.

### Affected migration functions

The existing `_migrate_download_sites_to_ordered` which converts flat
dict → `[{name: ...}]` stays in place. The new migration runs after
it and converts `[{name: ...}]` → `[{key: {...}}]`.

The four fitgirl-specific migrations that iterate `download_sites.*`
(`_migrate_fitgirl_exclude_keywords`,
`_migrate_fitgirl_cache_ttl_hours`,
`_migrate_pending_days_to_max_queue_days`,
`_migrate_recheck_days_to_max_queue_days`) already handle the list
format safely (they guard with `isinstance(parent, dict)` and skip
list-format configs). Since the new keyed-list migration runs last,
they see the config in `[{name: ...}]` format — no changes needed.

---

## 4. Pipeline & Scheduler Changes

Minimal field renames at access points:

| File | Change |
|------|--------|
| `scheduler.py:181` | `fitgirl_entry.rss_url` → `fitgirl_entry.feed_url` |
| `pipeline.py:124` | `fitgirl_rss_url` param → `fitgirl_feed_url` |
| `pipeline.py:219` | `entry.rss_url = fitgirl_rss_url` → `entry.feed_url = fitgirl_feed_url` |
| `pipeline.py:417` | kwargs key `rss_url` → `feed_url`, value `entry.rss_url` → `entry.feed_url` |
| `fitgirl.py:237-252` | Constructor docstring + `self._rss_url` → `self._feed_url` |

The `entry.name` access pattern stays the same everywhere — no logic
changes to source matching or dispatching.

---

## 5. Testing

| Test | What it verifies |
|------|-----------------|
| `test_parse_keyed_list` | `[{fitgirl: {enabled: true}}]` parses with correct name |
| `test_parse_keyed_list_legacy` | Old `[{name: fitgirl, rss_url: ...}]` still works |
| `test_feed_url_renamed_from_rss_url` | `rss_url` → `feed_url` during migration |
| `test_name_excluded_from_dump` | Serialized YAML has no `name` field |
| Update all existing config tests | Use new keyed-list format |

---

## 6. Files Changed

| File | Change |
|------|--------|
| `src/gamarr/config.py` | `SourceConfigEntry.feed_url` + `Field(exclude=True)` on `name`; `_parse_keyed_list` validator; new migration function |
| `src/gamarr/scheduler.py` | `rss_url` → `feed_url` in `_build_kwargs` |
| `src/gamarr/pipeline.py` | `rss_url` → `feed_url` in params and kwargs |
| `src/gamarr/sources/fitgirl.py` | `rss_url` → `feed_url` in constructor + docstring |
| `tests/unit/test_config.py` | Update tests, add new migration tests |

---

## 7. Open Questions

None — the design has been reviewed and approved.
