# Design: sort_order `criticscore`/`userscore` and critic-threshold rename

Date: 2026-08-31
Status: Approved

## Goal

1. Add a third `sort_order` value, `userscore`, which browses Metacritic
   in user-score order.
2. Rename `sort_order: metascore` to `sort_order: criticscore` so the
   values read clearly as `new`, `criticscore`, `userscore`.
3. For naming consistency, also rename the critic-threshold keys:
   `min_metascore` → `min_criticscore`,
   `min_metascore_reviews` → `min_criticscore_reviews`.

## Decisions (user-approved)

- **Migration strategy: auto-migrate the YAML file in place.** On first
  load after upgrade, gamarr rewrites the user's `gamarr.yml` with the
  new key names (matching the existing config-version migration
  pattern). No manual user action required.
- **`userscore` is sort-only.** The browse phase only changes candidate
  discovery ORDER; all score thresholding still happens at detail-page
  verification. The two-phase score model is unchanged.
- The rename is total: the legacy `metascore` spellings are not valid
  config values after migration.

## Current mechanics (verified)

- `MetacriticPlatformConfig.sort_order: Literal["new", "metascore"] = "new"`
  (config.py), mirrored in `pipeline.py` (`run_acquisition`,
  `AcquisitionConfig`).
- The sort value is interpolated directly into the Metacritic browse URL:
  `https://www.metacritic.com/browse/game/{platform}/all/{year_str}/{sort_order}/`.
  Live check: the slugs `new`, `metascore`, and `userscore` all return
  HTTP 200 with the expected Nuxt browse-page structure.
- `new` mode scans per-year with the `max_pages` budget; `metascore`
  uses the else-branch (no year dimension, `year=0` sentinel, single
  budget stream).
- A detected sort-order change clears the metacritic cache and resets
  browse progress (`db.get_last_sort_order` vs config, pipeline.py).

## Design

### 1. Config surface (`src/gamarr/config.py`)

- `sort_order: Literal["new", "criticscore", "userscore"] = "new"`.
- Rename fields: `min_criticscore: int = 75`,
  `min_criticscore_reviews: int = 10`.
- `load_config()` gains a legacy-key mapping pass that runs before
  pydantic validation:
  - `metascore` → `criticscore` (under
    `review_sites.metacritic.platform_overrides.<platform>`),
  - `min_metascore` → `min_criticscore`,
  - `min_metascore_reviews` → `min_criticscore_reviews`.
- If any legacy key was present, rewrite `gamarr.yml` in place with the
  new names. The rewrite is naturally one-time: after it, the legacy
  keys no longer exist, so later loads find nothing to migrate (the
  project's migrations are key-presence-driven, not version-marker
  driven). Log an INFO line naming the migrated keys (same tone as the
  existing migration logs). After rewriting, reload the migrated dict so
  the rest of startup sees only new spellings.

### 2. Pipeline and metacritic (`src/gamarr/pipeline.py`, `src/gamarr/metacritic.py`)

- Widen the `sort_order` Literals in `run_acquisition` and
  `AcquisitionConfig` to `["new", "criticscore", "userscore"]`.
- `MetacriticClient` needs no change: the value is passed straight into
  the URL slug, and `userscore` is a valid slug.
- `userscore` takes the existing else-branch (no per-year dimension,
  `year=0`, single `max_pages` budget stream) — identical flow to
  `criticscore`. Only `new` keeps per-year scanning.
- Sort-change detection stays string-based. Upgrading a `metascore`
  install to `criticscore` therefore triggers a ONE-TIME cache clear and
  browse-progress reset (the progress dimension key changes). This is
  accepted: it is a correctness-preserving reset, not data loss.

### 3. Behavior

- `sort_order` only changes discovery order. `min_criticscore`,
  `min_criticscore_reviews`, `min_user_score`, `min_user_reviews`
  filtering remains at detail-page verification. The README's
  two-phase-score caveats remain accurate.

### 4. Docs and tests

- README config table: update the `sort_order` row to document
  `"new"`, `"criticscore"`, `"userscore"`; rename the
  `min_metascore`/`min_metascore_reviews` rows to the critic names.
- Tests:
  - Config parsing accepts all three sort values and rejects unknown
    ones with a clear validation error.
  - Legacy keys (`metascore`, `min_metascore`,
    `min_metascore_reviews`) load successfully and trigger a one-time
    YAML rewrite (assert the file content afterwards, using a temp
    config file).
  - Second load after migration contains no legacy keys and does not
    rewrite again.
  - Pipeline passes `userscore` through to the else-branch flow
    (`year=0`), mirroring the existing `metascore`-mode tests.
  - Sort-change progress reset still fires on rename (string change).
  - README-accuracy tests updated for the new key names.

### 5. Error handling

- Unknown sort values fail pydantic validation with the Literal message
  listing the three options.
- The legacy mapping normalizes before validation, so no other code
  path can observe the old spellings.
- If the YAML rewrite fails (e.g. read-only filesystem), log a WARNING
  and continue with the migrated in-memory config; the rewrite retries
  next startup.

## Out of scope

- CLI flag for sort order (config-only today; unchanged).
- Early user-score filtering during browse (explicitly rejected).
- Renaming `min_user_score`/`min_user_reviews` (already
  user-appropriate names).
