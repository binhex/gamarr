# CLI Override Options Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow key config values (qBittorrent connection, database, library, PID) to be overridden via CLI flags.

**Architecture:** CLI options default to `None`. After `load_config()`, `_apply_cli_overrides()` mutates the `Config` object in-place with any non-None values. The scheduler receives an already-overridden `Config` object and works unchanged.

**Tech Stack:** Python 3.12+, click, APScheduler

---

## Files changed

| File | Change |
|------|--------|
| `src/gamarr/config.py` | Add `pid_path`, `library_path_list` to `GeneralConfig` |
| `src/gamarr/cli.py` | Add click options + `_apply_cli_overrides()` + wire into `cli()` |
| `tests/unit/test_cli.py` | Add tests for overrides |
| `src/gamarr/scheduler.py` | Change `run()` to accept `Config`; add PID helpers |
| `tests/unit/test_scheduler.py` | Update any tests calling `run()` |

---

### Task 1: Add config fields

**Files:**
- Modify: `src/gamarr/config.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Add fields to GeneralConfig**

In `src/gamarr/config.py`, add to `GeneralConfig`:

```python
class GeneralConfig(BaseModel):
    """Top-level general runtime options."""

    config_version: str = _CONFIG_VERSION
    daemon_mode: str = "foreground"
    log_level_console: str = "INFO"
    log_level_file: str = "INFO"
    log_path: str = "logs"
    db_path: str = "db"
    pid_path: str = "pids"                      # ← new
    library_path_list: list[str] = Field(default_factory=list)  # ← new
```

Make sure `Field` is imported at the top of config.py (it already is — used elsewhere in the file).

- [ ] **Step 2: Run existing config tests to verify nothing broke**

Run: `pytest tests/unit/test_config.py -q --no-header`
Expected: All config tests pass

- [ ] **Step 3: Commit**

```bash
git add src/gamarr/config.py
git commit -m "feat(config): add pid_path and library_path_list to GeneralConfig"
```

---

### Task 2: Add CLI override options and functions

**Files:**
- Modify: `src/gamarr/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Add click options to `cli()` decorator**

Add these options to the `@click.option(...)` decorators before `@click.version_option`:

```python
@click.option(
    "--db-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<dir>",
    help="Override the database directory from config.",
)
@click.option(
    "--pid-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<dir>",
    help="Override the PID file directory from config.",
)
@click.option(
    "--library-path",
    "library_path_list",
    multiple=True,
    default=None,
    show_default=False,
    metavar="<path>",
    help="Override library paths from config (repeatable: --library-path /a --library-path /b).",
)
@click.option(
    "--qbt-host",
    default=None,
    show_default=False,
    metavar="<host>",
    help="Override qBittorrent host from config.",
)
@click.option(
    "--qbt-port",
    type=int,
    default=None,
    show_default=False,
    metavar="<port>",
    help="Override qBittorrent WebUI port from config.",
)
@click.option(
    "--qbt-username",
    default=None,
    show_default=False,
    metavar="<user>",
    help="Override qBittorrent username from config.",
)
@click.option(
    "--qbt-password",
    default=None,
    show_default=False,
    metavar="<pass>",
    help="Override qBittorrent password from config.",
)
```

- [ ] **Step 2: Add new params to `cli()` function signature**

```python
def cli(
    config_path: str,
    log_level: str | None,
    log_path: str | None,
    test: bool,
    db_path: str | None = None,
    pid_path: str | None = None,
    library_path_list: tuple[str, ...] | None = None,
    qbt_host: str | None = None,
    qbt_port: int | None = None,
    qbt_username: str | None = None,
    qbt_password: str | None = None,
) -> None:
```

- [ ] **Step 3: Add override functions before the `cli()` definition**

```python
def _apply_general_overrides(config: Config, overrides: dict[str, object]) -> None:
    if overrides.get("db_path") is not None:
        config.general.db_path = str(overrides["db_path"])
    if overrides.get("pid_path") is not None:
        config.general.pid_path = str(overrides["pid_path"])
    if overrides.get("library_path_list") is not None:
        config.library.paths = [str(p) for p in overrides["library_path_list"]]


def _apply_qbt_overrides(config: Config, overrides: dict[str, object]) -> None:
    if overrides.get("qbt_host") is not None:
        config.torrent_client.qbittorrent.host = str(overrides["qbt_host"])
    if overrides.get("qbt_port") is not None:
        config.torrent_client.qbittorrent.port = int(overrides["qbt_port"])
    if overrides.get("qbt_username") is not None:
        config.torrent_client.qbittorrent.username = str(overrides["qbt_username"])
    if overrides.get("qbt_password") is not None:
        config.torrent_client.qbittorrent.password = str(overrides["qbt_password"])


def _apply_cli_overrides(config: Config, **overrides: object) -> None:
    """Apply non-None CLI override values onto *config* in-place."""
    _apply_general_overrides(config, overrides)
    _apply_qbt_overrides(config, overrides)
```

