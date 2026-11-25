"""Resolve the discovery config into the scan plan one cycle runs.

The plan is the single answer to "what will this cycle read?", so the pipeline
never asks the config which mode it is in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from loguru import logger

from gamarr.config import DEFAULT_SCORE_PAGES_PER_CYCLE

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
        date_mode: True when release months pace the cycle; a score sweep reads by year instead.
        floor_month: The window floor as ``YYYYMM``, read by both branches and by year for score, ``0`` for none.
        pace_months: Months one cycle reads back, ``0`` for no pacing.
        pages_per_cycle: Per-cycle page pace, ``0`` for unlimited.
    """

    mode: DISCOVERY_MODE
    scan_order: SCAN_ORDER
    date_mode: bool
    floor_month: int
    pace_months: int
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
            pages_per_cycle=0,
        )
    if backlog.score.pages_per_cycle == 0:
        logger.info(
            "Config: pages_per_cycle 0 has no meaning for a score sweep - "
            f"using {DEFAULT_SCORE_PAGES_PER_CYCLE} so one cycle cannot crawl the catalogue"
        )
    return ScanPlan(
        mode="backlog",
        scan_order=backlog.sort_order,
        date_mode=False,
        floor_month=backlog.score.oldest_month,
        pace_months=0,
        pages_per_cycle=backlog.score.pages_per_cycle or DEFAULT_SCORE_PAGES_PER_CYCLE,
    )
