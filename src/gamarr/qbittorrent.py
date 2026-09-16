"""qBittorrent WebUI client wrapper for gamarr."""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from dataclasses import dataclass
from typing import Any

import qbittorrentapi
from loguru import logger

_TAG_PREFIX = "gamarr-"
_INFOHASH_PATTERN = re.compile(r"xt=urn:btih:([0-9A-Za-z]+)", re.IGNORECASE)


@dataclass(frozen=True)
class TorrentCounts:
    """Breakdown of the gamarr torrents seen by :meth:`QBittorrentClient.list_completed`.

    ``downloading`` counts the remaining torrents: those that hold their metadata
    but are not finished, plus any whose file listing could not be read.  It lets a
    summary line report in-progress work separately from torrents that are still
    waiting for metadata.
    """

    downloading: int
    awaiting_metadata: int


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


def _match_infohash(torrents: Any, infohash: str) -> Any | None:
    """Return the torrent from *torrents* whose hash equals *infohash*.

    The hash filter is only honoured from Web API 2.0.1 onwards, so the answer is
    matched explicitly instead of being trusted positionally: a server that
    ignores the filter returns every torrent in the session.
    """
    wanted = infohash.lower()
    return next((t for t in torrents if str(getattr(t, "hash", "")).lower() == wanted), None)


def _gamarr_tag_of(torrent: Any) -> str:
    """Return the gamarr tag *torrent* carries, or "" when it carries none."""
    return _extract_gamarr_tag(str(getattr(torrent, "tags", "") or ""))


def _has_tag(torrent: Any, tag: str) -> bool:
    """Return True when *torrent* carries *tag* in its comma-separated tag list."""
    tags = {t.strip() for t in str(getattr(torrent, "tags", "") or "").split(",")}
    return tag in tags


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


