# Fixer Dispatch

## Contents

- Fixer prompt (all paths)
- Path A — sequential
- Path B — parallel standalone batches
- Handling failures
- Path C — agent team batches

## Fixer prompt (all paths)

Use `review-fixer` with its Finding spec: Shape A for one finding, Shape B
for a same-file group sorted by line descending. Include the full ID, severity,
file, line, category, description, and unchanged Suggested fix, plus:

```
SOURCE REVIEW FILE: <absolute artifact path>
PROJECT TYPE-CHECK COMMAND: <detected command or explicit SKIP (...)>
```

Agents edit only their assigned source files; orchestrator alone updates source
artifact statuses. When worktree active, append `Working directory: $PARENT_PATH`
and coordination note: other agents share this directory; do not run destructive
git commands (`reset`, `clean`, `checkout`). Do not pass `isolation: "worktree"`.

### Path A — Sequential Execution (default)

Process batches in order; within a batch, one finding or same-file group at a
time. For each: dispatch a single `review-fixer` (Shape A or B), wait for
its report, then `Edit` the finding's `**Status**: Open` line to `Fixed` on
`STATUS: Fixed` or `Failed` on `STATUS: Failed`. Track `[done] F042 — Fixed` /
`[failed] F043 — Failed: <blocker>` in todos. Continue after a failure so the
user gets the full picture. In worktree mode run the between-batch validation
inside `${PARENT_PATH}` before advancing to the next severity. Path A has no
stop-and-ask gate: between-batch validation is diagnostic here and never
pauses the pipeline; the fail-stop gate applies to Path B and Path C only.

### Path B — Parallel Sub-Agent Execution (`PARALLEL_MODE=true`)

- When `WORKTREE_ACTIVE=true`: batches are still severity-ordered. Dispatch every parallel review-fixer with `Working directory: ${PARENT_PATH}` only. Do **not** add `isolation: "worktree"`. Between-batch validation runs once in `${PARENT_PATH}` before advancing to the next severity. No fan-in merge.

Process batches sequentially; within each batch, dispatch all review-fixer agents as standalone sub-agents in parallel. Dispatch per the contract in `.claude/skills/_shared/references/standalone-dispatch.md`.

For each batch:

1. **Dispatch review-fixer agents in parallel** — Use a **single message** with **multiple `Agent` tool calls**, one per finding or same-file group in the batch:
   - `subagent_type`: `"review-fixer"`
   - `description`: e.g., `"Fix F042: missing null check in payments.ts"`
   - `prompt`: The Finding spec (Shape A or Shape B) plus `SOURCE REVIEW FILE` and `PROJECT TYPE-CHECK COMMAND`

2. **Wait for all agents in the batch to complete** before proceeding.

3. **Collect results and update the review file**:
   - For each `STATUS: Fixed` → `Edit` the review file to update that finding's `**Status**: Open` → `**Status**: Fixed`
   - For each `STATUS: Failed` → `Edit` the review file to update that finding's `**Status**: Open` → `**Status**: Failed`

4. **Between-batch validation (Levels 1 + 2)** — After each batch (except the last), run:

   ```bash
   $TYPECHECK_CMD
   $TEST_CMD
   ```

   - If both pass: log `[done] Batch N: K fixes — validation pass` and proceed.
   - If either fails: **stop** the parallel pipeline. Report which batch broke validation. Use `AskUserQuestion` to ask:
     - "Resume sequentially from next batch"
     - "Abort and leave current state as-is"
     - "Skip remaining findings and jump to Phase 5 (verify + report)"

5. **Track progress** in todos per batch, not per finding.

### Handling failures

If a `review-fixer` agent returns `STATUS: Failed`:

- **Do not retry** the same fix automatically. The agent already judged the Suggested fix to be incompatible.
- Mark the finding as `Failed` in the review file.
- Include the agent's `BLOCKER` and `RECOMMENDATION` in the fix report.
- Continue with remaining findings.

### Path C — Agent Team Execution (`AGENT_TEAM_MODE=true`, Claude Code only)

- When `WORKTREE_ACTIVE=true`: batches are still severity-ordered. Dispatch teammates with `Working directory: ${PARENT_PATH}` only. Do **not** add `isolation: "worktree"`. Before advancing to the next severity batch, send `SendMessage(shutdown)` to all current batch teammates, then run between-batch validation inside `${PARENT_PATH}`. No fan-in merge.

> **Agent team lifecycle**
>
> In Path C you must follow the agent-team lifecycle. Do not mix standalone sub-agents
> with team dispatch. Every `Agent` call below must include `name=`.
>
> 1. Use the implicit session team across all batches
> 2. `TaskCreate` for **every eligible finding (or same-file group) across all batches**
>    up front. Flat graph — no `addBlockedBy` wiring, because batch ordering is
>    orchestrator-controlled, not task-graph-controlled. Each batch is processed only
>    after the previous one's teammates are shut down.
> 3. Per batch: spawn teammates (single message, multiple `Agent` calls with
>    `name=`)
> 4. `TaskList` to confirm batch completion; run between-batch validation
> 5. `SendMessage({type:"shutdown_request"})` to all teammates of completed batch
>    before spawning next batch
> 6. Send shutdown requests to every teammate after the final batch (or on abort)
>
> If up-front `TaskCreate` fails, abort the skill. Refer to
> `.claude/skills/_shared/references/agent-team-dispatch.md`
> for the full lifecycle contract.

