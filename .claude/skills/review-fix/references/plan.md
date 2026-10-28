# Fix Plan (review-fix Phase 3 / Phase 4 lead-in)

## Contents

- Batching rules
- Batch construction algorithm
- Display the plan
- Worktree-mode batching override
- Phase 4 lead-in
- Worktree-mode lifecycle

## Batching rules

1. **Findings in the same file go in the same group.** Concurrent edits to one file cause write conflicts, so same-file findings are ALWAYS processed sequentially within a single `review-fixer` agent.
2. **Findings in different files are parallel candidates.** They can go in the same batch and be dispatched concurrently.
3. **Within a same-file group, sort by line number DESCENDING.** This ensures earlier edits don't shift the line numbers of later findings.
4. **Severity ordering across batches**: process CRITICAL findings first, then HIGH, then MEDIUM, then LOW. This gives the user the highest-value fixes immediately and ensures that if the pipeline stops mid-run, the worst issues are addressed first.

## Batch construction algorithm

```
1. Group eligible findings by file → same_file_groups[]
2. Sort same_file_groups by max severity of the group (CRITICAL first)
3. For each severity level (CRITICAL, HIGH, MEDIUM, LOW):
     - Collect all same_file_groups whose MAX severity matches this level
     - If those groups are all for different files, they become one batch (parallel-eligible)
     - Otherwise split into multiple batches (unlikely since groups are keyed by file)
4. Within each group, sort findings by line number DESCENDING
```

## Display the plan

```
Fix Plan:
  Eligible findings:  M
  Same-file groups:   G
  Batches:            B
  Max parallel width: W  (largest batch size)

Batch 1 (CRITICAL, 3 fixes, 3 files):
  - F001 (src/auth.ts:42)     SQL injection in user lookup
  - F002 (src/api.ts:17)      Missing null check
  - F003 (src/db/query.ts:8)  Unescaped input

Batch 2 (HIGH, 2 fixes, 1 file with 2 findings):
  - src/utils/format.ts:
      - F005 (line 112) Function exceeds 50 lines
      - F004 (line 78)  Missing error handling

...
```

## Worktree-mode batching override (`WORKTREE_ACTIVE=true`)

When `WORKTREE_ACTIVE=true`, override the default same-file batching with severity-keyed batching:

- **Batch N = all eligible findings at one severity level.**
- Batch order: CRITICAL (Batch 1) → HIGH (Batch 2) → MEDIUM (Batch 3) → LOW (Batch 4), stopping at `MIN_SEVERITY`. Severities with no eligible findings are skipped.
- Within a severity batch, preserve the existing same-file grouping (findings in the same file stay together, sorted by line DESCENDING). Different files within the batch can still parallelize under Path B/C.
  Example plan summary when `WORKTREE_ACTIVE=true`:

```
Worktree-mode plan (4 severity batches, 7 findings, 5 files):

Batch 1 — CRITICAL (2 fixes, 2 files)
  - F001 (src/auth.ts:42)
  - F002 (src/api.ts:17)

Batch 2 — HIGH (3 fixes, 2 files)
  - F003 (src/db/query.ts:112)
  - F004 (src/db/query.ts:87)     ← same file as F003, descending line order
  - F005 (src/utils/format.ts:9)

Batch 3 — MEDIUM (1 fix, 1 file)
  - F006 (src/logging.ts:45)

(LOW batch skipped — 0 eligible findings or below MIN_SEVERITY)
```

When `--dry-run` is combined with `--worktree`, print this summary and exit. All batches would run inside the parent worktree at `$PARENT_PATH`.

## Phase 4 lead-in

Select the execution path from the flags: neither flag → **Path A** — sequential execution (default); `PARALLEL_MODE` → **Path B** — parallel standalone sub-agent batches; `AGENT_TEAM_MODE` → **Path C** — agent-team batch execution.

## Worktree-mode lifecycle (`WORKTREE_ACTIVE=true`)

When worktree mode is active, every batch — regardless of Path A / B / C — runs
inside the single feature worktree from
`.claude/skills/_shared/references/worktree-strategy.md` §1. There are
no per-severity child worktrees and no fan-in merge.

**One-time parent setup** (only when `WORKTREE_MODE=true` was forced and
`$PARENT_PATH` needs creation; skip if already ensured by `/code-review`):

```bash
PARENT_PATH=$(bash ".claude/skills/_shared/scripts/setup-worktree.sh" \
  parent "${REPO}" "${SLUG}" --base-ref "${PARENT_BRANCH}")
WORKTREE_ACTIVE=true
```

**Per-batch steps** (repeat for each severity batch in order):

1. **Dispatch** review-fixer agents with `Working directory: $PARENT_PATH` appended to
   their prompt, plus the coordination note: "Other agents in this batch are working
   in the same directory — do not run destructive git commands (reset, clean, checkout)."
   Do **not** pass `isolation: "worktree"` here; that creates a separate harness worktree per review-fixer and breaks the single-worktree contract.

2. **Validate** inside `$PARENT_PATH` after the batch completes:

   ```bash
   ( cd "$PARENT_PATH" && $TYPECHECK_CMD ) && ( cd "$PARENT_PATH" && $TEST_CMD )
   ```

3. **Advance** to the next severity batch. No merge step. No child worktree removal.

**Status updates**: Edit the `Status:` field in the review artifact file in place
(Open → Fixed / Failed) after each agent completes its finding. The artifact lives
in the active worktree at `$PARENT_PATH/docs/prps/reviews/pr-<N>-review.md`.
