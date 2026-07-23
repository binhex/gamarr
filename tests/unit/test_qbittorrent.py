"""Tests for gamarr qBittorrent client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gamarr.qbittorrent import QBittorrentClient


class TestQBittorrentClient:
    """QBittorrentClient construction."""

    def test_constructs_with_defaults(self) -> None:
        client = QBittorrentClient()
        assert client._host == "localhost"
        assert client._port == 8080

    def test_constructs_with_custom_values(self) -> None:
        client = QBittorrentClient(
            host="10.0.0.1",
            port=9090,
            username="user",
            password="pass",
            category="games-gamarr",
        )
        assert client._host == "10.0.0.1"
        assert client._category == "games-gamarr"


class TestQBittorrentAddTorrent:
    """Adding torrents to qBittorrent."""

    def test_add_no_url_returns_false(self) -> None:
        client = QBittorrentClient()
        result = client.add_torrent(magnet_url="", title="Test Game")
        assert result is False

    def test_add_torrent_returns_tag_on_success(self) -> None:
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Ok."
            mock_client.torrents_info.return_value = [MagicMock(hash="abc123")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:abc",
                title="Test Game",
            )
            assert result is not False
            assert isinstance(result, str)
            assert result.startswith("gamarr-")
            mock_client.torrents_add.assert_called_once()

    def test_add_torrent_api_error_returns_false(self) -> None:
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.APIError("mock error")

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:xyz",
                title="Broken Game",
            )
            assert result is False


class TestQBittorrentConnectivity:
    """Connection health checks."""

    def test_is_connected_returns_true_when_connected(self) -> None:
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.sync_maindata.return_value.server_state.connection_status = "connected"
            assert client.is_connected() is True

    def test_is_connected_returns_false_when_disconnected(self) -> None:
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.sync_maindata.return_value.server_state.connection_status = "disconnected"
            assert client.is_connected() is False

    def test_is_connected_handles_api_error(self) -> None:
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.sync_maindata.side_effect = qbittorrentapi.APIError("mock error")
            assert client.is_connected() is False


class TestQBittorrentRename:
    """Torrent rename on add."""

    def test_add_torrent_renames_via_add_param(self) -> None:
        """torrents_add is called with rename=<title> when title is provided."""
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Ok."
            mock_client.torrents_info.return_value = [MagicMock(hash="abc123")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:abc",
                title="Elden Ring",
            )
            assert result is not False
            assert isinstance(result, str)
            assert result.startswith("gamarr-")
            # Should pass rename= to torrents_add (not use separate rename call)
            mock_client.torrents_add.assert_called_once_with(
                urls="magnet:?xt=urn:btih:abc",
                category="games-gamarr",
                is_paused=False,
                tags=result,
                rename="Elden Ring",
            )
            # No separate torrents_rename call needed
            mock_client.torrents_rename.assert_not_called()

    def test_add_torrent_skips_rename_when_no_title(self) -> None:
        """No rename param when title is empty."""
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Ok."
            mock_client.torrents_info.return_value = [MagicMock(hash="abc123")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:abc",
                title="",
            )
            assert result is not False
            # Should NOT include rename param when title is empty
            args, kwargs = mock_client.torrents_add.call_args
            assert "rename" not in kwargs or kwargs["rename"] is None
            mock_client.torrents_reannounce.assert_called_once()

    def test_add_torrent_skips_rename_for_whitespace_title(self) -> None:
        """Whitespace-only title is treated as empty — no rename."""
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Ok."
            mock_client.torrents_info.return_value = [MagicMock(hash="abc123")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:abc",
                title="   ",
            )
            assert result is not False
            # Should NOT include rename param for whitespace title
            args, kwargs = mock_client.torrents_add.call_args
            assert "rename" not in kwargs or kwargs["rename"] is None
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


class TestListCompleted:
    """Tests for list_completed method."""

    def test_returns_empty_when_api_fails(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        client._client.torrents_info.side_effect = Exception("API down")
        completed, total_count = client.list_completed()
        assert completed == []

    def test_skips_non_gamarr_tags(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        fake_torrent = MagicMock()
        fake_torrent.tags = "other-tag, no-gamarr"
        fake_torrent.amount_left = 0
        client._client.torrents_info.return_value = [fake_torrent]
        completed, total_count = client.list_completed()
        assert completed == []

    def test_skips_incomplete_torrents(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        fake_torrent = MagicMock()
        fake_torrent.tags = "gamarr-abc123"
        fake_torrent.amount_left = 1024  # not done
        client._client.torrents_info.return_value = [fake_torrent]
        completed, total_count = client.list_completed()
        assert completed == []

    def test_returns_completed_gamarr_torrents(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        fake = MagicMock()
        fake.tags = "gamarr-xyz789"
        fake.amount_left = 0
        fake.hash = "deadbeef"
        fake.name = "Game Title [Repack]"
        fake.state = "uploading"
        fake.save_path = "/downloads/Game"
        client._client.torrents_info.return_value = [fake]

        fake_file = MagicMock()
        fake_file.name = "setup.exe"
        fake_file.size = 123456
        client._client.torrents_files.return_value = [fake_file]

        fake_props = MagicMock()
        fake_props.save_path = "/downloads/Game"
        client._client.torrents_properties.return_value = fake_props

        completed, total_count = client.list_completed()
        assert len(completed) == 1
        entry = completed[0]
        assert entry["torrent_tag"] == "gamarr-xyz789"
        assert entry["torrent_hash"] == "deadbeef"
        assert entry["torrent_name"] == "Game Title [Repack]"
        assert entry["torrent_state"] == "uploading"
        assert entry["torrent_save_path"] == "/downloads/Game"
        assert len(entry["torrent_file_list"]) == 1
        assert entry["torrent_file_list"][0]["file_name"] == "setup.exe"
        assert entry["torrent_file_list"][0]["file_size"] == 123456


class TestDeleteTorrent:
    """Tests for delete_torrent method."""

    def test_delete_torrent_with_data(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        client.delete_torrent("abc123", delete_data=True)
        client._client.torrents_delete.assert_called_once_with(delete_files=True, torrent_hashes="abc123")

    def test_delete_torrent_without_data(self) -> None:
        client = QBittorrentClient()
        client._client = MagicMock()
        client.delete_torrent("abc123", delete_data=False)
        client._client.torrents_delete.assert_called_once_with(delete_files=False, torrent_hashes="abc123")
