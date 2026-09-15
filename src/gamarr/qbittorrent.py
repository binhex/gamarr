"""qBittorrent WebUI client wrapper for gamarr."""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from typing import Any

import qbittorrentapi
from loguru import logger

_TAG_PREFIX = "gamarr-"
_INFOHASH_PATTERN = re.compile(r"xt=urn:btih:([0-9A-Za-z]+)", re.IGNORECASE)


def _magnet_infohash(magnet_url: str) -> str | None:
    """Return the BitTorrent v1 infohash carried by *magnet_url*, as hex.

    Accepts both the 40-character hex and 32-character base32 forms, so the
    caller can look the torrent up in qBittorrent.  Returns ``None`` when the
    magnet carries no usable infohash.
    """
    match = _INFOHASH_PATTERN.search(magnet_url)
    if match is None:
        return None
    raw = match.group(1)
    if len(raw) == 40 and all(char in "0123456789abcdefABCDEF" for char in raw):
        return raw.lower()
    if len(raw) == 32:
        try:
            return base64.b32decode(raw.upper()).hex()
        except (binascii.Error, ValueError):
            return None
    return None


def _extract_gamarr_tag(tags_str: str) -> str:
    """Return the first 'gamarr-' tag from a comma-separated tag string, or ''."""
    return next(
        (t.strip() for t in tags_str.split(",") if t.strip().startswith(_TAG_PREFIX)),
        "",
    )


def _add_succeeded(result: Any) -> bool:
    """Return True when qBittorrent reports that the torrent was added.

    Older Web API versions answer 200 with the body ``Ok.``/``Fails.``; 2.14+
    returns counts and added ids; a failed add raises 409 instead, which the
    caller routes through :meth:`_handle_add_failure`.  Unknown shapes (such as
    test doubles) are treated as success so only explicit failure is rejected.
    """
    if result is None:
        return True
    text = str(result).strip()
    if text.startswith("Fails"):
        return False
    if text.startswith("Ok"):
        return True
    added_ids = getattr(result, "added_torrent_ids", None)
    if isinstance(added_ids, (list, tuple)):
        return len(added_ids) > 0
    return True