Process batches sequentially under a single team, with per-batch teammate spawn and inter-batch shutdown. Use this when the fix run spans many findings across multiple batches and you want a shared task graph for audit/visibility, or when fixers should be able to cross-reference each other via `SendMessage` (e.g., if fixing F003 reveals the same root cause as F007 in a different file).

#### C.1 Build the team name

Derive `<sanitized-review-name>` from the source review file basename:

- Strip the `.md` extension.
- Lowercase; replace non-`[a-z0-9-]` with `-`; collapse runs of `-`; trim; truncate to **20 characters** max.
- Fall back to `untitled` if empty.

Team name: `rfix-<sanitized-review-name>`.

Example: `docs/prps/reviews/pr-42-review.md` → `rfix-pr-42-review`.
Example: `docs/prps/reviews/local-20260408-143022-review.md` → `rfix-local-20260408-1` (truncated to 20 chars).

#### C.2 Dry-run gate (if `DRY_RUN=true`)

The Phase 3 plan has already been printed. Additionally print:

```
Team name:    rfix-<sanitized-review-name>
Total tasks:  <M>  (eligible findings, across <B> batches, max parallel width <W>)
Dependencies: none  (batches are orchestrator-controlled; task graph is flat)

Per-batch teammate roster:
  Batch 1 (<severity>, <count> fixes):
    - <task-id-1>  subagent_type=review-fixer  finding=<F###: short>
    - <task-id-2>  subagent_type=review-fixer  finding=<F###: short>
  Batch 2 (...):
    ...
```

Do **not** call any team/task/agent tools. Exit the skill.

#### C.3 Create the team

The team is implicit; use `rfix-<sanitized-review-name>` as the display-only run label.

On failure, abort.

#### C.4 Register all eligible tasks up front (flat graph)

For **every eligible finding or same-file group across all batches** (from the Phase 3 plan):

```
TaskCreate: subject="<task-id>: fix <F###> in <file>", description="<full Finding spec — Shape A for single finding, Shape B for same-file group — plus SOURCE REVIEW FILE and PROJECT TYPE-CHECK COMMAND>"
```

Use stable task IDs derived from the primary finding ID, e.g.:

- Single finding `F042` in `src/api.ts` → task id `f042`
- Same-file group `F004, F005` in `src/utils/fmt.ts` → task id `f004-f005`

**No `addBlockedBy` wiring.** Batch ordering is enforced by the orchestrator (per-batch spawn + shutdown), not by the shared task graph. The shared task list's role in Path C is observability and inter-fixer communication, not dependency resolution.

If any `TaskCreate` fails, shut down any active teammates and abort.

#### C.5 Per-batch loop

For each batch `B1, B2, ... BN` in order (from the Phase 3 plan):

1. **Identify batch tasks** — Extract all task IDs for findings (or same-file groups) in this batch.

2. **Spawn batch teammates** — Single message, multiple `Agent` tool calls, one per finding or same-file group in the batch. Every call must include:
   - `name`: the task ID (e.g., `"f042"`, `"f004-f005"`) — must match the `TaskCreate` subject prefix
   - `subagent_type`: `"review-fixer"`
   - `description`: One-line fix title (e.g., `"Fix F042: missing null check in api.ts"`)
   - `prompt`: The Finding spec (Shape A or Shape B) plus `SOURCE REVIEW FILE`, `PROJECT TYPE-CHECK COMMAND`, and a directive that the teammate shares a task list with sibling fixers (list their task IDs) and may `SendMessage` them if a related finding becomes relevant, and must call `TaskUpdate` to mark its task complete before returning.

3. **Wait for batch completion via `TaskList`** — poll until all tasks in this batch are `completed`. If a teammate messages with an issue, respond via `SendMessage` with guidance.

4. **Collect results and update the review file** — For each completed task, the teammate returns `STATUS: Fixed` or `STATUS: Failed`:
   - For each `STATUS: Fixed` → `Edit` the review file to update that finding's `**Status**: Open` → `**Status**: Fixed`
   - For each `STATUS: Failed` → `Edit` the review file to update that finding's `**Status**: Open` → `**Status**: Failed`

5. **Between-batch validation (Levels 1 + 2)** — After each batch (except the last), run:

   ```bash
   $TYPECHECK_CMD
   $TEST_CMD
   ```

   - If both pass: log `[done] Batch N: K fixes — validation pass` and proceed.
   - If either fails: **stop** the pipeline. Use `AskUserQuestion`:
     - "Resume sequentially from next batch" — shut down the active teammates, continue with Path A logic for remaining batches.
     - "Switch to parallel sub-agents for remaining batches" — shut down the active teammates, continue with Path B for remaining batches.
     - "Abort and leave current state as-is" — shut down the active teammates, exit.
     - "Skip remaining findings and jump to Phase 5 (verify + report)" — shut down the active teammates, jump to Phase 5.

6. **Shut down completed-batch teammates** — Send to every teammate of the just-completed batch:

   ```
   SendMessage(to="<task-id>", message={type:"shutdown_request"})
   ```

   Wait for shutdowns to complete before spawning the next batch's teammates.

7. **Track progress** in todos per batch, not per finding.

#### C.6 After all batches complete

Confirm all teammates have shut down. Then proceed to **Phase 5 — VERIFY** as normal.

#### Path C failure handling

Same principles as Path B: do NOT auto-retry failed fixes, do NOT skip a failed batch. Always shut down teammates before exiting, regardless of success or failure. Review file updates happen incrementally after each batch returns, so an aborted Path C run leaves the source review file reflecting the last completed batch's state.