Add `from movarr.config import Config` import — actually, `Config` needs to be imported in cli.py. It currently only imports inside the `if test:` block. Add it at the top:

```python
from gamarr.config import Config
```

- [ ] **Step 4: Update `cli()` body to apply overrides**

Replace the existing `if test:` block and `run()` call with:

```python
    from gamarr.config import load_config

    config = load_config(config_path)

    _apply_cli_overrides(
        config,
        db_path=db_path,
        pid_path=pid_path,
        library_path_list=library_path_list,
        qbt_host=qbt_host,
        qbt_port=qbt_port,
        qbt_username=qbt_username,
        qbt_password=qbt_password,
    )

    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{level}</level>"
    ...

    if test:
        click.echo("Configuration loaded successfully. Test mode — exiting.")
        return

    from gamarr.scheduler import run

    run(config)  # was: run(config_path=config_path)
```

- [ ] **Step 5: Write tests for override functions**

Add to `tests/unit/test_cli.py`:

```python
class TestCliOverrides:
    """Tests for CLI override functions."""

    def test_apply_general_overrides_db_path(self) -> None:
        from gamarr.config import Config
        from gamarr.cli import _apply_cli_overrides

        config = Config()
        assert config.general.db_path == "db"
        _apply_cli_overrides(config, db_path="/custom/db")
        assert config.general.db_path == "/custom/db"

    def test_apply_general_overrides_pid_path(self) -> None:
        from gamarr.config import Config
        from gamarr.cli import _apply_cli_overrides

        config = Config()
        assert config.general.pid_path == "pids"
        _apply_cli_overrides(config, pid_path="/custom/pids")
        assert config.general.pid_path == "/custom/pids"

    def test_apply_general_overrides_library_paths(self) -> None:
        from gamarr.config import Config
        from gamarr.cli import _apply_cli_overrides

        config = Config()
        assert config.library.paths == []
        _apply_cli_overrides(config, library_path_list=("/media/games", "/media/more"))
        assert config.library.paths == ["/media/games", "/media/more"]

    def test_apply_qbt_overrides(self) -> None:
        from gamarr.config import Config
        from gamarr.cli import _apply_cli_overrides

        config = Config()
        _apply_cli_overrides(
            config,
            qbt_host="192.168.1.10",
            qbt_port=9090,
            qbt_username="custom",
            qbt_password="secret",
        )
        assert config.torrent_client.qbittorrent.host == "192.168.1.10"
        assert config.torrent_client.qbittorrent.port == 9090
        assert config.torrent_client.qbittorrent.username == "custom"
        assert config.torrent_client.qbittorrent.password == "secret"

    def test_override_none_does_not_change_defaults(self) -> None:
        from gamarr.config import Config
        from gamarr.cli import _apply_cli_overrides

        config = Config()
        _apply_cli_overrides(config)  # no overrides
        assert config.general.db_path == "db"
        assert config.general.pid_path == "pids"
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/unit/test_cli.py -v`
Expected: All tests pass (old tests + new tests)

- [ ] **Step 7: Commit**

```bash
git add src/gamarr/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add CLI override options for qbt, db, pid, library"
```

---

### Task 3: Update scheduler and add PID file support

**Files:**
- Modify: `src/gamarr/scheduler.py`
- Test: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Change `run()` to accept `Config` object + add PID helpers**

Replace the current `run()` function in `src/gamarr/scheduler.py`:

```python
def run(config: Config) -> None:
    """Run a scan cycle, either as scheduled daemon or single pass.

    When ``schedule.acquisition.enabled`` is ``true`` in the config, runs
    in continuous scheduled mode using APScheduler. Otherwise runs a
    single scan pass and exits.

    The PID file is written before starting and cleaned up in a
    ``finally`` block.

    Args:
        config: Application configuration (may have CLI overrides applied).
    """
    pid_path = config.general.pid_path or None
    if pid_path:
        _write_pid(pid_path)

    try:
        if config.schedule.acquisition.enabled:
            _run_daemon(config)
        else:
            run_once(config)
    finally:
        _cleanup_pid_file(pid_path)
```

Add `import os` at the top of the file if not already present (check existing imports).

Add helper functions before `run()`:

```python
def _write_pid(pid_path: str) -> None:
    """Write the current process PID to a file under *pid_path*."""
    pid_file = pid_path if os.path.splitext(pid_path)[1] else os.path.join(pid_path, "gamarr.pid")
    pid_dir = os.path.dirname(pid_file)
    if pid_dir:
        os.makedirs(pid_dir, exist_ok=True)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    logger.debug("PID {} written to '{}'.", os.getpid(), pid_file)


def _cleanup_pid_file(pid_path: str | None) -> None:
    """Remove the PID file at *pid_path* if it exists."""
    if pid_path:
        pid_file = pid_path if os.path.splitext(pid_path)[1] else os.path.join(pid_path, "gamarr.pid")
        if os.path.exists(pid_file):
            os.unlink(pid_file)
```

