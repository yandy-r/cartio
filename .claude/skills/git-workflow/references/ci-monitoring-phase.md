# Phase 6 — CI Monitoring (Steps 32–37)

## Contents

- Entry conditions
- Step 32: discover the PR
- Step 33: load policy
- Step 34: authorization prompt
- Step 35: audit log
- Step 36: loop iteration
- Step 37: final report and Goal Signals
- Success criteria

## Entry conditions

Runs only when `--ci ∈ flags`; skip silently otherwise. Phase 5 need not have run — bare `--ci` and `--push --ci` reach this phase by design.

GitHub-only: the loop depends on `gh run` controls (`gh run view --log-failed`, `gh run rerun`). On a non-GitHub remote `ci-monitor.sh` returns `RESULT=unsupported-provider` (exit 2). Treat it like `pr-not-found` / `refused-default-branch`: report it directly, say the loop is GitHub-only and why, end Phase 6 cleanly without erroring. See `.claude/skills/_shared/references/forge-detection.md` and `.claude/skills/_shared/references/ci-monitoring.md`.

## Step 32: Discover PR

If `PR_NUMBER` was captured in Phase 5 (created in Steps 23–30 or detected in Step 22a), use it and go to Step 33.

Otherwise (bare `--ci`, `--push --ci`, or Phase 5 did not run):

```bash
PR_NUMBER=$(gh pr list --head "$CURRENT_BRANCH" --json number --jq '.[0].number' 2>/dev/null || echo "")
PR_URL=$(gh pr list --head "$CURRENT_BRANCH" --json url --jq '.[0].url' 2>/dev/null || echo "")
```

With MCP, prefer `mcp__github__list_pull_requests` with `head=<branch>`.

- `PR_NUMBER` empty: hard-stop with `--ci was passed but no open PR exists for branch '<branch>'. Re-run with --pr to create one, or open a PR manually first.`
- Otherwise record `PR_NUMBER`, `PR_URL`; go to Step 33.

## Step 33: Load policy

Read `.claude/skills/_shared/references/ci-monitoring.md` for the failure-classification table, termination policy, audit log schema and loop protocol. It is authoritative; do not duplicate it.

## Step 34: One-time authorization prompt

Skip if `--ci-yes`. Otherwise display:

```
CI auto-fix loop authorization
==============================
PR:                 #<pr_number> (<head_branch> → <base_branch>)
Max auto-pushes:    <resolved --ci-max-pushes>
Max same failure:   <resolved --ci-max-same-failure>
Wall-clock timeout: <resolved --ci-timeout-min> minutes
Audit log:          ~/.claude/session-data/ci-watch/<pr>-<timestamp>.log

Safety constraints (non-toggleable):
  - Never `git push --force`
  - Never `--no-verify`
  - Only push to PR head branch
  - Refuse if head equals default branch

Proceed? (yes/no):
```

On `no`, exit cleanly with `CI monitoring declined; PR was created but not monitored.`

## Step 35: Audit log

Create `~/.claude/session-data/ci-watch/` if absent. Compute `~/.claude/session-data/ci-watch/<pr>-<utc-iso-timestamp>.log` and reuse it for the loop.

## Step 36: Loop iteration

```
.claude/skills/_shared/scripts/ci-monitor.sh \
  --pr <pr_number> \
  --branch <head_branch> \
  --base <base_branch> \
  --max-pushes <N> \
  --max-same-failure <N> \
  --timeout-min <N> \
  --log-file <audit_log_path>
```

Branch on stdout `RESULT=...` per the Loop Protocol in `ci-monitoring.md`:

- `green` → render the success block (Step 37); end Phase 6.
- `handoff` → read `RUN_ID`, `WORKFLOW`, `JOB`, `CATEGORY`, `SIGNATURE`, `LOG_EXCERPT_FILE`, `SUGGESTED_COMMIT_TYPE`, `SUGGESTED_COMMIT_SCOPE` from stdout. Apply the fix per the Failure Classification table for `CATEGORY` (in `ci-monitoring.md`). Use the direct-vs-agents decision tree of Phase 1, constrained to the failure scope. Stage the fix, validate the message with `.claude/skills/git-workflow/scripts/validate-commit.sh`, commit, push to the head branch (never `--force`, never `--no-verify`). Go to Step 36.
- `rerun-pending` → flake suspected; the script already ran `gh run rerun --failed`. Apply no fix. `sleep 30`, then Step 36.
- `bail-recurrence` / `bail-nonfixable` / `bail-pushes` / `bail-timeout` → render a diagnosis block citing the audit log path, the `RESULT=` value, the `REASON=` line if present, and the cap that fired. End Phase 6; push nothing further.
- `pr-not-found` / `refused-default-branch` → surface the error; do not retry.
- `unsupported-provider` → remote is not GitHub; report that the loop is GitHub-only and skip cleanly; no retry, not an error.

## Step 37: Final report

On `green`:

```
✓ CI green for PR #<pr> after <iterations> iteration(s), <pushes> auto-push(es).
  URL: <url>
  Audit log: <path>

Goal Signals (machine-readable — printed verbatim for /goal)
PR_IN_SCOPE: PASS
PR_URL_PRINTED: PASS
CI_GREEN: PASS
CI_BAIL_VISIBLE: n/a
```

On any bail:

```
✗ CI monitoring ended: <RESULT> — <REASON>
  Cap fired: <which cap or constraint>
  URL: <url>
  Audit log: <path>

Goal Signals (machine-readable — printed verbatim for /goal)
PR_IN_SCOPE: PASS
PR_URL_PRINTED: PASS
CI_GREEN: FAIL
CI_BAIL_VISIBLE: PASS
```

Print every Goal Signal verbatim, one `KEY: PASS|FAIL|n/a` per line, as the last lines of the run when `--ci` is set. `PASS` only when the matching success criterion holds, otherwise `FAIL`. `CI_BAIL_VISIBLE` is `PASS` only when a terminal `bail-*` result and its `REASON=` (or equivalent diagnosis) were printed; otherwise `n/a`.

## Success criteria

- **PR_IN_SCOPE**: a PR is in scope — created in Phase 5, detected in Step 22a, or discovered in Step 32.
- **PR_URL_PRINTED**: the final CI report prints the PR URL with the number.
- **CI_GREEN**: the loop reached `RESULT=green` and the Step 37 report shows `✓ CI green`.
- **CI_BAIL_VISIBLE**: if the loop bailed, the Step 37 report states the `bail-*` result, the reason, and the cap or constraint that fired. `n/a` when CI reached green.

These keys let a `/goal` evaluator observe CI-loop completion from the transcript alone.
