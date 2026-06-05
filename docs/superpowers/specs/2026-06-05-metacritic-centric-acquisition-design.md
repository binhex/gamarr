# Metacritic-Centric Acquisition Design

**Date:** 2026-06-05
**Status:** Approved design

## Summary

Gamarr currently reads FitGirl RSS first, then looks up each title on Metacritic for scores. This design flips the architecture: Metacritic becomes the primary source for game discovery, and FitGirl becomes an availability provider checked for matching repacks.

## Motivation

- Games with TBD scores or low review counts should be re-checked as more reviews come in
- A Metacritic-first approach discovers high-scoring games regardless of when FitGirl repacks them
- The architecture generalises to support additional torrent providers in the future

## Architecture

```
Metacritic Browse → Pending Queue → Source Matching → Download
                      ↑                    ↑
                 (pending_days)    FitGirl Sitemap Index
                                      (rebuilt each cycle)
```

The old FitGirl→Metacritic path is kept as a fast path for new RSS entries that match pending games.

## Database Schema

### New table: `pending_games`

```python
class PendingGame(Base):
    __tablename__ = "pending_games"

    slug = Column(String, primary_key=True)
    game_title = Column(String, nullable=False)
    platform = Column(String, nullable=False)
    metascore = Column(Float, nullable=True)
    metascore_reviews = Column(Integer, nullable=True)
    user_score = Column(Float, nullable=True)
    user_reviews = Column(Integer, nullable=True)
    genres = Column(String, nullable=True)           # JSON list
    release_date = Column(String, nullable=True)
    discovered_at = Column(String, nullable=False)    # ISO timestamp
    expires_at = Column(String, nullable=False)        # ISO timestamp (now + pending_days)
    last_checked_at = Column(String, nullable=True)    # ISO timestamp
```

### New table: `source_titles`

Rebuilt each cycle from the FitGirl sitemap. Provides fast title matching without per-game HTTP requests.

```python
class SourceTitle(Base):
    __tablename__ = "source_titles"

    source = Column(String, nullable=False, primary_key=True)  # "fitgirl"
    title = Column(String, nullable=False)                      # Full repack title
    url = Column(String, nullable=False, primary_key=True)      # Download page URL
```

## Pipeline Flow

### Phase 1: Build Source Index
1. Fetch `https://fitgirl-repacks.site/sitemap.xml`
2. Parse XML → extract all repack page URLs
3. Rebuild `source_titles` table for `"fitgirl"` source

### Phase 2: Browse Metacritic
1. Scan Metacritic browse pages (platform-specific, sorted by new)
2. For each listed game:
   - Skip if already processed or pending (by slug)
   - Skip if `release_date` < now - `days_since_release`
   - Evaluate scores against platform thresholds
   - If scores pass → insert into `pending_games` with `expires_at = now + pending_days`
   - Self-terminate: stop when all listed games are already pending/processed or too old

### Phase 3: Match Pending Games
For each non-expired pending game:
- Normalize title via `_normalise_for_compare`
- Scan `source_titles` for matching normalized title
- If match found → fetch download page → extract magnet → add to qBittorrent → move to history as "Passed"
- If no match → update `last_checked_at`
- If `now > expires_at` → move to history as "Expired", detail "Not available on any source within window"

### Phase 4: Legacy RSS Fast Path
- Fetch FitGirl RSS entries
- If entry matches a pending game → handle as download immediately
- If entry is unprocessed and unmatched → do old-style Metacritic lookup + score check → record result

### Phase 5: Report
- Log summary: X new pending, Y matched+downloaded, Z expired

## Config Changes

New fields in `MetacriticPlatformConfig`:

```yaml
metacritic:
  platform_overrides:
    pc:
      pending_days: 30       # How long to keep trying sources for a qualifying game
      browse_enabled: true   # Enable/disable Metacritic browse discovery
```

`pending_days` defaults to 30. `browse_enabled` defaults to true (disable for legacy-only mode).

## Title Matching

Uses the existing `_normalise_for_compare` function on both the Metacritic game title and the FitGirl repack title (after `_clean_title` processing). Exact normalized string comparison.

## Error Handling

| Scenario | Handling |
|----------|----------|
| FitGirl sitemap fetch fails | Log warning, skip indexing. `last_checked_at` NOT updated for pending games — they won't age out during outage. |
| Metacritic browse page fails | Log warning, skip that page. Existing pending games still matched. |
| Metacritic entirely unreachable | Browse phase skipped entirely. Pending matching + legacy RSS still work. |
| Magnet link dead | Record as "Error" in history, remove from pending, send notification. |
| `source_titles` empty (first run) | Pending games persist until expiry, matched on next cycle. |

## Testing

- Browse page parsing: existing `_parse_browse_page` tests
- Pending lifecycle: insert → match → download → expire
- Sitemap parsing: synthetic XML fixture
- Title matching: extend `_normalise_for_compare` test suite
- Pipeline integration: mocked Metacritic browse + fake sitemap + in-memory DB

## Migration

- New tables created alongside existing `history` table — no schema migration
- Old `result="Failed"` rows NOT converted to pending — system starts fresh
- Legacy RSS→Metacritic path fully intact when `browse_enabled: false`

## Future Work (Out of Scope)

- Additional torrent providers (Dodi, Ankergames, etc.)
- Per-source pending_days or priority ordering
- Notifications for expired pending games
- Manual "re-check" action for expired games