- [ ] **Step 2: Write tests for PID helpers**

Add to `tests/unit/test_scheduler.py`:

```python
class TestPidFile:
    """PID file write/cleanup tests."""

    def test_write_pid_creates_file(self, tmp_path: Path) -> None:
        from gamarr.scheduler import _write_pid

        pid_dir = tmp_path / "pids"
        pid_dir.mkdir()
        _write_pid(str(pid_dir))
        pid_file = pid_dir / "gamarr.pid"
        assert pid_file.exists()
        content = pid_file.read_text().strip()
        assert content == str(os.getpid())

    def test_write_pid_full_path(self, tmp_path: Path) -> None:
        from gamarr.scheduler import _write_pid

        pid_file = tmp_path / "custom.pid"
        _write_pid(str(pid_file))
        assert pid_file.exists()
        assert pid_file.read_text().strip() == str(os.getpid())

    def test_cleanup_pid_file_removes(self, tmp_path: Path) -> None:
        from gamarr.scheduler import _cleanup_pid_file

        pid_file = tmp_path / "gamarr.pid"
        pid_file.write_text("12345")
        assert pid_file.exists()
        _cleanup_pid_file(str(tmp_path))
        assert not pid_file.exists()

    def test_cleanup_pid_file_nonexistent(self, tmp_path: Path) -> None:
        """Should not raise when PID file doesn't exist."""
        from gamarr.scheduler import _cleanup_pid_file

        _cleanup_pid_file(str(tmp_path / "nonexistent.pid"))  # should not raise

    def test_cleanup_pid_file_none(self) -> None:
        """Should not raise when pid_path is None."""
        from gamarr.scheduler import _cleanup_pid_file

        _cleanup_pid_file(None)  # should not raise
```

Need `import os` in the test and `from pathlib import Path` in the test (check if already imported).

- [ ] **Step 3: Write test for run() accepting Config**

Add to `tests/unit/test_scheduler.py`:

```python
def test_run_accepts_config_object(self, tmp_path: Path) -> None:
    """run() should accept a Config object directly (not just a path)."""
    from gamarr.config import Config
    from gamarr.scheduler import run

    config = Config()
    config.general.pid_path = ""  # Disable PID file for this test
    config.schedule.acquisition.enabled = False

    # Should not raise — runs single pass with defaults
    run(config)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_scheduler.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add src/gamarr/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat(scheduler): accept Config object, add PID file support"
```

---

### Task 4: Full validation

- [ ] **Step 1: Run full test suite**

Run: `rm -f .coverage && pytest --cov=src/gamarr --cov-fail-under=95 -q`
Expected: All tests pass, coverage >= 95%

- [ ] **Step 2: Run quality checks**

Run: `uv run ruff check --fix . && uv run ruff format . && uv run mypy .`
Expected: No errors

- [ ] **Step 3: Run pre-commit**

Run: `uv run pre-commit run --all-files`
Expected: All hooks pass

- [ ] **Step 4: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "chore: final cleanup after CLI override options feature"
```

---

## Self-Review

### Spec coverage check
- ✅ `pid_path` field in `GeneralConfig` (Task 1)
- ✅ `library_path_list` field in `GeneralConfig` (Task 1)
- ✅ Click options for all 7 CLI flags (Task 2)
- ✅ `_apply_cli_overrides()` + sub-functions (Task 2)
- ✅ `run()` accepts `Config` object (Task 3)
- ✅ `_write_pid()` + `_cleanup_pid_file()` helpers (Task 3)
- ✅ PID file written before start, cleaned up in `finally` (Task 3)
- ✅ Test coverage for override functions (Task 2)
- ✅ Test coverage for PID helpers (Task 3)
- ✅ `library_path_list` maps to `config.library.paths` (Task 2)
- ✅ `_build_kwargs()` unchanged (non-goal)
- ✅ `run_acquisition()` unchanged (non-goal)

### Placeholder scan
- No "TBD", "TODO", "implement later", or "fill in details"
- No "add appropriate error handling" without specifics
- No "write tests for the above" without test code
- All steps have complete code shown
- All file paths exact

### Type consistency
- `_apply_cli_overrides(config, **overrides)` called with keyword args matching click parameter names
- `db_path: str | None` in `cli()` → `str` in override functions
- `library_path_list: tuple[str, ...] | None` from click `multiple=True` → `list[str]` in override
- `pid_path: str | None` handled with `or None` guard in scheduler
