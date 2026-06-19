"""End-to-end test for --clear-cache using a temp database."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def test_clear_cache_via_cli_sitemap(tmp_path: Path) -> None:
    """--clear-cache fitgirl removes the fitgirl sitemap cache row."""
    import shutil
    import subprocess

    from gamarr.database import Database

    # Create a temp database directory
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    db_path = db_dir / "gamarr.db"

    # Pre-populate sitemap cache entries
    db = Database(str(db_path))
    db.set_sitemap_cache("fitgirl")
    db.close()

    # Create a minimal gamarr config dir with gamarr.yml pointing at the temp db
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "gamarr.yml"
    config_file.write_text(
        f"""\
general:
  config_version: "1.35.0"
  db_path: "{db_dir}"
schedule:
  acquisition:
    enabled: false
"""
    )

    # Run gamarr with --clear-cache fitgirl
    gamarr_bin = shutil.which("gamarr")
    assert gamarr_bin is not None, "gamarr binary not found in PATH"
    result = subprocess.run(
        [gamarr_bin, "--config-path", str(config_dir), "--clear-cache", "fitgirl"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    db2 = Database(str(db_path))
    assert not db2.get_sitemap_cache("fitgirl", 9999), f"fitgirl should be cleared\n{result.stdout}\n{result.stderr}"
    db2.close()
