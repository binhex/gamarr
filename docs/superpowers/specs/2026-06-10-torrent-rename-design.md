# Torrent rename on add

**Date:** 2026-06-10
**Status:** Approved design

## Problem

When gamarr adds a torrent to qBittorrent via magnet link, the torrent's
display name defaults to whatever the magnet carries — typically an
infohash/SHA string that is meaningless to a user browsing their
qBittorrent queue. The pipeline already passes the game title
(`game_title`) to `QBittorrentClient.add_torrent()` as the `title`
parameter, but the parameter is never used.

## Solution

Rename the torrent to the game title immediately after adding it, using
qBittorrent's `torrents_rename` API. The rename happens before the
reannounce call. A rename failure is non-fatal — the torrent is still
added successfully, it just keeps its original magnet-derived name.

### Change — `src/gamarr/qbittorrent.py`

Inside `QBittorrentClient.add_torrent()`, replace the existing
`torrents_info` → `torrents_reannounce` block with:

```python
try:
    infos = self._client.torrents_info(tag=tag)
    if infos:
        h = str(infos[0].hash)
        if title:
            try:
                self._client.torrents_rename(torrent_hash=h, new_torrent_name=title)
                logger.info("Renamed torrent to '{}'", title)
            except Exception as exc:
                logger.warning("Failed to rename torrent '{}': {}", title, exc)
        self._client.torrents_reannounce(torrent_hashes=h)
except Exception as exc:
    logger.warning("Post-add operations failed for '{}': {}; continuing.", title, exc)
```

### Behaviour by scenario

| Scenario | Result |
|---|---|
| Torrent added with `title="Elden Ring"` | Torrent is renamed to "Elden Ring" in qBittorrent UI |
| Torrent added with `title=""` (no game title) | Rename is skipped, torrent keeps magnet-derived name |
| Rename API call fails | Warning logged, torrent still added and reannounced |
| `torrents_info` fails (cannot get hash) | Post-add operations skipped, torrent added without rename or reannounce |

### Affects

Both paused and active torrents — the `title` parameter is passed from
the pipeline regardless of `add_paused` config. No config change needed.

## Files affected

| File | Action |
|------|--------|
| `src/gamarr/qbittorrent.py` | Modify `add_torrent()` — add rename call |
| `tests/unit/test_qbittorrent.py` | Update/add tests for rename behaviour |

## Testing

### Unit tests for `qbittorrent.py`

| Test | What it verifies |
|------|------------------|
| `test_add_torrent_renames_to_title` | `torrents_rename` called with correct hash and title |
| `test_add_torrent_skips_rename_when_no_title` | No rename call when title is empty |
| `test_add_torrent_rename_failure_continues` | Rename exception is caught, torrent still reannounced |
| `test_add_torrent_info_failure_skips_post_add` | `torrents_info` failure skips both rename and reannounce |

### Existing tests preserved

All existing `QBittorrentClient` tests continue to pass. The new rename
step is transparent to callers — same return value, same public API.

## Implementation order

1. Add rename logic to `QBittorrentClient.add_torrent()`
2. Add/update unit tests
3. Run full test suite — verify no regressions
