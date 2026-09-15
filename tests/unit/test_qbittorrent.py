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

    def test_add_torrent_reuses_existing_gamarr_tag(self) -> None:
        """A duplicate add (qBittorrent 409) for an existing tagged torrent is a success."""
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
            mock_client.torrents_info.return_value = [MagicMock(hash="abc", tags="gamarr-existing")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert result == "gamarr-existing", "an already-present torrent must not be a delivery failure"
        mock_client.torrents_add_tags.assert_not_called()

    def test_add_torrent_tags_existing_untagged_torrent(self) -> None:
        """An already-present gamarr-category torrent without a tag is adopted."""
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
            mock_client.torrents_info.return_value = [MagicMock(hash="abc", tags="", category="games-gamarr")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert isinstance(result, str)
        assert result.startswith("gamarr-")
        mock_client.torrents_add_tags.assert_called_once()
        kwargs = mock_client.torrents_add_tags.call_args.kwargs
        assert kwargs["torrent_hashes"] == "abc123def4567890abc123def4567890abc12345"
        assert kwargs["tags"] == result

    def test_add_torrent_categorises_adopted_torrent(self) -> None:
        """An adopted gamarr torrent is put in gamarr's category for post-processing."""
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
            mock_client.torrents_info.return_value = [MagicMock(hash="abc", tags="gamarr-existing", category="")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert result == "gamarr-existing"
        mock_client.torrents_set_category.assert_called_once_with(
            category="games-gamarr", torrent_hashes="abc123def4567890abc123def4567890abc12345"
        )

    def test_add_torrent_skips_category_when_none_configured(self) -> None:
        """Adopting with no configured category must not clear the user's category."""
        import qbittorrentapi

        client = QBittorrentClient(category="")
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
            mock_client.torrents_info.return_value = [MagicMock(hash="abc", tags="gamarr-existing", category="mine")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert result == "gamarr-existing"
        mock_client.torrents_set_category.assert_not_called()

    def test_add_torrent_leaves_foreign_torrent_untouched(self) -> None:
        """A torrent the user added themselves is reported delivered but never modified."""
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
            mock_client.torrents_info.return_value = [MagicMock(hash="abc", tags="", category="movies")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert isinstance(result, str) and result.startswith("gamarr-")
        mock_client.torrents_add_tags.assert_not_called()
        mock_client.torrents_set_category.assert_not_called()

    def test_add_torrent_ignores_empty_configured_category(self) -> None:
        """With no configured category, an uncategorised torrent is not gamarr's."""
        import qbittorrentapi

        client = QBittorrentClient(category="")
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
            mock_client.torrents_info.return_value = [MagicMock(hash="abc", tags="", category="")]

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert isinstance(result, str) and result.startswith("gamarr-")
        mock_client.torrents_add_tags.assert_not_called()
        mock_client.torrents_set_category.assert_not_called()

    def test_add_torrent_failure_logs_infohash(self) -> None:
        """A rejected add that is genuinely absent is a failure, and logs the hash."""
        import io

        import qbittorrentapi
        from loguru import logger

        client = QBittorrentClient()
        stream = io.StringIO()
        handler = logger.add(stream, format="{message}", level="WARNING")
        try:
            with patch.object(client, "_client") as mock_client:
                mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
                mock_client.torrents_info.return_value = []

                result = client.add_torrent(
                    magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                    title="Test Game",
                )
        finally:
            logger.remove(handler)

        assert result is False
        log_output = stream.getvalue()
        assert "abc123def4567890abc123def4567890abc12345" in log_output, (
            f"Expected the infohash in the failure log, got: {log_output}"
        )

    def test_add_torrent_adopts_existing_torrent_from_base32_magnet(self) -> None:
        """A base32 btih is decoded so an already-present torrent can be adopted."""
        import base64

        import qbittorrentapi

        infohash = "aa" * 20
        magnet = f"magnet:?xt=urn:btih:{base64.b32encode(bytes.fromhex(infohash)).decode()}"
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
            mock_client.torrents_info.return_value = [MagicMock(hash=infohash, tags="gamarr-b32")]

            result = client.add_torrent(magnet_url=magnet, title="Test Game")

        assert result == "gamarr-b32"
        # The decoded infohash must be the hex form, not the raw base32 token.
        assert mock_client.torrents_info.call_args.kwargs["torrent_hashes"] == infohash

    def test_add_torrent_failure_with_unparsable_hash_still_warns(self) -> None:
        """Magnets whose btih cannot be decoded fall through to a plain failure."""
        import qbittorrentapi

        client = QBittorrentClient()
        for magnet in (
            "magnet:?dn=Game",  # no btih at all
            "magnet:?xt=urn:btih:xyz",  # wrong length
            "magnet:?xt=urn:btih:" + "1" * 32,  # invalid base32 alphabet
        ):
            with patch.object(client, "_client") as mock_client:
                mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
                mock_client.torrents_info.return_value = [MagicMock(hash="abc")]

                result = client.add_torrent(magnet_url=magnet, title="Test Game")

            assert result is False, magnet
            mock_client.torrents_info.assert_not_called()

    def test_add_torrent_lookup_failure_is_a_plain_failure(self) -> None:
        """When the torrent cannot be looked up, the add failure is not adopted."""
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
            mock_client.torrents_info.side_effect = RuntimeError("lookup failed")

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert result is False
        mock_client.torrents_add_tags.assert_not_called()
        mock_client.torrents_set_category.assert_not_called()

    def test_add_torrent_returns_false_when_tagging_fails(self) -> None:
        """A failed tagging call must retry, not claim an unapplied tag."""
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
            mock_client.torrents_info.return_value = [MagicMock(hash="abc", tags="", category="games-gamarr")]
            mock_client.torrents_add_tags.side_effect = RuntimeError("tag failed")

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert result is False, "an untagged adopted torrent stays invisible to post-processing"

    def test_add_torrent_returns_false_when_categorising_fails(self) -> None:
        """A failed category assignment must retry, not claim delivery."""
        import qbittorrentapi

        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.side_effect = qbittorrentapi.Conflict409Error("Conflict")
            mock_client.torrents_info.return_value = [MagicMock(hash="abc", tags="gamarr-existing", category="")]
            mock_client.torrents_set_category.side_effect = RuntimeError("category failed")

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert result is False, "an uncategorised adopted torrent stays invisible to post-processing"

    def test_add_torrent_skips_reannounce_when_torrent_not_listed(self) -> None:
        """When the added torrent is not listed yet, no reannounce is attempted."""
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Ok."
            mock_client.torrents_info.return_value = []

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert isinstance(result, str) and result.startswith("gamarr-")
        mock_client.torrents_reannounce.assert_not_called()

    def test_add_torrent_fails_body_is_not_a_success(self) -> None:
        """A 200 'Fails.' body means nothing was added, so it must not report a tag."""
        client = QBittorrentClient()
        with patch.object(client, "_client") as mock_client:
            mock_client.torrents_add.return_value = "Fails."
            mock_client.torrents_info.return_value = []

            result = client.add_torrent(
                magnet_url="magnet:?xt=urn:btih:ABC123DEF4567890ABC123DEF4567890ABC12345",
                title="Test Game",
            )

        assert result is False, "an add that qBittorrent rejected must retry, not report a tag"

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


class TestClientTimeoutBounds:
    """qBittorrent WebUI HTTP calls must be bounded so a hung client
    cannot block the acquisition thread forever."""

    def test_client_constructed_with_bounded_timeouts(self) -> None:
        from unittest.mock import patch

        from gamarr.qbittorrent import QBittorrentClient

        with patch("gamarr.qbittorrent.qbittorrentapi.Client") as mock_client:
            QBittorrentClient(host="localhost", port=8080, username="admin", password="secret")

        mock_client.assert_called_once()
        _, call_kwargs = mock_client.call_args
        assert call_kwargs.get("REQUESTS_ARGS") == {"timeout": (5, 30)}, call_kwargs
