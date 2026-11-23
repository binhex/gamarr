"""Tests for the discovery scan plan."""

from __future__ import annotations

from gamarr.config import DiscoveryConfig
from gamarr.discovery import build_scan_plan


class TestBuildScanPlan:
    """One plan per mode and sort, no key ambiguity (ADR 0001, 0003, 0005)."""

    def test_latest_is_always_date_mode_in_new_order(self) -> None:
        plan = build_scan_plan(DiscoveryConfig.model_validate({"mode": "latest"}))
        assert plan.mode == "latest"
        assert plan.scan_order == "new"
        assert plan.date_mode is True
        assert plan.floor_month == 0
        assert plan.pace_months == 1
        assert plan.page_depth == 0
        assert plan.pages_per_cycle == 0

    def test_latest_carries_floor_and_pace(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {"mode": "latest", "latest": {"oldest_month": 202601, "months_per_cycle": 3}}
        )
        plan = build_scan_plan(discovery)
        assert plan.floor_month == 202601
        assert plan.pace_months == 3

    def test_backlog_new_is_date_mode(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {"mode": "backlog", "backlog": {"sort_order": "new", "new": {"oldest_month": 202001}}}
        )
        plan = build_scan_plan(discovery)
        assert plan.scan_order == "new"
        assert plan.date_mode is True
        assert plan.floor_month == 202001
        assert plan.page_depth == 0

    def test_backlog_score_is_page_mode(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {"mode": "backlog", "backlog": {"sort_order": "criticscore", "score": {"page_depth": 40}}}
        )
        plan = build_scan_plan(discovery)
        assert plan.scan_order == "criticscore"
        assert plan.date_mode is False
        assert plan.floor_month == 0
        assert plan.pace_months == 0
        assert plan.page_depth == 40

    def test_userscore_shares_the_score_block(self) -> None:
        discovery = DiscoveryConfig.model_validate(
            {"mode": "backlog", "backlog": {"sort_order": "userscore", "score": {"pages_per_cycle": 7}}}
        )
        plan = build_scan_plan(discovery)
        assert plan.scan_order == "userscore"
        assert plan.pages_per_cycle == 7
