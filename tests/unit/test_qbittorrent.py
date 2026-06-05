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
