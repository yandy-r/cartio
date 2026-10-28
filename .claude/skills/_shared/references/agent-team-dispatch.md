# Agent Team Dispatch — Canonical Lifecycle Reference

## Contents

1. Team Name Sanitization
2. Lifecycle (6 steps)
3. Spawn Rule
4. Failure Policy
5. Dry-Run Semantics
6. Multi-Batch Runs
7. Worktree-aware team dispatch
8. Skill Adapters: Planning and Batched Executors

Used by `plan`, `plan-workflow`, `prp-plan`, `prp-implement`,
`implement-plan`, `orchestrate` and `deep-research`
when the `--team` flag is passed. This file documents the universal implicit team →
TaskCreate → Agent(name=...) → TaskList → SendMessage lifecycle. Individual skills own their
teammate roster and prompt templates; only the mechanism lives here.

> **`--team` is opt-in.** It requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` to be set
> in the user's Claude Code environment. Skills that reference this document validate
> that at flag-parse time and abort with guidance to use `--parallel` instead when the
> flag isn't set.
>
> **Implicit-team model.** With `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, every session
> already has exactly one team. There are no team-creation or team-deletion tools: spawn
> teammates directly with the `Agent` tool's `name` parameter. `team_name` is accepted but
> ignored, so skills never pass it. Team labels such as `pw-<feature>` are display-only run
> labels for dry-run output and prompts. Task tracking uses `TaskCreate`, `TaskGet`,
> `TaskList`, and `TaskUpdate`; teammate communication uses `SendMessage`.

---

## 1. Team Name Sanitization

Run labels follow the pattern: `<skill-prefix>-<sanitized-context>`.

**Sanitization rules for the `<sanitized-context>` portion**:

1. Lowercase all characters.
2. Replace any character matching `[^a-z0-9-]` with `-`.
3. Collapse runs of `-` to a single `-`.
4. Trim leading and trailing `-`.
5. **Truncate to a maximum of 20 characters** for the `<sanitized-context>` portion
   (the prefix is excluded from this cap).

**Skill prefixes** (do not change):

| Skill                | Prefix  | Example                 |
| -------------------- | ------- | ----------------------- |
| `plan`           | `plan-` | `plan-add-rate-limit`   |
| `prp-plan`       | `prpp-` | `prpp-billing-webhooks` |
| `prp-implement`  | `prpi-` | `prpi-user-auth-flow`   |
| `implement-plan` | `impl-` | `impl-user-auth-flow`   |
| `deep-research`  | `drpr-` | `drpr-ai-deployment`    |
| `plan-workflow`  | `pw-`   | `pw-user-auth-flow`     |
| `orchestrate`    | `orch-` | `orch-user-auth-flow`   |

If the sanitized context would be empty (e.g., only symbols), fall back to
`untitled`.

---

## 2. Lifecycle (6 steps)

Every `--team` run MUST perform these steps in order. Deviation corrupts the
shared task list or leaves orphaned teammates.

### Step 1 — Confirm the implicit team

No setup call exists. Verify `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` at flag-parse time
(see the prerequisite above) and choose the display-only run label
`<prefix>-<sanitized-context>`. Record a one-line purpose naming the skill and goal (e.g.,
`"Multi-perspective planning team for: add rate limiting"`) for the teammate prompts. If
the flag is absent, **abort the skill** with a clear error. Do NOT silently fall back to a
non-team mode.

### Step 2 — Register all tasks up front

Register every subtask before spawning any teammates. Wire dependencies via
`addBlockedBy` so the shared task list reflects the full graph:

```
TaskCreate: subject="<task-id>: <short description>", description="<full spec>"
TaskUpdate: taskId="<task-2-id>", addBlockedBy=["<task-1-id>"]
```

Up-front registration is important for multi-batch runs (e.g., `prp-implement`): it
preserves dependency state across batches and prevents the TaskList from resetting
when teammates from a completed batch are shut down.

For single-batch runs (e.g., `plan`, `prp-plan`), the task graph is flat — still
register all tasks before spawning.

### Step 3 — Agent spawn (single message, multiple calls)

**CRITICAL**: All teammates for a given batch MUST be spawned in **ONE message** with
**MULTIPLE `Agent` tool calls**. Sequential `Agent` calls across messages break the
parallel semantics.

Every teammate `Agent` call MUST include `name=` and MUST NOT pass `team_name`:

```
Agent(
  name        = "<teammate-name>",
  subagent_type = "<subagent-type>",
  description = "<one-line task title>",
  prompt      = "<full task prompt>"
)
```

The `name` must match the `TaskCreate` `subject` prefix so the TaskList maps cleanly.

### Step 4 — Monitor task completion

Use `TaskList` to confirm batch completion before advancing. Teammates mark their
tasks complete via `TaskUpdate`; the orchestrator reads status via `TaskList`. Do
NOT rely on agent return values alone — check the shared task state.

