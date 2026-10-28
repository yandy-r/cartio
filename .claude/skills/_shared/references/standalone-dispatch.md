# Standalone Dispatch — Agent Tool Reference

## Contents

1. What `Agent` Is
2. Spawn Rule
3. Result Delivery and Batch Completion
4. Hard Anti-Patterns
5. Disk-Artifact Backstop
6. Failure Policy
7. Cross-Target Portability
8. Worktree and Branch Preparation
9. Dependency Resolution and Batching
10. Executing a Confirmed Plan

Used by `prp-plan`, `deep-research`, `prp-implement`, `review-fix`,
`quick-fix`, `orchestrate`, `feature-research`, `parallel-plan`,
`shared-context`, `plan-workflow`, `clean`, and `git-workflow` for
**standalone** sub-agent fan-out (no shared team, no `--team` flag). This file documents
the `Agent`-tool spawn/return contract. See `agent-team-dispatch.md` in this directory
for the separate `Agent` with `name` lifecycle used by `--team`/Path C runs. Sections 8-10
hold the dispatch-mode-independent worktree, dependency-batching and plan-execution
mechanics that `--team` runs reuse; only dispatch arguments and coordination differ.

---

## 1. What `Agent` Is

`Agent` dispatches standalone sub-agents or named teammates. For standalone work,
omit `name` and `run_in_background`; identify the role through `description` and
`prompt`. This requests standalone dispatch, not a blocking-return guarantee.
The runtime may still deliver results asynchronously, including in headless sessions.

Named teammates (`Agent` with `name`) require the implicit-team lifecycle in
`agent-team-dispatch.md`. Do not mix teammate and standalone dispatch in one batch.
Compatibility generators must project these instructions onto their target's tools.

---

## 2. Spawn Rule (non-negotiable)

Spawn independent standalone sub-agents in **ONE message** with **MULTIPLE
`Agent` tool calls**:

```
Agent(subagent_type="<type>", description="<role and short title>", prompt="<full task prompt>")
Agent(subagent_type="<type>", description="<role and short title>", prompt="<full task prompt>")
```

- Omit `name` (reserved for teammates) and `run_in_background`. Also omit
  `team_name`, which is accepted but ignored.
- Track the orchestrator's own progress with `TaskCreate`, `TaskGet`, `TaskList`,
  and `TaskUpdate` when needed. These tools neither dispatch agents nor retrieve results.
- Keep every dispatched agent associated with its role and expected deliverable.
- Keep agent guidance self-contained: inline any skill/shared reference content needed by the agent in its dispatch prompt; standalone agent installs do not ship those files.

---

## 3. Result Delivery and Batch Completion

Results may arrive as tool return values or asynchronously through task notifications.
Wait for **all** dispatched agents' results before merging or starting the next batch.
If results are pending and the runtime offers a wait tool, call it until every dispatched
job has settled; otherwise end the turn so completion notifications can arrive. Never
finish the run while a dispatched job is still running, and do not busy-wait with
`sleep`, `echo`, or polling. Resume collection when notified.

Validate each completed agent's report and required artifacts before marking its task
complete. A launch acknowledgement or task status alone is not a findings report.
Only treat a result as missing after the agent has completed or failed, not merely
because its dispatch returned without findings.

---

## 4. Hard Anti-Patterns

- Do not pass `name` or `run_in_background` for standalone dispatch.
- Do not proceed with a partial batch while other results are pending.
- Do not use `SendMessage` for standalone result retrieval; it is teammate messaging.
- Do not fabricate findings for a completed agent with an empty or malformed report.
  Apply the backstop and failure policies below after completion.

---

## 5. Disk-Artifact Backstop

Content-producing sub-agents with `Write` (for example, report-writing researchers)
should also write findings to a declared artifact path and return the same content. Read-only reviewers, advisors, and planners never write artifacts: the
orchestrator persists each valid delivered report, and an empty/missing return is a
failed review, not evidence to reconstruct.

For agents allowed to write a backstop artifact:

1. After the agent completes, the orchestrator validates its delivered report first.
2. If the delivered report is empty or missing, re-read the backstop artifact from disk
   before treating the sub-agent as failed.
3. Only if **both** the delivered report and the backstop artifact are empty/missing is the
   sub-agent considered to have failed (see §6).

For **code-writing** sub-agents (e.g. `implementor`, `review-fixer`), there is no
separate findings file — the "artifact" is the code changes already committed to disk,
plus a final `STATUS:` line in the delivered report so the `Agent` result stays
parseable (e.g. `STATUS: Fixed` / `STATUS: Failed: <reason>`).

