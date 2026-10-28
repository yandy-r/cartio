# Review Dispatch

## Contents

- Standalone dispatch
- Agent team
- Scratch backstop and cleanup

## Standalone dispatch (Path B)

Follow the spawn/return contract in
`.claude/skills/_shared/references/standalone-dispatch.md` for
how reviewers are dispatched and how their results are collected.

Dispatch 3 standalone `code-reviewer` sub-agents in parallel in a single
message with multiple `Agent` calls. Use the mode-appropriate roster
(Local / Quick Mode Roster or PR Mode Roster) and the Standard Findings Format
from `.claude/skills/_shared/references/review-checklist.md`.
That reference defines the roster, severity rubric, findings-format directive
and merge procedure. Prompt contract follows below.

Run-id and scratch paths per mode:

| Mode  | run-id                                                                                                    | scratch dir                                            |
| ----- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| Local | `local-<TIMESTAMP>` (one canonical timestamp per invocation; reused for the Phase 3 review filename)      | `docs/prps/reviews/.review-scratch/local-<TIMESTAMP>/` |
| PR    | `pr-<NUMBER>` (PR numbers are unique; no timestamp)                                                       | `docs/prps/reviews/.review-scratch/pr-<NUMBER>/`       |
| Quick | `quick-<TIMESTAMP>` (reused for the artifact filename if the user later selects an artifact-writing path) | `docs/prps/reviews/.review-scratch/quick-<TIMESTAMP>/` |

Each reviewer prompt includes the instruction "flag only gaps that affect correctness or the stated requirements", plus:

1. The list of changed files (mode-specific: `git diff --name-only HEAD` for
   local/quick; PR number + head revision + changed-file list for PR), plus the
   diff itself and any command output (validation, build, test results) the
   reviewer needs — reviewers are read-only and cannot run commands.
2. Relevant context (CLAUDE.md rules, PRP artifacts, PR description) — PR mode only.
3. Its assigned focus / categories and what to check (from the roster tables
   in `review-checklist.md` — `.claude/skills/_shared/references/review-checklist.md`).
4. The severity rubric.
5. A directive to return findings in the **Standard Findings Format** below.
6. (Path B only) The spawn/return contract loaded by the caller from
   `.claude/skills/_shared/references/standalone-dispatch.md`.
   The reviewer is dispatched read-only (Read/Grep/Glob): it returns its
   findings inline as the `Agent` result and never writes files or runs
   commands. The prompt must supply the diff and any command output the
   reviewer needs.

Wait for all 3 reviewer results before merging: if results are pending and the runtime
offers a wait tool, call it until every reviewer job has settled; never finish the run
while a reviewer is still running. After all 3 `Agent` calls return, the calling skill
(not the reviewer) persists each valid inline return to its scratch copy at
`docs/prps/reviews/.review-scratch/<run-id>/<reviewer-name>.md`, where
`<run-id>` is `local-<YYYYMMDD-HHMMSS>` for local mode, `pr-<NUMBER>` for PR
mode, `quick-<YYYYMMDD-HHMMSS>` for quick mode. An empty, missing, or malformed
return marks that reviewer failed (merge procedure in review-checklist.md); the
scratch copy is not a second recovery source. Pass the merged findings to
the REPORT phase (Phase 3 local / Phase 6 PR) as if they came from a
single-pass review. Scratch cleanup happens after the artifact write; see
below.

Validation commands still run sequentially in the main skill —
parallelization applies only to the review pass.

## Agent team (Path C)

Claude Code only (`--team` requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`;
bundles ship without team tools — use `--parallel` there).

You must follow the agent-team lifecycle. Do not mix standalone sub-agents
with team dispatch. Every `Agent` call must include `name=`.
If up-front `TaskCreate` fails, abort the skill. See
`.claude/skills/_shared/references/agent-team-dispatch.md`
for the full lifecycle contract.

1. Confirm implicit session team is available
2. TaskCreate for all 3 reviewer subtasks up front (flat graph — no dependencies)
3. Spawn 3 teammates: single message, three `Agent` calls with `name=`
4. TaskList to monitor until all reviewers mark complete
5. SendMessage({type:"shutdown_request"}) to all 3 teammates
6. Send shutdown requests to all 3 teammates before merging

Same 3-reviewer roster and category split as Path B, but dispatched as a
coordinated team with a shared task list. Use this when reviewers may surface
overlapping findings and should cross-reference each other via `SendMessage`.

### Team name per mode

| Mode  | Team name                                                                            |
| ----- | ------------------------------------------------------------------------------------ |
| Local | `crev-local-<YYYYMMDD-HHMMSS>` (same timestamp as the artifact filename)             |
| PR    | `crev-pr-<NUMBER>`                                                                   |
| Quick | `qrev-local-<YYYYMMDD-HHMMSS>` (same timestamp as the artifact filename, if written) |

### Register subtasks

Create 3 tasks in the shared task list (flat graph — reviewers are
independent), one per reviewer, with the full reviewer prompt as the
description. Subjects:

- `correctness-reviewer: <scope> review of <target>`
- `security-reviewer: <scope> review of <target>`
- `quality-reviewer: <scope> review of <target>`

If any `TaskCreate` fails, shut down any active teammates and abort.

### Spawn the 3 reviewers

Single message, three `Agent` calls. Every call includes `name`
(the reviewer name — must match the `TaskCreate` subject prefix),
`subagent_type: "code-reviewer"`, a one-line `description`, and the same
`prompt` as Path B plus a note that the teammate shares a task list with two
sibling reviewers (name them), may `SendMessage` them on overlapping scope,
and must call `TaskUpdate` to mark its task complete before returning.

### Monitor, shutdown, merge

Use `TaskList` to confirm all 3 tasks are `completed` before merging. If a
teammate messages the orchestrator, respond via `SendMessage`. Failure policy:

- All 3 error → shut down active teammates and abort with a clear error.
- 1 or 2 error → record "partial review — {role} did not complete", proceed
  with the remaining findings, and note the gap in the artifact Summary (PR)
  or the inline output (local / quick).

After all teammates have marked their tasks complete (or been recorded as
failed), send `SendMessage({type:"shutdown_request"})` to each and
confirm all teammates have shut down (even on abort or partial failure). Then
merge findings exactly as Path B (combine by severity, de-dupe at `file:line`,
sort by file path, attach reviewer source tags) and pass them to the REPORT
phase.

## Scratch backstop and cleanup

- code-review local mode: remove `docs/prps/reviews/.review-scratch/local-<TIMESTAMP>/`
  once the review artifact has been written successfully (Phase 3).
- code-review PR mode: remove `docs/prps/reviews/.review-scratch/pr-<NUMBER>/`
  once the artifact has been written and (when `WORKTREE_ACTIVE=true`)
  committed successfully (Phase 6).
- quick-review: remove `docs/prps/reviews/.review-scratch/quick-<TIMESTAMP>/`
  at the end of Phase 5 (HAND-OFF) unconditionally — including on "Discard"
  and "Apply fixes", which never write a permanent artifact. The scratch file
  is the one ephemeral exception to quick-review's artifact-free guarantee.