def _is_gamarr_owned(torrent: Any, category: str) -> bool:
    """Return True when *torrent* should be adopted as gamarr's own.

    A torrent counts as gamarr's when it carries a ``gamarr-`` tag or sits in
    gamarr's qBittorrent category, even if the user added it: being in that
    category is the opt-in.  Only torrents outside the configured category are
    left completely untouched, so an empty configured *category* matches nothing
    (without this guard every uncategorised torrent would look like gamarr's).
    """
    if _extract_gamarr_tag(str(getattr(torrent, "tags", "") or "")):
        return True
    return bool(category) and str(getattr(torrent, "category", "") or "") == category


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
            # Bound every HTTP call to the WebUI (connect 5s, read 30s) so a
            # hung qBittorrent can never block the acquisition thread forever.
            REQUESTS_ARGS={"timeout": (5, 30)},
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
        """Add a magnet link to qBittorrent and return a gamarr tag.

        When *title* is provided, the torrent's display name is set to
        *title* at add time via the ``rename`` parameter (so the user
        sees the game name, not a magnet SHA hash).  Whitespace-only
        titles are treated as empty (rename skipped).

        A torrent qBittorrent already holds is not an error: the add is
        rejected with a 409 ("Conflict") when nothing is added, so the
        existing torrent is adopted and delivery is reported as successful.  A
        torrent outside gamarr's category is likewise reported delivered but is
        left completely untouched.

        Returns:
            A ``gamarr-*`` tag on success — a fresh ``gamarr-{uuid}`` tag for a
            new add, the adopted torrent's existing tag when it was already
            present, or a fresh tag that is deliberately not applied when the
            torrent exists outside gamarr's category — or False when the add
            failed and no such torrent exists.
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
            add_result = self._client.torrents_add(
                urls=magnet_url,
                category=self._category,
                is_paused=self._add_paused,
                tags=tag,
                rename=rename_param,
            )
            if not _add_succeeded(add_result):
                # Older Web API versions answer 200 with "Fails." instead of 409.
                return self._handle_add_failure(
                    magnet_url,
                    title,
                    tag,
                    RuntimeError(f"qBittorrent did not add the torrent ({add_result!r})"),
                )
            logger.info("Added torrent '{}' with tag '{}'", title, tag)
        except Exception as exc:
            return self._handle_add_failure(magnet_url, title, tag, exc)

        self._reannounce_tag(tag, title)
        return tag

    def _handle_add_failure(self, magnet_url: str, title: str, tag: str, exc: Exception) -> str | bool:
        """Interpret a failed add: adopt an already-present torrent, else fail.

        qBittorrent answers 409 ("Conflict") when it adds nothing, which is what
        a duplicate add looks like.  When the magnet's torrent is already in the
        session the delivery itself succeeded, so it is adopted (returning a
        tag) instead of retried forever.
        """
        infohash = _magnet_infohash(magnet_url)
        if infohash is not None:
            torrent = self._find_torrent(infohash)
            if torrent is not None:
                if _is_gamarr_owned(torrent, self._category):
                    logger.info(
                        "Torrent '{}' is already present in qBittorrent ({}) \u2014 adopting it for post-processing",
                        title,
                        infohash,
                    )
                    return self._adopt_existing_torrent(infohash, tag, torrent)
                logger.info(
                    "Torrent '{}' is already present in qBittorrent ({}) but was not added by gamarr "
                    "\u2014 treating as delivered without touching it",
                    title,
                    infohash,
                )
                return tag
        logger.warning(
            "Failed to add torrent '{}' (infohash {}): {}",
            title,
            infohash or "unknown",
            exc,
        )
        return False

    def _reannounce_tag(self, tag: str, title: str) -> None:
        """Reannounce the freshly added torrent so its trackers pick it up."""
        try:
            infos = self._client.torrents_info(tag=tag)
            if infos:
                self._client.torrents_reannounce(torrent_hashes=str(infos[0].hash))
        except Exception as exc:
            logger.warning("Reannounce failed for '{}': {}; continuing.", title, exc)

    def _find_torrent(self, infohash: str) -> Any | None:
        """Return the torrent qBittorrent holds for *infohash*, or None."""
        try:
            infos = list(self._client.torrents_info(torrent_hashes=infohash))
        except Exception:
            return None
        return infos[0] if infos else None

    def _adopt_existing_torrent(self, infohash: str, fallback_tag: str, torrent: Any) -> str | bool:
        """Tag an already-present torrent so post-processing can see it.

        Applies *fallback_tag* when the torrent carries no ``gamarr-`` tag and,
        when a category is configured, assigns gamarr's category so the torrent is
        visible to :meth:`list_completed`.  Returns the torrent's existing gamarr
        tag when it has one, otherwise *fallback_tag*.  Returns False when the
        tagging or categorising call fails: without either, the torrent stays
        invisible to post-processing, so reporting success would record the game
        as passed while it is never copied and never retried.
        """
        existing = _extract_gamarr_tag(str(getattr(torrent, "tags", "") or ""))
        if not existing:
            try:
                self._client.torrents_add_tags(tags=fallback_tag, torrent_hashes=infohash)
            except Exception as exc:
                logger.warning(
                    "Could not tag already-present torrent '{}': {} \u2014 retrying next cycle", infohash, exc
                )
                return False
            existing = fallback_tag
        if self._category:
            # An empty category would clear whatever the user had set.
            try:
                self._client.torrents_set_category(category=self._category, torrent_hashes=infohash)
            except Exception as exc:
                logger.warning(
                    "Could not set category on already-present torrent '{}': {} \u2014 retrying next cycle",
                    infohash,
                    exc,
                )
                return False
        return existing

    def list_completed(self) -> tuple[list[dict[str, Any]], int]:
        """Return (completed_list, total_gamarr_count) for gamarr-tagged torrents.

        Queries by category, filters to gamarr-tagged torrents with
        ``amount_left == 0`` — no status filter (mirrors movarr).

        Returns:
            A tuple of (completed_torrents, total_gamarr_torrents) where
            ``completed_torrents`` contains 100%%-complete torrents and
            ``total_gamarr_torrents`` is the count of ALL gamarr-tagged
            torrents (including in-progress downloads).
        """
        try:
            all_torrents = self._client.torrents_info(category=self._category)
        except Exception as exc:
            logger.warning("Failed to list completed torrents: {}", exc)
            return [], 0

        results: list[dict[str, Any]] = []
        gamarr_count = 0
        for torrent in all_torrents:
            tag = _extract_gamarr_tag(torrent.tags)
            if not tag:
                continue
            gamarr_count += 1
            if int(torrent.amount_left) != 0:
                continue

            try:
                files = self._client.torrents_files(torrent.hash)
                props = self._client.torrents_properties(torrent.hash)
            except Exception as exc:
                logger.warning("Failed to fetch metadata for torrent '{}': {}; skipping.", torrent.hash, exc)
                continue

            results.append(
                {
                    "torrent_tag": tag,
                    "torrent_hash": torrent.hash,
                    "torrent_name": torrent.name,
                    "torrent_save_path": props.save_path or torrent.save_path,
                    "torrent_state": torrent.state,
                    "torrent_file_list": [{"file_name": f.name, "file_size": f.size} for f in files],
                }
            )
        return results, gamarr_count

    def delete_torrent(self, torrent_hash: str, *, delete_data: bool = False) -> bool:
        """Delete a torrent and optionally its downloaded data.

        Args:
            torrent_hash: The torrent hash to delete.
            delete_data: If True, also delete the downloaded files.

        Returns:
            True if deletion succeeded, False otherwise.
        """
        try:
            self._client.torrents_delete(delete_files=delete_data, torrent_hashes=torrent_hash)
            logger.info("Deleted torrent '{}' (delete_data={}).", torrent_hash, delete_data)
            return True
        except Exception as exc:
            logger.warning("Failed to delete torrent '{}': {}", torrent_hash, exc)
            return False
