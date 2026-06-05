"""Library scanning for gamarr.

Scans configured library paths for existing game titles, normalizes
folder and file names, and provides fast lookups to avoid re-downloading
games already on disk.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from loguru import logger

_GAME_EXTENSIONS = (
    ".iso",
    ".zip",
    ".rar",
    ".7z",
    ".exe",
    ".bin",
    ".cue",
    ".nsp",
    ".xci",
    ".nro",
    ".nca",
    ".tar.gz",
    ".tar",
    ".gz",
)

_EDITION_SUFFIX_PATTERN = re.compile(
    r"\s*(?:"
    r"\(v?\d[\d.]*[^)]*\)"
    r"|\(\d{4}\)"
    r"|(?:Digital\s+)?Deluxe\s+Edition"
    r"|Complete\s+Edition"
    r"|Enhanced\s+Edition"
    r"|Game\s+of\s+the\s+Year\s+Edition"
    r"|Gold\s+Edition"
    r"|Platinum\s+Edition"
    r"|Ultimate\s+Edition"
    r"|Premium\s+Edition"
    r"|Limited\s+Edition"
    r"|Special\s+Edition"
    r"|Collectors?(?:'s)?\s+Edition"
    r"|Standard\s+Edition"
    r"|Phantom\s+Liberty\s+Edition"
    r"|GOTY(?:\s+Edition)?"
    r")",
    re.IGNORECASE,
)


@dataclass
class LibraryMatch:
    """Result of a library scan for a single game title."""

    found: bool
    matched_name: str
    matched_path: str


def _strip_extension(name: str) -> str:
    lower = name.lower()
    for ext in _GAME_EXTENSIONS:
        if lower.endswith(ext):
            return name[: -len(ext)]
        if lower.endswith(ext + ".part"):
            return name[: -(len(ext) + len(".part"))]
        if lower.endswith(ext + ".001"):
            return name[: -(len(ext) + len(".001"))]
    return name


def _normalise_name(name: str) -> str:
    if not name:
        return ""
    name = _strip_extension(name)
    # Remove bracketed content entirely (release group tags, etc.)
    name = re.sub(r"\[.*?\]", "", name)
    # Replace separators with spaces
    name = re.sub(r"[._-]", " ", name)
    # Remove edition/franchise suffixes before lowercasing
    name = _EDITION_SUFFIX_PATTERN.sub("", name)
    name = name.lower()
    # Remove remaining non-alphanumeric characters (preserve spaces)
    name = re.sub(r"[^a-z0-9\s]", "", name)
    # Remove trailing version suffixes like " v1 0"
    name = re.sub(r"\s+v\d[\d\s]*$", "", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _partial_match(entry_norm: str, index_key: str) -> float | None:
    """Return a similarity ratio if *entry_norm* and *index_key* partially match.

    One must be a substring of the other with a minimum length of 5
    characters. Returns a float ratio (0.0 to 1.0) on match, None otherwise.
    """
    if len(entry_norm) < 5 or len(index_key) < 5:
        return None
    if entry_norm in index_key or index_key in entry_norm:
        shorter = min(len(entry_norm), len(index_key))
        longer = max(len(entry_norm), len(index_key))
        return shorter / longer
    return None


class LibraryScanner:
    """Scans configured library paths for existing game titles."""

    def __init__(
        self,
        library_paths: list[str] | None = None,
    ) -> None:
        self._paths = library_paths or []
        self._index: dict[str, list[str]] = {}
        if self._paths:
            self._build_index()

    def _build_index(self) -> None:
        for lib_path in self._paths:
            if not os.path.isdir(lib_path):
                logger.warning("Library path '{}' does not exist or is not a directory.", lib_path)
                continue
            self._index_path(lib_path)
        logger.debug("Library index built: {} entries from {} path(s)", len(self._index), len(self._paths))

    def _index_path(self, lib_path: str) -> None:
        """Walk a single library path and index its contents."""
        for root, dirs, files in os.walk(lib_path):
            for dir_name in dirs:
                if dir_name.startswith("."):
                    continue
                norm = _normalise_name(dir_name)
                if norm:
                    self._index.setdefault(norm, []).append(os.path.join(root, dir_name))
            for file_name in files:
                norm = _normalise_name(file_name)
                if norm:
                    self._index.setdefault(norm, []).append(os.path.join(root, file_name))

    def check_game(self, title: str) -> LibraryMatch | None:
        if not self._index:
            return None

        norm = _normalise_name(title)
        if not norm:
            return None

        if norm in self._index:
            paths = self._index[norm]
            return LibraryMatch(found=True, matched_name=norm, matched_path=paths[0])

        return self._find_best_partial_match(norm)

    def _find_best_partial_match(self, norm: str) -> LibraryMatch | None:
        """Return the best partial match for *norm* in the index, or None."""
        best_key, best_path, best_ratio = None, None, 0.0
        for index_key, paths in self._index.items():
            ratio = _partial_match(norm, index_key)
            if ratio is not None and ratio > best_ratio:
                best_key, best_path, best_ratio = index_key, paths[0], ratio
        if best_key is not None and best_ratio >= 0.5:
            return LibraryMatch(found=True, matched_name=best_key, matched_path=best_path)  # type: ignore[arg-type]
        return None