---

## 6. Failure Policy

| Failure                                                                                  | Response                                                                                                                                   |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Single sub-agent: empty/missing delivered report **and** empty/missing backstop artifact | Record a gap. If non-critical to the overall task, continue without it. Otherwise, re-dispatch once with the same prompt before giving up. |
| Majority of a batch fails (empty/missing delivered report **and** backstop artifact)     | Abort the skill run and report to the user, with guidance to retry. Do not attempt to synthesize a result from a majority-failed batch.    |
| Re-dispatch also fails                                                                   | Treat as a true failure per the non-critical/abort branches above — do not re-dispatch more than once with an identical prompt.            |

Never silently drop a failed sub-agent's slot from downstream synthesis without recording
the gap — a quietly missing finding is worse than a reported one.

---

## 7. Cross-Target Portability

This file is the Claude-facing source contract. Cursor, Codex, opencode, and OMP generators
must project it onto the target's actual tools and lifecycle rather than copying Claude
identifiers or return assumptions. `Agent` with `name` remains Claude-Code-only. Per the
`AGENTS:cursor` note in `target-capability-matrix.md` in this directory, those
generated targets have no Claude teammate-messaging surface (`SendMessage`); their standalone path must use the target-native dispatch
primitive, and their generated `--team` path must fail closed when no equivalent exists.
OMP dispatches through one `task` batch, ships no team tools, and fails `--team` closed.

---

## 8. Worktree and Branch Preparation

Single source for plan, plan-workflow, prp-plan (annotation directive) and the executors
`implement-plan`, `prp-implement`, `orchestrate`. Applies to standalone and `--team` runs.

### 8.1 How worktree mode is decided

1. `--no-worktree` present: `WORKTREE_MODE=false`. Forced off regardless of plan annotations;
   no feature worktree; work on the prepared `feat/<slug>` branch in the current checkout.
2. Plan contains `## Worktree Setup`: `WORKTREE_MODE=true`. Annotations are the source of truth.
3. Neither: `WORKTREE_MODE=true` (default). Derive the single path below.

`--worktree` is a silent legacy no-op; combining it with `--no-worktree` is an error where the
skill says so. When the plan has `## Worktree Setup`, read `WT_PARENT_PATH` from its
`**Parent**:` line and `WT_FEATURE_SLUG` as the segment after `<repo>-`. Set
`WORKTREE_ACTIVE=true` if annotations exist or `WORKTREE_MODE=true`; false for `--no-worktree`.

Default-on deduction (no annotations):

- `WT_REPO_NAME` = basename of `git rev-parse --show-toplevel`
- `WT_FEATURE_SLUG` per skill: `implement-plan` uses the raw feature-name argument;
  `prp-implement` sanitizes the plan basename (cap 40); `orchestrate` sanitizes the task
  description (cap 20); `plan` confirmed execution uses the team sanitization (cap 20,
  fallback `untitled`). Shared sanitizer: lowercase, `[^a-z0-9-]` to `-`, collapse runs, trim
- `FEATURE_BRANCH` = `feat/${WT_FEATURE_SLUG}`
- `WT_PARENT_PATH` = `<repo-root>/.claude/worktrees/${WT_REPO_NAME}-${WT_FEATURE_SLUG}/`

### 8.2 Prepare (before any implementor dispatch)

Set `REPO_ROOT=$(git rev-parse --show-toplevel)`. Inspect `git status --porcelain`: expected pre-commit plan artifacts may be dirty;
unrelated dirty files: stop and ask user to stash, commit or clean. Then:

```bash
FEATURE_BRANCH=$(bash .claude/skills/_shared/scripts/prepare-feature-branch.sh "${WT_FEATURE_SLUG}")
```

Do not skip it. Narrative-only branch instructions let agents commit to `main`. Contract:
`branch-prep.md`. When `WORKTREE_ACTIVE=true`, once before the first batch (idempotent; adopts
the prepared branch; echoes path, store as `WT_PARENT_PATH`):

```bash
WT_PARENT_PATH=$(bash .claude/skills/_shared/scripts/setup-worktree.sh parent "${WT_REPO_NAME}" "${WT_FEATURE_SLUG}")
```

Then move plan artifacts once (never `cp`, `rsync` or sync; never move back); companions such as
`shared.md` ride as extra arguments. Use `$PLAN_PATH` for every later reference; run later
validation/reports/archive inside `$WT_PARENT_PATH`:

