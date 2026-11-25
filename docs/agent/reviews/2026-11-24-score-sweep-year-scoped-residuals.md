# Year-scoped score sweep — review residuals (cap round)

Branch: `main` · HEAD at review start: `fcf3d56` (uncommitted change set) · Round reached: **10** of
cap **10** · Reviewers: `opencode-go/deepseek-v4.1-flash:max` (A) and `opencode-go/glm-5.3-flash:max` (B).

The cap round reported findings, so the loop ended here rather than on a severity-clean round. Both
reviewers verified every fix from rounds 1-9 held; the rows below are what round 10 itself raised.
Fixes were applied after the last reviewer pass and were **not** independently re-reviewed.

| Severity | Location | Description | Status |
| --- | --- | --- | --- |
| MEDIUM | `src/gamarr/config.py` `_migrate_score_page_depth` (no-reach branch) | The round-9 no-bound default wrote `oldest_month` before the flat-key path, so a mixed-shape file's own `max_pages` was refused by `_place` and the invented ~16-month floor was persisted (with a log line blaming a value the migration had just written). Found by both reviewers. | `applied, not independently re-reviewed` - the branch now skips a platform that still carries flat legacy keys, so its budget converts first (`test_a_flat_budget_beats_the_default_floor_in_a_mixed_shape`) |
| MEDIUM | `src/gamarr/config.py` `_migrate_score_page_depth` (unusable-value branch) | An unreadable in-block `page_depth` (`"5OO"`, `null`, `true`, `-3`) was dropped and left the sweep floorless, while an absent one kept the default reach. Found by both reviewers. | `applied, not independently re-reviewed` - it now warns and falls back to `_default_floor()`; `test_an_unusable_page_depth_is_dropped_without_aborting_the_load` asserts the reach |
| MEDIUM | `src/gamarr/config.py` `_migrate_score_page_depth` (absent/null block) | A block-shaped file that selects a score sort but has no `discovery.backlog.score` block (or a YAML null) reaches no migration arm, so it validates floorless and widens from the old 500-page default to the whole catalogue. | `not fixed` - writing a score block into every such file would add an unread block (and the inert-key line) for `new`-sorted and `latest` files, and cannot be distinguished from a hand-written new-style config. A released generated file always carries `page_depth`, so only a hand-edited block reaches this shape. |
| LOW | `docs/agent/decisions/0007-page-counts-convert-to-months.md` | The no-bound paragraph over-claimed that every fallback was covered (it did not name the unusable-value case). | `applied, not independently re-reviewed` - the sentence now names the unreadable-value fallback and the flat-key exemption |

## Verification gap

The cap round's fixes were never re-reviewed by either model. A later run may take these rows on
(`review-triage` names the ledgers it accepts and stamps them `Triage: PLAY`); until then this ledger
is the durable record that the change closes **unresolved, not clean**.

Reviewer notes worth carrying, all outside the change and not fixed here: `docs/agent/plans/` and the
older specs still quote pre-change page figures (point-in-time records), and `src/gamarr.egg-info/`
is stale generated build output that the repo's global ignore rules cover.
