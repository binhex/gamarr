# Config error reporting — review residuals (plateau stop)

Branch: `main` · HEAD at review start: `d6a375c` (the change is uncommitted) · Rounds run: **3** of cap
**10** · Reviewers: `opencode-go/deepseek-v4.1-flash:max` (A) and `opencode-go/glm-5.3-flash:max` (B).

The loop ended by **user decision on a plateau**, not at the cap: two consecutive rounds carried a
non-zero fix-induced count that did not fall (round 2: 5, round 3: 6). The user chose "Stop now and
record residuals". Rounds 1 and 2 are closed — the following round's reviewers verified every fix from
them. The round-3 fixes were applied after the last reviewer pass and were **never independently
re-reviewed**.

## Findings from the stopping round (round 3)

| Severity | Location | Description | Status |
| --- | --- | --- | --- |
| MEDIUM | `tests/unit/test_cli.py` (unreadable-file test) | The test denied reads with `chmod(0o000)`, which uid 0 ignores (CAP_DAC_OVERRIDE), so it would fail or fail to isolate on a root runner. | `applied, not independently re-reviewed` — the denial is now injected with `monkeypatch.setattr(Path, "open", ...)`, matching the pattern already used in `test_config.py` |
| LOW | `src/gamarr/config.py` (`load_config`) | The "Validate first" comment appeared twice verbatim, and the removed comment had explained the load-bearing `copy.deepcopy(merged)`. | `applied, not independently re-reviewed` — one comment, deepcopy rationale restored |
| LOW | `src/gamarr/cli.py` (`_report_config_error`) | A multi-line parser message printed continuation lines without the `  - ` list prefix, so only the first line looked like a problem. | `applied, not independently re-reviewed` — non-schema messages are split into one list item per line |
| LOW | `src/gamarr/cli.py` (header) | An unreadable-but-valid file was headed "Configuration is invalid". | `applied, not independently re-reviewed` — non-schema failures now read "Configuration could not be read or parsed" |
| LOW | `src/gamarr/cli.py` (`cli`) | The round-1/2 handler pushed `cli` to CC 9, CRAP 9.08 — one function over the project's tech-debt gate (threshold 9). | `applied, not independently re-reviewed` — reporting extracted to `_report_config_error`; measured after the fix: `cli` CRAP 7.10, `#>=thr = 0` |
| LOW | `tests/unit/test_config.py` (pending_order rewrite) | The round-2 persistence fix was asserted only in memory; the on-disk half (the actual complaint) had no test. | `applied, not independently re-reviewed` — `test_a_pending_order_metascore_is_corrected_on_disk` added |

## Per-round provenance

```text
Round 1: 0 high, 1 medium, 3 low — 4 new, 0 fix-induced
  fixes: README.md:559 / cli.py:227 — widened the catch to (ValidationError, YAMLError, ValueError)
           and named the file in the header — evidence: test_a_yaml_syntax_error_exits_cleanly and
           test_a_non_mapping_config_root_exits_cleanly failed before (raw exception escaped), pass after
         config.py:2568 — removed the duplicate ERROR log (CLI is the single reporter) — evidence:
           test_load_config_leaves_reporting_to_the_cli; real run 2 -> 1 occurrence
         config.py:1048 — extended the critic-name migration to discovery.backlog.sort_order and
           pending_order — evidence: test_a_block_shaped_metascore_is_rewritten_by_the_migration (red)
         config.py:1783 — extraction note closed as no defect (CRAP remediation from the tech-debt step)

Round 2: 0 high, 2 medium, 7 low — 4 new, 5 fix-induced
  fix-induced: config.py:1066 — pending_order rewrite sat after the overrides guard, so a file with no
               platform block kept the legacy name forever — evidence: test_a_pending_order_metascore_is
               _rewritten_without_platform_overrides (red); live run rewrites the file to criticscore
  fix-induced: cli.py:252 — path/permission failures printed the "fix the keys" trailer — evidence:
               test_an_unreadable_config_exits_cleanly (red)
  fix-induced: config.py:2595 — no-op `try/except ValidationError: raise` — deleted
  fix-induced: config.py:2545 — `_first_validation_error` production-dead — deleted with its keeper test
  fix-induced: cli.py:98 — `_config_file_for` re-implemented the loader's filename rule — now reuses
               `_config_file_path`
  new: cli.py:248 — every failing value was echoed, so a mistyped apprise_urls printed its token —
       evidence: test_a_secret_value_is_hidden_in_the_report (red); live run 0 occurrences
  new: config.py:191 — `score` message was false for pending_order — reworded
  new: README.md:559 — over-specified the report format — now states parser position / filesystem
       error / `<hidden>` masking
  new (pre-existing, same failure class): config.py:2455 — `_leaf_items` sorted mixed-type keys and
       raised TypeError before validation — now sorts by `str(key)`; evidence:
       test_mixed_type_keys_do_not_crash_the_inert_key_report (red)

Round 3: 0 high, 1 medium, 5 low — 0 new, 6 fix-induced   [stopping round: plateau, user decision]
  fix-induced: test_cli.py — chmod-based denial replaced with an injected PermissionError
  fix-induced: config.py — duplicated comment removed, deepcopy rationale restored
  fix-induced: cli.py — multi-line messages split into one list item per line
  fix-induced: cli.py — class-specific header for a read/parse failure
  fix-induced: cli.py — reporting extracted; `cli` CRAP 9.08 -> 7.10
  fix-induced: test_config.py — on-disk assertion for the pending_order rewrite
```

## Verification gap

- The round-3 fixes above were applied after the last reviewer pass and were never re-reviewed.
- Every other round-1 and round-2 fix was verified by both reviewers in the following round.
- Final tree at the stop: `uv run pytest -q` → 1364 passed; `ruff check`, `ruff format --check`,
  `mypy` clean; `pytest --cov --crap --crap-threshold=9` → 0 functions flagged (max 8.81);
  coverage unchanged at 99%.
- Growth: the review fixes added 347 lines to an original 408-line diff, inside the growth budget, and
  no file outside the original scope set was touched.

## Residual risk

The remaining findings are test-isolation and presentation hygiene; the substance of the change (clean
reported exit for every config failure class, credential masking, legacy `metascore` accepted and
rewritten) is covered by tests that were observed failing first. The open class the plateau stopped
short of re-reviewing is the report's formatting and the migration's on-disk proof — no known
behavioural defect is left unrecorded.