```bash
PLAN_PATH=$(bash .claude/skills/_shared/scripts/move-plan-to-worktree.sh "<plan-path>" "$WT_PARENT_PATH" ["<companion>" ...])
```

`WORKTREE_ACTIVE=false`: skip worktree setup and the move; keep original plan path and run in
the prepared current checkout.

### 8.3 Agent prompt lines

Active worktree, every agent prompt (parallel and sequential):

```
Working directory: ${WT_PARENT_PATH}
All parallel agents in this batch share this path; batching guarantees no two agents touch the same file.
```

`--no-worktree`:

```
Working directory: ${REPO_ROOT}
All parallel agents in this batch share the current feature branch; batching guarantees no two agents touch the same file.
```

Treat that directory as repo root for Read/Write/Edit/Bash. Never pass `isolation: "worktree"` or
`Agent(isolation: "worktree")`; it creates separate harness worktrees. Codex/opencode: prompt line is
sufficient. Cursor: warn and print the manual `git worktree add` command.

### 8.4 Planner annotation directive (default WORKTREE_MODE=true; omit for --no-worktree)

Append to planner and persona prompts:

```
WORKTREE MODE:

The plan consumer will run all tasks in a single shared git worktree.
In your emitted plan:

1. Add a top-level `## Worktree Setup` section BEFORE the Batches summary
   (or before Implementation Steps when there is no Batches section):

   ## Worktree Setup

   - **Parent**: <repo-root>/.claude/worktrees/<repo>-<feature>/ (branch: feat/<feature>)

   Substitute the concrete `<repo>` and `<feature>` values; keep `<repo-root>/.claude/worktrees/`
   as this runtime's worktree root. All tasks - parallel and sequential - share this one path.
   Do NOT add a `**Children**:` list and do NOT add per-task `**Worktree**:` annotations.
```

---

## 9. Dependency Resolution and Batching

### 9.1 Graph and batches

Build the graph from `Depends on [...]` annotations. Tasks with `Depends on [none]` form batch 1.
A task joins the earliest batch whose dependencies are all in prior batches. Two tasks touching the
same file never share a batch. Cross-cutting shared types/config get a dedicated early batch.
Prefer wide-shallow graphs over narrow-deep chains. Dispatch each batch as one message;
wait for the whole batch before the next.

### 9.2 Planner output directives (parallel plans)

When a planner is asked for parallel output, append:

```
PARALLEL OUTPUT MODE:

Shape the output for parallel execution:
1. Number steps hierarchically (1.1, 1.2, 2.1); first digit is batch.
2. Populate Dependencies on every step with explicit IDs (`Depends on [1.1, 1.2]` or `Depends on [none]`).
3. Add `## Batches` after `## Architecture Changes` and before `## Implementation Steps`:

   | Batch | Steps         | Depends On | Parallel Width |
   | ----- | ------------- | ---------- | -------------- |
   | B1    | 1.1, 1.2, 1.3 | -          | 3              |

   - **Total steps**: N
   - **Total batches**: M
   - **Max parallel width**: X
4. Apply the batch construction rules from section 9.1.
```

### 9.3 Between batches and failures

Run the skill's own validation inside the execution tree (worktree path or prepared checkout).
After each batch update todos and print the skill's verbatim `[done] Batch BN: K tasks — complete` marker (em dash; transcript loops match the marker).
Do not auto-retry a failed batch (usually file conflict or hidden dependency) and do not skip it;
later batches may depend on it. Preserve completed work; ask the user (fix/resume, switch to
sequential, abort) per the `failure-recovery` class in `question-policy.md` in this directory.
Standalone partial failure: continue only independent tasks.

---

## 10. Executing a Confirmed Plan

After a user confirms an in-conversation parallel plan: run section 8 (worktree: plan names parent
`<repo-root>/.claude/worktrees/<repo>-<feature>/`; run `setup-worktree.sh parent` once, dispatch with
`Working directory: <parent path>`; `--no-worktree`: derive the slug with the team-name sanitization
rules (lowercase, non `[a-z0-9-]` to `-`, collapse, cap 20, fallback `untitled`) and run
`prepare-feature-branch.sh`). The helper is idempotent, exits 1 on unrelated dirty tree, exits 2 on
a different feature branch (`--allow-existing-feature-branch` after confirmation) or on `release/*`
when `RELEASING.md` exists (`--allow-release-branch` for release-only fixes). Do not skip.
Then process section 9 batches with one `implementor` `Agent` per step in one message; run the
project type-check and unit-test commands between batches; on failure stop and ask the user
(`failure-recovery` class in `question-policy.md`).
