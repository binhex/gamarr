"""Source abstraction for gamarr."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BaseSource(Protocol):
    """Protocol every source must implement."""

    @property
    def source_name(self) -> str: ...

    def fetch_sitemap(self, db: object) -> None: ...
