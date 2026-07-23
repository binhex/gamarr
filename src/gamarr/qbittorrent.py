"""qBittorrent WebUI client wrapper for gamarr."""

from __future__ import annotations

import uuid
from typing import Any

import qbittorrentapi
from loguru import logger

_TAG_PREFIX = "gamarr-"


def _extract_gamarr_tag(tags_str: str) -> str:
    """Return the first 'gamarr-' tag from a comma-separated tag string, or ''."""
    return next(
        (t.strip() for t in tags_str.split(",") if t.strip().startswith(_TAG_PREFIX)),
        "",
    )


class QBittorrentError(Exception):
    """Raised when a qBittorrent API call fails."""


class QBittorrentClient:
    """Wraps the qBittorrent WebUI API for gamarr operations."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8080,
        username: str = "admin",
        password: str = "adminadmin",
        category: str = "games-gamarr",
        add_paused: bool = False,
        verify_ssl: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._category = category
        self._add_paused = add_paused
        if username == "admin" and password == "adminadmin":
            logger.warning(
                "Using default qBittorrent credentials (admin:adminadmin) - override in config/gamarr.yml for security"
            )
        self._client = qbittorrentapi.Client(
            host=host,
            port=port,
            username=username,
            password=password,
            VERIFY_WEBUI_CERTIFICATE=verify_ssl,
        )

    @property
    def add_paused(self) -> bool:
        """Return whether torrents should be added in the paused state."""
        return self._add_paused

    def is_connected(self) -> bool:
        """Return True if the qBittorrent API is reachable and reports a connected status."""
        try:
            status = self._client.sync_maindata().server_state.connection_status
            return status in {"connected", "firewalled"}
        except Exception as exc:
            logger.warning("qBittorrent connectivity check failed: {}", exc)
            return False

    def add_torrent(self, magnet_url: str, title: str = "") -> str | bool:
        """Add a magnet link to qBittorrent and return a unique tag.

        When *title* is provided, the torrent's display name is set to
        *title* at add time via the ``rename`` parameter (so the user
        sees the game name, not a magnet SHA hash).  Whitespace-only
        titles are treated as empty (rename skipped).

        Returns a gamarr-{uuid} tag string on success, or False on failure.
        """
        if not magnet_url:
            return False

        tag = f"{_TAG_PREFIX}{uuid.uuid4()}"
        # Pass rename= to torrents_add so qBittorrent sets the display name
        # immediately. A separate torrents_rename call won't work because
        # the torrent hasn't appeared in the list yet (magnet is still
        # resolving when added via URL).
        rename_param = title if (title and title.strip()) else None

        try:
            self._client.torrents_add(
                urls=magnet_url,
                category=self._category,
                is_paused=self._add_paused,
                tags=tag,
                rename=rename_param,
            )
            logger.info("Added torrent '{}' with tag '{}'", title, tag)
        except Exception as exc:
            logger.warning("Failed to add torrent '{}': {}", title, exc)
            return False

        try:
            infos = self._client.torrents_info(tag=tag)
            if infos:
                self._client.torrents_reannounce(torrent_hashes=str(infos[0].hash))
        except Exception as exc:
            logger.warning("Reannounce failed for '{}': {}; continuing.", title, exc)

        return tag

    def list_completed(self) -> list[dict[str, Any]]:
        """Return details for all 100%-complete gamarr-tagged torrents.

        Queries by category, filters to gamarr-tagged torrents with
        ``amount_left == 0`` — no status filter (mirrors movarr).
        """
        try:
            all_torrents = self._client.torrents_info(category=self._category)
        except Exception as exc:
            logger.warning("Failed to list completed torrents: {}", exc)
            return []

        results: list[dict[str, Any]] = []
        for torrent in all_torrents:
            tag = _extract_gamarr_tag(torrent.tags)
            if not tag:
                continue
            if int(torrent.amount_left) != 0:
                continue

            try:
                files = self._client.torrents_files(torrent.hash)
                props = self._client.torrents_properties(torrent.hash)
            except Exception as exc:
                logger.warning("Failed to fetch metadata for torrent '{}': {}; skipping.", torrent.hash, exc)
                continue

            results.append({
                "torrent_tag": tag,
                "torrent_hash": torrent.hash,
                "torrent_name": torrent.name,
                "torrent_save_path": props.save_path or torrent.save_path,
                "torrent_state": torrent.state,
                "torrent_file_list": [
                    {"file_name": f.name, "file_size": f.size}
                    for f in files
                ],
            })
        return results

    def delete_torrent(self, torrent_hash: str, *, delete_data: bool = False) -> None:
        """Delete a torrent and optionally its downloaded data.

        Args:
            torrent_hash: The torrent hash to delete.
            delete_data: If True, also delete the downloaded files.
        """
        try:
            self._client.torrents_delete(delete_files=delete_data, torrent_hashes=torrent_hash)
            logger.info("Deleted torrent '{}' (delete_data={}).", torrent_hash, delete_data)
        except Exception as exc:
            logger.warning("Failed to delete torrent '{}': {}", torrent_hash, exc)
