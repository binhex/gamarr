# Pipeline Test Migration for search_mode API

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update 50 existing pipeline tests to use the new `search_mode` parameter and mode-specific Database methods introduced by the backlog/latest mode split.

**Architecture:** The `search_mode` refactor changed all pipeline function signatures to accept `search_mode: Literal["backlog", "latest"]`, defaulting to `"latest"`. The `Database` class gained mode-specific CRUD methods (`record_backlog_pending`, `get_backlog_pending`, etc.). The legacy `pending_games` table data is migrated to `pending_games_backlog` on `Database.__init__()`. All existing tests operate on backlog data and need `search_mode="backlog"`.

**Tech Stack:** pytest, Python 3.12+

---

### Task 1: Add `search_mode="backlog"` to all remaining `run_acquisition()` calls

**Files:**
- Modify: `tests/unit/test_pipeline.py`

- [ ] **Step 1: Identify missing calls**

Run: `grep -n "run_acquisition(" tests/unit/test_pipeline.py`

For each call NOT already containing `search_mode`, add `search_mode="backlog",` as the first keyword argument or insert before the closing `)`.

- [ ] **Step 2: Apply changes**

For example, changing:
```python
run_acquisition(
    platform="pc",
    ...
)
```
To:
```python
run_acquisition(
    platform="pc",
    search_mode="backlog",
    ...
)
```

Lines needing this fix include calls in: `TestVerifyPendingScoresEdgeCases`, `TestScrapeHealth`, `TestBrowseReviewCountPrefilter`, `TestProcessByAge`, `TestProcessAgedGames`, `TestAgedGamesMatchOrder`, `TestBacklogAdvancing`, `TestCancellation`.

- [ ] **Step 3: Verify**

```bash
uv run pytest tests/unit/test_pipeline.py -v 2>&1 | tail -5
```

---

### Task 2: Replace legacy DB method calls with backlog variants

**Files:**
- Modify: `tests/unit/test_pipeline.py`

Find all calls to the following legacy methods and replace with backlog-specific variants:

| Legacy method | Backlog variant |
|---|---|
| `db.record_pending(` | `db.record_backlog_pending(` |
| `db.get_pending(` | `db.get_backlog_pending(` |
| `db.remove_pending(` | `db.remove_backlog_pending(` |
| `db.touch_pending(` | `db.touch_backlog_pending(` |
| `db.update_pending_scores(` | `db.update_backlog_pending_scores(` |
| `db.get_expired_pending(` | `db.get_expired_backlog_pending(` |
| `db.is_pending(` | `db.is_backlog_pending(` |
| `db.has_verified_pending(` | `db.has_verified_backlog_pending(` |

Apply to all test classes listed in the failing test list.

- [ ] **Step 1: Verify final state**

```bash
uv run pytest tests/unit/test_pipeline.py -v 2>&1 | tail -5
```

Expected: all PASS

---

### Task 3: Verify full test suite

- [ ] **Step 1: Run all tests**

```bash
uv run pytest tests/ -v 2>&1 | tail -10
```

Expected: 0 failures

- [ ] **Step 2: Commit**

```bash
git add tests/unit/test_pipeline.py
git commit -m "test: update pipeline tests for search_mode and mode-specific DB methods"
```
