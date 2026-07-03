"""Source abstraction for gamarr."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import threading


@runtime_checkable
class BaseSource(Protocol):
    """Protocol every source must implement."""

    @property
    def source_name(self) -> str: ...

    def fetch_sitemap(self, db: object, cancel_event: threading.Event | None = None) -> None: ...

    def close(self) -> None: ...
