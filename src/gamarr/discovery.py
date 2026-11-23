"""Resolve the discovery config into the scan plan one cycle runs.

The plan is the single answer to "what will this cycle read?", so the pipeline
never asks the config which mode it is in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from gamarr.config import DiscoveryConfig

SCAN_ORDER = Literal["new", "criticscore", "userscore"]
PENDING_ORDER = Literal["new", "criticscore", "userscore"]
DISCOVERY_MODE = Literal["latest", "backlog"]


@dataclass(frozen=True)
class ScanPlan:
    """What one discovery cycle will read.

    Attributes:
        mode: The selected discovery mode.
        scan_order: The browse order the cycle reads.
        date_mode: True when release dates bound the cycle instead of a page count.
        floor_month: The window floor as ``YYYYMM``, ``0`` when there is none.
        pace_months: Months one cycle reads back, ``0`` for no pacing.
        page_depth: Total page budget, ``0`` for none.
        pages_per_cycle: Per-cycle page pace, ``0`` for unlimited.
    """

    mode: DISCOVERY_MODE
    scan_order: SCAN_ORDER
    date_mode: bool
    floor_month: int
    pace_months: int
    page_depth: int
    pages_per_cycle: int


def build_scan_plan(discovery: DiscoveryConfig) -> ScanPlan:
    """Return the plan for the configured mode and sort order.

    Args:
        discovery: The validated discovery config.

    Returns:
        The plan the cycle runs.
    """
    if discovery.mode == "latest":
        latest = discovery.latest
        return ScanPlan(
            mode="latest",
            scan_order="new",
            date_mode=True,
            floor_month=latest.oldest_month,
            pace_months=latest.months_per_cycle,
            page_depth=0,
            pages_per_cycle=0,
        )
    backlog = discovery.backlog
    if backlog.sort_order == "new":
        floor_month = backlog.new.oldest_month
        pace_months = backlog.new.months_per_cycle
        if pace_months == 0 and floor_month == 0:
            # No floor and no pace is the whole catalogue in one cycle.  The model
            # default paces a sweep, so this only catches an explicit 0; bound it
            # the same way rather than trusting the file (bug 2026-11-23).
            pace_months = 1
        return ScanPlan(
            mode="backlog",
            scan_order="new",
            date_mode=True,
            floor_month=floor_month,
            pace_months=pace_months,
            page_depth=0,
            pages_per_cycle=0,
        )
    return ScanPlan(
        mode="backlog",
        scan_order=backlog.sort_order,
        date_mode=False,
        floor_month=0,
        pace_months=0,
        page_depth=backlog.score.page_depth,
        pages_per_cycle=backlog.score.pages_per_cycle,
    )