If a teammate messages the orchestrator with an issue, respond via `SendMessage` with
guidance.

### Step 5 — SendMessage (shutdown between batches)

Between batches — and at the end of every run, including aborts — send a
shutdown request to every teammate of the completed batch:

```
SendMessage(to="<teammate-name>", message={type: "shutdown_request"})
```

Wait for all shutdowns to complete before spawning the next batch (multi-batch runs)
or proceeding to Step 6 (single-batch runs).

### Step 6 — Confirm shutdown

After the final batch completes (or if the skill aborts partway), confirm every spawned
teammate acknowledged its shutdown request. There is no team-deletion call; the implicit
team ends with the session. Never leave a teammate running after the skill exits.

---

## 3. Spawn Rule (non-negotiable)

- All teammates in a batch → ONE message → MULTIPLE `Agent` calls.
- Every `Agent` call includes `name=`.
- No standalone sub-agent spawns mixed in.

If an implementation spawns a teammate without `name`, it's a bug. If it passes
`team_name`, it's dead configuration. Fix before continuing.

`Agent` with `name` (this file) and `Agent` (standalone fan-out) are mutually exclusive
primitives — never mix them within the same dispatch. See
`standalone-dispatch.md` in this directory for the `Agent` contract.

---

## 4. Failure Policy

| Failure                        | Response                                                         |
| ------------------------------ | ---------------------------------------------------------------- |
| Experimental flag missing      | Abort skill; report error. Do NOT fall back silently.            |
| `TaskCreate` fails             | Shut down any spawned teammates, then abort.                     |
| Teammate returns error         | Record failure in TaskList; decide per skill (continue / abort). |
| Between-batch validation fails | Shutdown current batch; `failure-recovery` gate (see below).     |
| User aborts mid-run            | `SendMessage(shutdown)` to every active teammate, then stop.     |

Never leave a teammate live after the skill exits.

---

## 5. Dry-Run Semantics

If the skill is invoked with both `--team` and `--dry-run` (where the skill
supports dry-run), print:

```
Team name:      <prefix>-<sanitized-context>
Teammates:      <count>
  - <name-1>   subagent_type=<type>   task=<short>
  - <name-2>   subagent_type=<type>   task=<short>
  ...
Batches:        <count>  (batch <n>: <comma-separated teammate names>)
Dependencies:   <edge-count>  (or "none" for flat graphs)
```

Do **not** call `TaskCreate`, `Agent`, or `SendMessage` in
dry-run mode. The output above is the entire dry-run artifact.

---

## 6. Multi-Batch Runs (relevant to `prp-implement`)

For skills that process multiple batches against a single team:

1. Confirm the implicit team once at the start (Step 1).
2. `TaskCreate` for **every task across all batches** up front, with `addBlockedBy`
   wiring dependencies.
3. For each batch in order:
   - Spawn teammates for the batch's tasks (single message, multiple `Agent` calls).
   - Wait for `TaskList` to show all batch tasks complete.
   - Run any between-batch validation the skill requires.
   - `SendMessage(shutdown)` to all teammates of the completed batch.
4. Confirm every teammate has shut down once at the end.

This is cheaper than recreating a team per batch and preserves dependency state
across batches. If validation fails between batches, the user may abort or switch
to a non-team mode — in both cases, `SendMessage(shutdown)` to every active teammate first.

---

## 7. Worktree-aware team dispatch

Worktree mode is **on by default** for the 9 worktree-aware skills; pass `--no-worktree`
to opt out. The legacy `--worktree` flag is accepted as a silent no-op (it matches the
new default). When worktree mode is active (default), all teammates — parallel and
sequential — share **one** feature worktree: `<repo-root>/.claude/worktrees/<repo>-<feature>/`
(see `worktree-strategy.md` in this directory). Parallel safety comes from
per-skill batching and prompts (e.g. different files, or exclusive sections), not from
separate per-task worktrees. The `setup-worktree.sh child` + `merge-children.sh` fan-in
**sequence is deprecated**; do not add it in new team flows.

**One-time (before the first `Agent` spawn, when the feature worktree is used):** ensure
the feature worktree exists, typically via:

`setup-worktree.sh parent <repo> <feature-slug>` (optional `--base-ref` for an existing
branch — e.g. PR review).

### 7.1 Per-batch ordering

Exact ordering within ONE batch (supersedes the old “create child / merge-children” flow):

1. `TaskCreate` for all tasks in the batch (as in §2.2) — _if_ the team graph registers batch tasks the same way as non-team mode; otherwise follow the skill’s own registration rules.
2. `Agent` spawn — one message, multiple `Agent` calls (per §2.3). Each call includes
   `name=`, and the **same** `Working directory: <feature-worktree-path>` in the prompt (or equivalent) so all parallel teammates operate in the one tree. Do
   **not** add `isolation: "worktree"` for this contract; that creates
   separate harness worktrees per teammate.
3. `TaskList` monitor → `SendMessage(shutdown)` to every teammate of the batch (per
   §2.4–2.5).
