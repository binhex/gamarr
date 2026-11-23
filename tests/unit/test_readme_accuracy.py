"""Verify README.md config tables match actual code defaults.

The README is the primary user-facing documentation.  Its configuration
tables and feature descriptions must match the actual model defaults
in config.py.  Drift here means users see wrong defaults when configuring
gamarr.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
README_PATH = PROJECT_ROOT / "README.md"


def _find_config_block(lines: list[str], heading: str) -> list[str] | None:
    """Find a config table block under *heading* in the README lines.

    Returns the table rows following *heading*, or None if *heading* is
    not found. The block ends at the first non-table line.
    """
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(f"### `{heading}`") or line.strip().startswith(f"#### `{heading}`"):
            start = i
            break
    if start is None:
        return None

    # Skip the heading and the table header row
    block: list[str] = []
    in_table = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("| `"):
            in_table = True
            block.append(stripped)
        elif in_table:
            if stripped == "" or stripped.startswith("|"):
                block.append(stripped)
            else:
                break
    return block


def _parse_readme_defaults(table_lines: list[str]) -> dict[str, str]:
    """Parse a markdown table into a dict of key → default value string.

    Expects header: | Key | Description | Default |
    Skips the header separator row.
    """
    defaults: dict[str, str] = {}
    for line in table_lines:
        if line.startswith("|---|---") or line.startswith("| ---"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4:
            key = parts[1].strip("`").strip()
            default = parts[3].strip("`").strip()
            defaults[key] = default
    return defaults


class TestReadmeGeneralConfig:
    """README `general` table must match GeneralConfig defaults."""

    def _readme_defaults(self) -> dict[str, str]:
        with open(README_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        block = _find_config_block(lines, "general")
        assert block is not None, "Could not find ### `general` config block in README"
        return _parse_readme_defaults(block)

    def test_missing_pid_path(self) -> None:
        """README general table should list pid_path with default 'pids'."""
        defaults = self._readme_defaults()
        assert "pid_path" in defaults, (
            "README is missing pid_path in the general config table. Code: pid_path: str = 'pids'"
        )
        assert defaults["pid_path"] == "pids", (
            f"README says pid_path default is '{defaults['pid_path']}', expected 'pids'"
        )

    def test_missing_library_path_list(self) -> None:
        """README general table should list library_path_list."""
        defaults = self._readme_defaults()
        assert "library_path_list" in defaults, (
            "README is missing library_path_list in the general config table. Used by --library-path CLI flag."
        )


class TestReadmeScheduleConfig:
    """README `schedule` table must match ScheduleConfig defaults."""

    def test_schedule_enabled_default(self) -> None:
        """schedule.enabled default is false, not true."""
        from gamarr.config import ScheduleConfig

        cfg = ScheduleConfig()
        assert cfg.enabled is False, "Code default is False"

    def test_schedule_enabled_readme_default(self) -> None:
        """Verify the README documents the correct default for schedule.enabled."""
        with open(README_PATH, encoding="utf-8") as f:
            content = f.read()

        # Find the schedule table entry for enabled
        import re

        # Look for the row in the schedule table
        pattern = r"\|\s*`(?:schedule\.)?enabled`\s*\|.*?\|\s*(.*?)\s*\|"
        match = re.search(pattern, content)
        assert match is not None, "Could not find schedule.enabled in schedule table"
        readme_default = match.group(1).strip().strip("`")
        assert readme_default == "false", (
            f"README says schedule.enabled default is '{readme_default}', expected 'false' (code default is False)"
        )


class TestReadmeCriticRenameRows:
    """README config table must reflect the criticscore rename."""

    def test_critic_threshold_rows_use_new_names(self) -> None:
        from gamarr.config import MetacriticPlatformConfig

        with open(README_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        block = _find_config_block(lines, "review_sites.metacritic.platform_overrides.<platform>")
        assert block is not None, "platform_overrides table not found in README"
        defaults = _parse_readme_defaults(block)

        cfg = MetacriticPlatformConfig()
        assert defaults.get("min_criticscore") == str(cfg.min_criticscore), defaults
        assert defaults.get("min_criticscore_reviews") == str(cfg.min_criticscore_reviews), defaults
        assert "min_metascore" not in defaults, "legacy key must not appear in the README table"

    def test_cache_hours_rows_renamed(self) -> None:
        """Both cache_hours rows exist and no legacy row survives."""
        with open(README_PATH, encoding="utf-8") as f:
            readme = f.read()
        assert "cache_pages_hours" not in readme, "Legacy key must not remain in the README"
        assert readme.count("| `cache_hours` |") >= 2, "Expected the Metacritic and per-source cache_hours rows"


class TestReadmeFeatureDescriptions:
    """README feature descriptions must match current code behavior."""

    def test_retry_limit_feature_is_accurate(self) -> None:
        """The re-verification feature must not reference max_verify_attempts.

        max_verify_attempts was removed; retry is now controlled by
        source_matching_days (expiry-based).
        """
        with open(README_PATH, encoding="utf-8") as f:
            content = f.read()

        # Check the feature list for mentions of the old mechanism
        if "maximum number of attempts" in content:
            # This is fine as long as it's not in the feature description
            # Let's be more specific: check the features section
            features_section_start = content.find("## Features")
            features_section_end = content.find("## Prerequisites")
            features = content[features_section_start:features_section_end]

            assert "maximum number of attempts" not in features, (
                "Feature section mentions 'maximum number of attempts' "
                "but max_verify_attempts was removed. Should describe "
                "source_matching_days-based expiry model instead."
            )


class TestReadmeYamlDefaults:
    """README defaults must match _default_config_dict() exactly."""

    def test_general_defaults_match(self) -> None:
        """Compare all general section defaults against the default config dict."""
        from gamarr.config import _CONFIG_VERSION, _default_config_dict

        defaults = _default_config_dict()
        general = defaults.get("general", {})

        # Keys and defaults from README (correct ones that should be there)
        expected: dict[str, str] = {
            "config_version": _CONFIG_VERSION,
            "daemon_mode": "foreground",
            "log_level_console": "INFO",
            "log_level_file": "INFO",
            "log_path": "logs",
            "db_path": "db",
            "pid_path": "pids",
        }
        for key, expected_val in expected.items():
            actual = general.get(key)
            assert str(actual) == expected_val, f"general.{key}: README expected '{expected_val}', code has '{actual}'"

    def test_schedule_defaults_match(self) -> None:
        """Compare schedule section defaults against the default config dict."""
        from gamarr.config import _default_config_dict

        defaults = _default_config_dict()
        schedule = defaults.get("schedule", {})

        assert schedule.get("enabled") is False, f"schedule.enabled: expected False, got {schedule.get('enabled')}"
        assert schedule.get("schedule_time_mins") == 60
        assert schedule.get("run_on_start") is True


class TestReadmeTopLevelDefaults:
    """The top-level source window row must match the model default."""

    def test_source_matching_days_default_row(self) -> None:
        from gamarr.config import Config

        readme = README_PATH.read_text(encoding="utf-8")
        row = next((line for line in readme.splitlines() if line.startswith("| `source_matching_days` |")), "")
        assert row, "the top-level source_matching_days row must exist"
        assert row.rstrip().endswith(f"| `{Config().source_matching_days}` |"), row


class TestReadmeDiscoveryRows:
    """README must document the discovery blocks and their defaults."""

    def _readme_defaults(self) -> dict[str, str]:
        with open(README_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        block = _find_config_block(lines, "review_sites.metacritic.platform_overrides.<platform>")
        assert block is not None, "platform_overrides table missing from README"
        return _parse_readme_defaults(block)

    def test_mode_default_row(self) -> None:
        from gamarr.config import DiscoveryConfig

        discovery = DiscoveryConfig()
        assert self._readme_defaults().get("discovery.mode") == f'"{discovery.mode}"'

    def test_latest_pace_default_row(self) -> None:
        from gamarr.config import DiscoveryConfig

        discovery = DiscoveryConfig()
        assert self._readme_defaults().get("discovery.latest.months_per_cycle") == str(
            discovery.latest.months_per_cycle
        )

    def test_backlog_sort_order_row_documents_the_three_values(self) -> None:
        with open(README_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        block = _find_config_block(lines, "review_sites.metacritic.platform_overrides.<platform>")
        assert block is not None
        row = next((line for line in block if line.startswith("| `discovery.backlog.sort_order`")), "")
        assert row, "the backlog sort_order row must exist"
        assert '"criticscore"' in row and '"userscore"' in row, row
        assert row.rstrip().endswith('`"new"` |'), row

    def test_backlog_page_depth_default_row(self) -> None:
        from gamarr.config import DiscoveryConfig

        discovery = DiscoveryConfig()
        assert self._readme_defaults().get("discovery.backlog.score.page_depth") == str(
            discovery.backlog.score.page_depth
        )

    def test_no_legacy_discovery_rows_remain(self) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        for legacy in ("`max_date`", "`max_cycle_months`", "`max_cycle_pages`", "`max_queue_days`"):
            assert legacy not in readme, f"legacy key {legacy} still documented"
        for line in readme.splitlines():
            assert not line.startswith("| `max_pages`"), "the max_pages config row must be gone"
        assert "`source_matching_days`" in readme, "the replacement key must be documented"
