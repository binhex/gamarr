"""Source abstraction for gamarr."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from gamarr.models import GameEntry


@runtime_checkable
class BaseSource(Protocol):
    """Protocol every source must implement."""

    @property
    def source_name(self) -> str: ...

    def fetch_new(self) -> list[GameEntry]: ...