def _int_or_none(value: Any) -> int | None:
    """Return *value* as an int, or ``None`` when it is not a plain number."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _has_metadata(torrent: Any) -> bool:
    """Return False only when qBittorrent explicitly reports missing metadata.

    Unknown shapes (such as test doubles) count as having metadata, so only an
    explicit ``has_metadata: false`` answer changes behaviour.
    """
    return getattr(torrent, "has_metadata", None) is not False


def _has_completed_payload(torrent: Any) -> bool:
    """Return True only when qBittorrent reports real, complete payload.

    A magnet whose metadata has not been fetched yet answers ``amount_left == 0``
    with ``has_metadata == false`` and ``size == 0``.  Reading that as complete
    sends post-processing looking for files that do not exist, which surfaces as
    a bogus copy failure on every post-processing cycle.
    """
    if not _has_metadata(torrent):
        return False
    amount_left = _int_or_none(getattr(torrent, "amount_left", None))
    if amount_left is not None and amount_left != 0:
        return False
    size = _int_or_none(getattr(torrent, "size", None))
    return size is None or size > 0


def _classify_torrent(torrent: Any) -> str:
    """Classify *torrent* as ``complete``, ``downloading`` or ``awaiting_metadata``."""
    if _has_completed_payload(torrent):
        return "complete"
    return "downloading" if _has_metadata(torrent) else "awaiting_metadata"


def _log_awaiting_metadata(names: list[str]) -> None:
    """Log one DEBUG line per torrent still waiting for its metadata."""
    for name in names:
        logger.debug("Torrent '{}' has no metadata yet; post-processing will retry once data arrives.", name)


def _is_gamarr_owned(torrent: Any, category: str) -> bool:
    """Return True when *torrent* should be adopted as gamarr's own.

    A torrent counts as gamarr's when it carries a ``gamarr-`` tag or sits in
    gamarr's qBittorrent category, even if the user added it: being in that
    category is the opt-in.  Only torrents outside the configured category are
    left completely untouched, so an empty configured *category* matches nothing
    (without this guard every uncategorised torrent would look like gamarr's).
    """
    if _gamarr_tag_of(torrent):
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
        torrent outside gamarr's category is left completely untouched and
        reported as a failure, so the caller keeps the game pending and records
        it as skipped on the next cycle instead of inventing a tag for it.

        Returns:
            A ``gamarr-*`` tag on success — a fresh ``gamarr-{uuid}`` tag for a
            new add, or the adopted torrent's existing tag when it was already
            present and gamarr's own — or False when nothing was added under
            gamarr's ownership.
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

    def is_torrent_present(self, magnet_url: str) -> bool | None:
        """Report whether qBittorrent already holds the magnet's torrent.

        Re-adding a magnet qBittorrent already holds is a duplicate, not a
        delivery failure, so the caller can skip the upload instead of retrying
        it forever.

        Returns:
            True when the torrent is present, False when it is definitely absent,
            and ``None`` when the question could not be answered (unusable magnet,
            or qBittorrent unreachable).  ``None`` must not be treated as absent:
            a duplicate upload would then be reported as a success while nothing
            was added.
        """
        infohash = _magnet_infohash(magnet_url or "")
        if infohash is None:
            return None
        try:
            present = _match_infohash(self._client.torrents_info(torrent_hashes=infohash), infohash)
        except Exception as exc:
            logger.info("Could not check whether the torrent is already present: {}", exc)
            return None
        return present is not None

    def adopt_present_torrent(self, magnet_url: str, title: str = "") -> str | bool | None:
        """Adopt an already-present torrent so gamarr keeps ownership of it.

        A torrent gamarr already holds must not be re-uploaded, but it must stay
        visible to post-processing: this tags an untagged torrent that sits in
        gamarr's category (the documented opt-in) and returns the ``gamarr-*`` tag
        it carries afterwards.

        Returns:
            The torrent's gamarr tag; ``False`` when the torrent is present but
            sits outside gamarr's category, so there is nothing for gamarr to
            copy; or ``None`` when the torrent could not be found or could not be
            tagged, which the caller must treat as a retryable failure.
        """
        infohash = _magnet_infohash(magnet_url or "")
        if infohash is None:
            return None
        torrent = self._find_torrent(infohash)
        if torrent is None:
            return None
        if not _is_gamarr_owned(torrent, self._category):
            return False
        return self._tag_present_torrent(infohash, title, torrent)

    def _tag_present_torrent(self, infohash: str, title: str, torrent: Any) -> str | None:
        """Return the torrent's gamarr tag, applying gamarr's tag and category first.

        The category matters as much as the tag: post-processing only ever lists
        torrents in gamarr's category, so a torrent that carries a gamarr tag but
        sits elsewhere would be recorded as delivered and then never copied.
        """
        existing = _gamarr_tag_of(torrent)
        candidate = existing or f"{_TAG_PREFIX}{uuid.uuid4()}"
        adopted = self._adopt_existing_torrent(infohash, candidate, torrent)
        if not isinstance(adopted, str):
            return None
        if not self._tag_is_applied(infohash, adopted):
            # qBittorrent answers 200 even for hashes it does not know, so an
            # unverified adoption must not be recorded as delivered.
            logger.info("Tag '{}' is not applied to torrent '{}' yet; retrying next cycle", adopted, infohash)
            return None
        if not existing:
            logger.debug("Adopted already-present torrent '{}' as '{}'", title or infohash, candidate)
        return adopted

    def _tag_is_applied(self, infohash: str, tag: str) -> bool:
        """Return True when *infohash* really carries *tag* after adoption."""
        torrent = self._find_torrent(infohash)
        return torrent is not None and _has_tag(torrent, tag)

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
                    adopted = self._adopt_existing_torrent(infohash, tag, torrent)
                    if isinstance(adopted, str):
                        logger.info(
                            "Torrent '{}' is already present in qBittorrent ({}) \u2014 adopted it for post-processing",
                            title,
                            infohash,
                        )
                    return adopted
                logger.info(
                    "Torrent '{}' is already present in qBittorrent ({}) but was not added by gamarr "
                    "\u2014 leaving it untouched",
                    title,
                    infohash,
                )
                # Not gamarr's to manage, so no tag is applied: report the failure
                # so the game stays pending and the next cycle records it skipped
                # once the presence check sees it.
                return False
        logger.warning(
            "Failed to add torrent '{}' (infohash {}): {}",
            title,
            infohash or "unknown",
            exc,
        )
        return False

    def _reannounce_tag(self, tag: str, title: str) -> None:
        """Reannounce the freshly added torrent so its trackers pick it up.

        The tag filter is only honoured from Web API 2.8.3 onwards, so the answer
        is matched against *tag* instead of trusting the first entry.
        """
        try:
            infos = [t for t in self._client.torrents_info(tag=tag) if _has_tag(t, tag)]
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
        return _match_infohash(infos, infohash)

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
        existing = _gamarr_tag_of(torrent)
        if not existing:
            try:
                self._client.torrents_add_tags(tags=fallback_tag, torrent_hashes=infohash)
            except Exception as exc:
                logger.info("Could not tag already-present torrent '{}': {} \u2014 retrying next cycle", infohash, exc)
                return False
            existing = fallback_tag
        if self._category:
            # An empty category would clear whatever the user had set.
            try:
                self._client.torrents_set_category(category=self._category, torrent_hashes=infohash)
            except Exception as exc:
                logger.info(
                    "Could not set category on already-present torrent '{}': {} \u2014 retrying next cycle",
                    infohash,
                    exc,
                )
                return False
        return existing

    def _completed_entry(self, torrent: Any, tag: str) -> dict[str, Any] | None:
        """Return the post-processing entry for *torrent*, or None if its files cannot be read."""
        try:
            files = self._client.torrents_files(torrent.hash)
            props = self._client.torrents_properties(torrent.hash)
        except Exception as exc:
            logger.warning("Failed to fetch metadata for torrent '{}': {}; skipping.", torrent.hash, exc)
            return None
        return {
            "torrent_tag": tag,
            "torrent_hash": torrent.hash,
            "torrent_name": torrent.name,
            "torrent_save_path": props.save_path or torrent.save_path,
            "torrent_state": torrent.state,
            "torrent_file_list": [{"file_name": f.name, "file_size": f.size} for f in files],
        }

    def list_completed(self) -> tuple[list[dict[str, Any]], TorrentCounts]:
        """Return (completed_list, counts) for gamarr-tagged torrents.

        Queries by category, filters to gamarr-tagged torrents that actually hold
        their payload (metadata present, ``amount_left == 0`` and a non-zero
        size), then returns each torrent's files and properties.

        A magnet whose metadata has not been fetched yet reports
        ``amount_left == 0``, so it is excluded here and counted in
        :class:`TorrentCounts` instead of being mistaken for a finished download.

        Returns:
            A tuple of (completed_torrents, counts) where ``completed_torrents``
            contains torrents whose payload is present and ``counts`` breaks the
            remaining gamarr torrents down into in-progress and metadata-less.
        """
        try:
            all_torrents = self._client.torrents_info(category=self._category)
        except Exception as exc:
            logger.warning("Failed to list completed torrents: {}", exc)
            return [], TorrentCounts(downloading=0, awaiting_metadata=0)

        results: list[dict[str, Any]] = []
        awaiting_metadata: list[str] = []
        gamarr_count = 0
        for torrent in all_torrents:
            tag = _gamarr_tag_of(torrent)
            if not tag:
                continue
            gamarr_count += 1
            state = _classify_torrent(torrent)
            if state == "awaiting_metadata":
                awaiting_metadata.append(str(getattr(torrent, "name", "") or tag))
            elif state == "complete":
                entry = self._completed_entry(torrent, tag)
                if entry is not None:
                    results.append(entry)
        _log_awaiting_metadata(awaiting_metadata)
        counts = TorrentCounts(
            downloading=gamarr_count - len(awaiting_metadata) - len(results),
            awaiting_metadata=len(awaiting_metadata),
        )
        return results, counts

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
