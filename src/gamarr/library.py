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
    r"|Game\s+of\s+the\s+Year\s+Edition"
    r"|Gold\s+Edition"
    r"|Ultimate\s+Edition"
    r"|Premium\s+Edition"
    r"|GOTY\b"
    r"|Standard\s+Edition"
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
        if lower.endswith(ext + ".part") or lower.endswith(ext + ".001"):
            return name[: -(len(ext) + 5)]
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


def _partial_match(entry_norm: str, index_key: str) -> bool:
    if len(entry_norm) < 5 or len(index_key) < 5:
        return False
    if entry_norm in index_key or index_key in entry_norm:
        shorter = min(len(entry_norm), len(index_key))
        longer = max(len(entry_norm), len(index_key))
        if shorter / longer >= 0.6:
            return True
    return False


class LibraryScanner:
    """Scans configured library paths for existing game titles."""

    def __init__(
        self,
        library_paths: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._paths = library_paths or []
        self._index: dict[str, list[str]] = {}
        if enabled and self._paths:
            self._build_index()

    def _build_index(self) -> None:
        for lib_path in self._paths:
            if not os.path.isdir(lib_path):
                logger.warning("Library path '{}' does not exist or is not a directory.", lib_path)
                continue
            for root, dirs, files in os.walk(lib_path):
                for dir_name in dirs:
                    norm = _normalise_name(dir_name)
                    full = os.path.join(root, dir_name)
                    self._index.setdefault(norm, []).append(full)
                for file_name in files:
                    norm = _normalise_name(file_name)
                    if norm:
                        full = os.path.join(root, file_name)
                        self._index.setdefault(norm, []).append(full)
        logger.debug("Library index built: {} entries from {} path(s)", len(self._index), len(self._paths))

    def check_game(self, title: str) -> LibraryMatch | None:
        if not self._enabled or not self._index:
            return None

        norm = _normalise_name(title)
        if not norm:
            return None

        if norm in self._index:
            paths = self._index[norm]
            return LibraryMatch(found=True, matched_name=norm, matched_path=paths[0])

        for index_key, paths in self._index.items():
            if _partial_match(norm, index_key):
                return LibraryMatch(found=True, matched_name=index_key, matched_path=paths[0])

        return None