4. **Between batches:** run whatever validation the skill requires **inside the same**
   feature worktree. **Do not** call `merge-children.sh` for fan-in; that entrypoint is a
   deprecated no-op in the new contract.
5. On validation or manual-merge failure in the worktree, follow the skill’s failure
   policy; do not expect `CONFLICT:` lines from a removed child merge script.

`merge-children.sh` and `setup-worktree.sh child` remain on disk for legacy skill text
and compatibility only.

### 7.2 Sequential vs parallel in one tree

“Sequential” here means the orchestrator does not run multiple `Agent` calls for a step
in one message; it does **not** mean a different worktree. Everyone uses
`<feature-worktree-path>` when worktree mode is on.

### 7.3 Dry-run interaction

`--dry-run` with `--team` (worktree mode is active by default; add `--no-worktree` to
suppress) may add a line to the existing dry-run output (§5), e.g.:

```
Worktree:   feature=<repo-root>/.claude/worktrees/<repo>-<feature>/  (all teammates)
```

No `setup-worktree.sh` or `merge-children.sh` calls are made in dry-run mode.

---

## 8. Skill Adapters: Planning and Batched Executors

Worktree/branch preparation, dependency-batch resolution and plan-execution mechanics are
mode-independent: use standalone-dispatch.md sections 8-10 even for `--team`. This section owns only
what differs when `--team` is passed.

Prerequisites: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, else abort with
`--team requires CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1. Use --parallel instead, or set CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 in your Claude Code settings if you intentionally want agent-team dispatch.`
Cursor/Codex abort: `--team requires team tools, which Cursor/Codex bundles do not ship. Use --parallel instead.`

### 8.1 Planning team adapter (`plan`, `prp-plan`)

Flat roster: register one `TaskCreate` per teammate (no `addBlockedBy`), one `Agent` message with
`name=`, `TaskList` until complete, merge, `SendMessage` shutdown to every spawned
teammate, confirm shutdown always (even on abort/partial failure). Prompt per teammate: role focus,
verbatim request/context, coordination via `SendMessage` with the roster for overlapping scope,
output directives (`standalone-dispatch.md` sections 8.4 and 9.2). Teams are deleted before the
plan is relayed; any re-dispatch spawns fresh teammates (same names fine) and never messages the old ones.
Critical teammate failure: abort and shut down the rest.

Plan adapter exact registration subjects (description: `Multi-perspective planning team for: <user request>`):

```
TaskCreate: subject="architect: structural plan for <user request>", description="..."
TaskCreate: subject="risk-analyst: risks and edge cases for <user request>", description="..."
TaskCreate: subject="test-strategist: testing strategy for <user request>", description="..."
# When ENHANCED_MODE=true, also:
TaskCreate: subject="security-reviewer: threat model for <user request>", description="..."
TaskCreate: subject="ux-reviewer: user-facing impact for <user request>", description="..."
```

### 8.2 Batched executor adapter (`implement-plan`, `prp-implement`, `orchestrate`)

1. Confirm the implicit team once; carry a purpose naming the skill and feature in each prompt.
2. `TaskCreate` for every task across all batches with subject `<task-id>: <title>` and the full
   spec; then `TaskUpdate ... addBlockedBy=[...]` for each dependency (section 6). Any failure:
   shut down any spawned teammates, then abort.
3. Per batch: identify tasks whose dependencies are done; single-message `Agent` calls with
   `name` equal to task ID, `subagent_type`, description, task-template prompt that
   requires reading listed files first, self-validation and `TaskUpdate` completion, plus the
   section 7 / standalone section 8.3 working-directory lines.
4. `TaskList` until batch is complete; answer teammate issues via `SendMessage`.
5. Skill's between-batch validation (e.g. `prp-implement` Levels 1+2) inside the execution tree.
6. `SendMessage(to="<task-id>", message={type:"shutdown_request"})` to every teammate of the
   batch; wait before next batch. Print the skill's `[done] Batch BN: ...` marker.
7. Confirm every teammate has shut down after the last batch or on abort.

Failure: no auto-retry, no skipping. This is the `failure-recovery` gate in
`.claude/skills/_shared/references/question-policy.md`. Ask the user via
`AskUserQuestion`: fix manually and resume, switch to sequential/standalone for remaining
batches, or abort. Headless (no question tool or chat turn): stop, shut down active teammates, and print the
failing batch plus the resume command. Switch/abort: shutdown active
teammates. Resume: shut down current batch teammates and continue. The feature
worktree survives abort for manual inspection/cleanup.

### 8.3 Dry-run for executors

Print run label, `Total tasks: <N> (across <M> batches, max parallel width <X>)`,
`Dependencies: <K edges>`, per-batch task IDs (`(depends on Batch N-1)`), and the per-batch
teammate roster (`<task-id> subagent_type=implementor task=<short>`). Call no team, task,
agent or `SendMessage` tools; stop.
