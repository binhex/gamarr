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


def _find_config_block(lines: list[str], heading: str, end_marker: str = "## ") -> list[str] | None:
    """Find a config table block under *heading* in the README lines.

    Returns the lines between *heading* and the next section or *end_marker*,
    or None if *heading* is not found.
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


class TestReadmeMetacriticPlatformConfig:
    """README `platform_overrides.<platform>` table must match MetacriticPlatformConfig defaults."""

    def _readme_defaults(self) -> dict[str, str]:
        with open(README_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        block = _find_config_block(lines, "review_sites.metacritic.platform_overrides.<platform>")
        assert block is not None, (
            "Could not find `review_sites.metacritic.platform_overrides.<platform>` config block in README"
        )
        return _parse_readme_defaults(block)

    def test_max_weeks_default(self) -> None:
        """max_weeks default is 13, not null."""
        from gamarr.config import MetacriticPlatformConfig

        cfg = MetacriticPlatformConfig()
        assert cfg.max_weeks == 13, f"Code default is 13, got {cfg.max_weeks}"

        defaults = self._readme_defaults()
        readme_val = defaults.get("max_weeks", "MISSING")
        assert readme_val == "13", (
            f"README says max_weeks default is '{readme_val}', expected '13' (code default is 13)"
        )


class TestReadmeFeatureDescriptions:
    """README feature descriptions must match current code behavior."""

    def test_retry_limit_feature_is_accurate(self) -> None:
        """The re-verification feature must not reference max_verify_attempts.

        max_verify_attempts was removed; retry is now controlled by
        max_queue_days (expiry-based).
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
                "max_queue_days-based expiry model instead."
            )


class TestReadmeYamlDefaults:
    """README defaults must match _default_config_dict() exactly."""

    def test_general_defaults_match(self) -> None:
        """Compare all general section defaults against the default config dict."""
        from gamarr.config import _default_config_dict

        defaults = _default_config_dict()
        general = defaults.get("general", {})

        # Keys and defaults from README (correct ones that should be there)
        expected: dict[str, str] = {
            "config_version": "1.0.0",
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
