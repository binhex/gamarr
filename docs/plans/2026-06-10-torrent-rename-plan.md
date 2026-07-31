# Torrent Rename on Add — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename torrents in qBittorrent to the game title immediately after adding them via magnet link, so the user sees meaningful names in their download queue.

**Architecture:** A single change to `QBittorrentClient.add_torrent()` in `src/gamarr/qbittorrent.py`. The method already receives the `title` parameter from the pipeline and already fetches the torrent hash via `torrents_info`. The rename call `torrents_rename(hash, title)` is inserted between `torrents_info` and `torrents_reannounce`.

**Tech Stack:** Python, `qbittorrentapi` library (already a dependency).

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/gamarr/qbittorrent.py` | **Modify** | Add `torrents_rename` call in `add_torrent()` |
| `tests/unit/test_qbittorrent.py` | **Modify** | Add tests for rename behaviour |


### Task 1: Add torrent rename to `QBittorrentClient.add_torrent()`

**Files:**
- Modify: `src/gamarr/qbittorrent.py:48-62` (the `add_torrent` method body)
- Test: `tests/unit/test_qbittorrent.py` (modified in Task 2)

- [ ] **Step 1: Write the failing test for rename**

Add failing tests to `tests/unit/test_qbittorrent.py`. Add them after the existing `TestQBittorrentAddTorrent` class:

```python
class TestQBittorrentRename:
    """Torrent rename on add."""

    def test_add_torrent_renames_to_title(self) -> None:
        """torrents_rename is called with the correct hash and title."""
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Ok."
            mock_client.torrents_info.return_value = [MagicMock(hash="abc123")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:abc",
                title="Elden Ring",
            )
            assert result is not False
            # Should rename to the game title
            mock_client.torrents_rename.assert_called_once_with(
                torrent_hash="abc123",
                new_torrent_name="Elden Ring",
            )
            # Should still reannounce
            mock_client.torrents_reannounce.assert_called_once()

    def test_add_torrent_skips_rename_when_no_title(self) -> None:
        """No rename call when title is empty."""
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Ok."
            mock_client.torrents_info.return_value = [MagicMock(hash="abc123")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:abc",
                title="",
            )
            assert result is not False
            mock_client.torrents_rename.assert_not_called()
            mock_client.torrents_reannounce.assert_called_once()

    def test_add_torrent_rename_failure_continues(self) -> None:
        """Rename failure is caught, torrent is still reannounced."""
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Ok."
            mock_client.torrents_info.return_value = [MagicMock(hash="abc123")]
            mock_client.torrents_rename.side_effect = qbittorrentapi.APIError("rename failed")

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:abc",
                title="Elden Ring",
            )
            assert result is not False
            mock_client.torrents_rename.assert_called_once()
            # Should still reannounce despite rename failure
            mock_client.torrents_reannounce.assert_called_once()

    def test_add_torrent_info_failure_skips_post_add(self) -> None:
        """torrents_info failure skips both rename and reannounce."""
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Ok."
            mock_client.torrents_info.side_effect = qbittorrentapi.APIError("info failed")

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:abc",
                title="Elden Ring",
            )
            assert result is not False
            mock_client.torrents_rename.assert_not_called()
            mock_client.torrents_reannounce.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_qbittorrent.py::TestQBittorrentRename -v`
Expected: FAIL — `torrents_rename` is not called (the method doesn't implement it yet)

- [ ] **Step 3: Modify `add_torrent()` in `qbittorrent.py`**

Replace the existing `torrents_info` → `torrents_reannounce` block (lines ~54-62) with:

```python
        try:
            infos = self._client.torrents_info(tag=tag)
            if infos:
                h = str(infos[0].hash)
                if title:
                    try:
                        self._client.torrents_rename(
                            torrent_hash=h,
                            new_torrent_name=title,
                        )
                        logger.info("Renamed torrent to '{}'", title)
                    except Exception as exc:
                        logger.warning("Failed to rename torrent '{}': {}", title, exc)
                self._client.torrents_reannounce(torrent_hashes=h)
        except Exception as exc:
            logger.warning("Post-add operations failed for '{}': {}; continuing.", title, exc)
```

Read the current file first to find the exact lines to replace. The current code around lines 48-62 should look like:

```python
        try:
            infos = self._client.torrents_info(tag=tag)
            if infos:
                self._client.torrents_reannounce(torrent_hashes=str(infos[0].hash))
        except Exception as exc:
            logger.warning("Reannounce failed for '{}': {}; continuing.", title, exc)
```

Replace that entire block with the new code above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/gamarr && uv run pytest tests/unit/test_qbittorrent.py -v`
Expected: All tests pass (including the 4 new rename tests + existing tests)

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `cd /data/gamarr && uv run pytest tests/unit/ -x -q --no-header`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
cd /data/gamarr && git add src/gamarr/qbittorrent.py tests/unit/test_qbittorrent.py && git commit -m "feat(qbt): rename torrent to game title after adding via magnet"
```
