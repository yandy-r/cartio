# CI Monitoring Reference

## Contents

- Provider Support
- Trust Boundary
- Caps and Defaults
- Failure Classification
- Termination Policy
- Loop Protocol
- Audit Log Schema
- Idempotency
- Safety Constraints (Non-Toggleable)
- Failure Signature
- When NOT to Use `--ci`
- Release Mode
- References

Consumed by `git-workflow` (Phase 6), `prp-pr` (Phase 7), `pr-autofix`
(Phase 7), and `releaser` (Phase 8.5) when the `--ci` flag is passed. This
document defines the policy, caps, failure classification, termination signals,
audit schema, and loop protocol that all four skills implement.

Two execution scripts share the same contract:

- **PR mode** — `.claude/skills/_shared/scripts/ci-monitor.sh`
  watches checks on a PR head branch (used by `git-workflow`, `prp-pr`,
  `pr-autofix`).
- **Release mode** — `.claude/skills/_shared/scripts/release-ci-monitor.sh`
  watches the release-event workflow run for a published tag (used by
  `releaser` Phase 8.5). See the "Release mode" section below for the
  mode-specific contract.

Failure classification and signature computation are shared between both
scripts via `.claude/skills/_shared/scripts/lib/ci-classify.sh`.
This document and the scripts are the single source of truth — every consumer
must not diverge from either.

---

## Provider Support

The CI-autofix loop is **GitHub-only**. Both `ci-monitor.sh` and
`release-ci-monitor.sh` depend on `gh` run controls (`gh run view
--log-failed`, `gh run rerun`, `gh run list`) that have no Forgejo/Gitea (`tea`)
or GitLab (`glab`) equivalent. Before entering the loop the scripts detect the
forge provider on the watched remote; on any non-GitHub remote they emit
`RESULT=unsupported-provider` (exit 2) and do nothing else.

The detection contract — provider values `github` / `forgejo` / `gitea` /
`gitlab` / `unknown`, the `forge_detect_provider` / `forge_cli` law — lives in
`forge-detection.md` in this directory (capability matrix: "CI-autofix loop
(`--ci`)" is GitHub-only).

**Skill loop handling:** treat `RESULT=unsupported-provider` exactly like
`pr-not-found` / `refused-default-branch` — surface it directly and do **not**
loop. State that the loop is GitHub-only, skip it cleanly, and never re-invoke
the monitor. It is a terminal non-loop result, not an error to retry.

---

## Trust Boundary

`--ci` grants the following standing authorization for the duration of one skill
invocation:

- **Loop authorization**: the skill may invoke `ci-monitor.sh` repeatedly, apply
  code fixes, commit, and push — bounded by the caps in the next section.
- **One-time prompt**: at loop start the skill renders a confirmation block
  showing the resolved caps (PR number, head branch, `--max-pushes`,
  `--max-same-failure`, `--timeout-min`) and the non-toggleable safety
  constraints below. The user must confirm before the first iteration. Skip this
  prompt by passing `--ci-yes` (for non-interactive callers). The prompt is a
  `confirm-publish` gate (`question-policy.md` in this directory): with a chat turn
  but no question tool, use the text-mode fallback. Headless (no question tool or
  chat turn) and without `--ci-yes`: do not enter the loop, report that CI was not
  monitored, and print `rerun with --ci-yes`.
- **Non-toggleable safety constraints** (cannot be overridden by any flag):
  - Never `git push --force` or `git push --force-with-lease`.
  - Never commit with `--no-verify`.
  - Only push to the PR head branch (the branch `gh pr view` reports as
    `headRefName`).
  - Refuse to operate if the head branch equals the repository default branch
    (exits `RESULT=refused-default-branch`, code 2).
- **Audit log**: every script invocation appends a JSONL record to
  `~/.claude/session-data/ci-watch/<pr>-<utc-timestamp>.log`. The path is
  determined once at loop start and reused for all iterations.

---

## Caps and Defaults

| Flag                    | Default | Purpose                                                    |
| ----------------------- | ------- | ---------------------------------------------------------- |
| `--ci-max-pushes`       | 5       | Hard cap on auto-pushes per invocation                     |
| `--ci-max-same-failure` | 3       | Bail after the same failure signature recurs N times       |
| `--ci-timeout-min`      | 30      | Wall-clock cap in minutes from first iteration to bail     |
| `--ci-yes`              | (off)   | Skip the one-time auth prompt (non-interactive)            |
| `--ci-dry-run`          | (off)   | Wire-test only — script returns `RESULT=green` immediately |

Skills pass these as `--max-pushes`, `--max-same-failure`, `--timeout-min`, and
`--dry-run` to `ci-monitor.sh`. `--ci-yes` is consumed by the skill layer (no
script flag).

---

## Failure Classification

The script's `classify_failure()` function matches log content in priority order.
The table below reproduces the exact categories and strategies.

| Category           | Fixable      | Strategy                                                                                                                                             |
| ------------------ | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `lint`             | yes          | Run repo formatter/linter; commit `fix(ci): apply linter fixes`                                                                                      |
| `format`           | yes          | Run formatter; commit `fix(ci): apply formatting`                                                                                                    |
| `type-check`       | yes          | LLM reads error, applies typed fix at the source; commit `fix(<area>): <summary>`                                                                    |
| `unit-test`        | yes          | LLM reads failing test + source, applies fix to source (preferred) or test; commit `fix(<area>): <summary>`                                          |
| `build`            | yes          | LLM reads error, applies fix (missing import, syntax, dep version); commit `fix(<area>): <summary>`                                                  |
| `integration-test` | no (default) | Bail with diagnosis — too risky to auto-fix                                                                                                          |
| `infra`            | no           | Bail; surface to user — runner outage, network failure                                                                                               |
| `secret-missing`   | no           | Bail; surface to user — required secret/env var absent                                                                                               |
| `flake-suspected`  | retry-once   | `gh run rerun --failed <id>`; emits `RESULT=rerun-pending` so the caller waits and re-invokes. If same failure recurs, escalate to `bail-nonfixable` |
| `unknown`          | no           | Bail with full log excerpt — cannot classify                                                                                                         |

Classification order is fixed: `lint` → `format` → `type-check` → `unit-test` →
`build` → `integration-test` → `infra` → `secret-missing` → `flake-suspected` →
`unknown`. The first matching pattern wins.

For `flake-suspected`: the script logs `action=rerun`, calls
`gh run rerun --failed <run_id>`, and exits `RESULT=rerun-pending` (code 21).
The caller MUST sleep ~30s and re-invoke without applying any fix. If the same
signature appears a second time with a prior `rerun` entry in the log, the script
escalates directly to `bail-nonfixable` (code 11) without another rerun.

---

## Termination Policy

The script writes `RESULT=<value>` to stdout before exiting with the
corresponding code.

| RESULT=                  | Exit code | Meaning                                                                  |
| ------------------------ | --------- | ------------------------------------------------------------------------ |
| `green`                  | 0         | All checks pass                                                          |
| `handoff`                | 20        | Caller must apply fix + commit + push, then re-invoke                    |
| `rerun-pending`          | 21        | Flake-suspected; caller must sleep ~30s, then re-invoke (no fix applied) |
| `bail-recurrence`        | 10        | Same signature recurred ≥ `--ci-max-same-failure` times                  |
| `bail-nonfixable`        | 11        | Failure category is non-fixable (or flake exhausted retries)             |
| `bail-pushes`            | 12        | Push count ≥ `--ci-max-pushes`                                           |
| `bail-timeout`           | 13        | Wall-clock ≥ `--ci-timeout-min`, or checks never registered              |
| `pr-not-found`           | 2         | PR does not exist or is unreachable                                      |
| `refused-default-branch` | 2         | Will not operate when head branch equals the default branch              |
| `unsupported-provider`   | 2         | Remote is not GitHub; the CI-autofix loop is GitHub-only                 |

Exit code 1 is reserved for argument/usage errors and never appears in the loop.

---

## Loop Protocol

The SKILL.md files for `git-workflow` and `prp-pr` implement the
following LLM-side loop. The script is a single-shot checker; the loop lives in
the skill.

**Long-running monitors.** One `ci-monitor.sh` call can block for up to
`--timeout-min`. Some runtimes move a shell command to a background job after a
fixed time (OMP: after 60 s). Pass a shell timeout above the monitor's wall-clock
cap, or wait for the background job's result; never end the turn while a monitor
runs, because headless runs exit and drop the result.

1. **One-time auth prompt** (skip if `--ci-yes`): render resolved caps + safety
   constraints to the user; require confirmation before proceeding.

2. **Initialize audit log path**: compute
   `~/.claude/session-data/ci-watch/<pr>-<utc-iso-timestamp>.log`. Create the
   directory if absent. Reuse this same path for every iteration in the session.

3. **Loop iteration**: invoke `ci-monitor.sh` with all resolved caps plus
   `--log-file <path>`:

   ```
   ci-monitor.sh \
     --pr <number> \
     --branch <head-branch> \
     --base <base-branch> \
     --max-pushes <N> \
     --max-same-failure <N> \
     --timeout-min <N> \
     --log-file <path> \
     [--dry-run]
   ```

4. **Branch on stdout `RESULT=...`**:
   - **`green`** — render success block (CI passed, no further pushes needed);
     exit phase.

   - **`handoff`** — read the following fields from stdout:
     - `RUN_ID` — GitHub Actions run ID of the failing run
     - `WORKFLOW` — workflow name
     - `JOB` — first failing job name
     - `CATEGORY` — failure category (from classification table above)
     - `SIGNATURE` — 16-hex failure signature
     - `LOG_EXCERPT_FILE` — path to a temp file containing the raw log
     - `SUGGESTED_COMMIT_TYPE` — always `fix`
     - `SUGGESTED_COMMIT_SCOPE` — always `ci`

     Apply fix per the Failure Classification table strategy for `CATEGORY`.
     Validate the commit message via
     `.claude/skills/git-workflow/scripts/validate-commit.sh`.
     Push to head branch (no `--force`, no `--no-verify`).
     Delete or ignore `LOG_EXCERPT_FILE` after reading.
     Goto step 3.

   - **`rerun-pending`** — flake suspected; the script already triggered
     `gh run rerun --failed`. Do NOT apply any fix. Sleep ~30s to let the rerun
     register, then goto step 3 to observe the new run's outcome.

   - **`bail-recurrence`** / **`bail-nonfixable`** / **`bail-pushes`** /
     **`bail-timeout`** — render a diagnosis block citing:
     - The audit log path
     - The `RESULT=` value and any `REASON=` line emitted by the script
     - The cap or constraint that fired
       Exit phase; do not push further.

   - **`pr-not-found`** / **`refused-default-branch`** / **`unsupported-provider`**
     — surface the error directly; do not enter the loop. For
     `unsupported-provider`, state that the CI-autofix loop is GitHub-only (see
     the Provider Support section above) and skip it cleanly.

---

## Audit Log Schema

One JSONL record per `ci-monitor.sh` invocation, appended to the log file.

```json
{
  "timestamp": "2026-05-05T12:34:56Z",
  "iteration": 1,
  "pr": 42,
  "run_id": 12345,
  "workflow": "CI",
  "conclusion": "failure",
  "classification": "lint",
  "action": "handoff",
  "push_count": 1,
  "signature": "a1b2c3d4e5f60789"
}
```

Field definitions (matching `log_jsonl()` in the script):

| Field            | Type                  | Meaning                                                                                                                                                              |
| ---------------- | --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `timestamp`      | string (ISO 8601 UTC) | Time the record was written                                                                                                                                          |
| `iteration`      | integer               | Log line count + 1 before this append (1-based)                                                                                                                      |
| `pr`             | integer               | PR number passed via `--pr`                                                                                                                                          |
| `run_id`         | integer               | GitHub Actions `databaseId`; `0` when not applicable                                                                                                                 |
| `workflow`       | string                | Workflow name from `gh run list`; empty string when not applicable                                                                                                   |
| `conclusion`     | string                | `success`, `failure`, or empty for pre-check bail                                                                                                                    |
| `classification` | string                | Category from `classify_failure()`; empty when classification did not run                                                                                            |
| `action`         | string                | One of: `green`, `handoff`, `rerun`, `bail-pushes`, `bail-timeout`, `bail-recurrence`, `bail-nonfixable`. (`rerun` corresponds to `RESULT=rerun-pending` on stdout.) |
| `push_count`     | integer               | Number of `handoff` actions recorded in the log before this entry                                                                                                    |
| `signature`      | string                | 16-hex failure signature; empty string when not computed                                                                                                             |

Optional fields (present only in specific cases):

| Field            | When present          |
| ---------------- | --------------------- |
| `"dry_run":true` | `--dry-run` mode only |

---

## Idempotency

- Re-invoking `--ci` against an already-open PR detects the PR via
  `gh pr list --head <branch>` (or the PR number the user supplied); no
  duplicate PR is created.
- The push counter (`push_count`) resets to zero at the start of each skill
  invocation because the audit log path is timestamp-suffixed — a new session
  writes a new log file and the script counts `action=handoff` entries within
  that file only.
- Because log files are timestamp-suffixed, no two sessions overwrite each
  other's logs.
- Pushes from a prior session are NOT counted toward the current session's
  `--ci-max-pushes` cap.

---

## Safety Constraints (Non-Toggleable)

These rules are enforced by the skill layer and the script; no flag can override
them:

- Never `git push --force` or `git push --force-with-lease` to any branch.
- Never commit with `--no-verify`; all hooks must pass.
- Only push to the PR head branch (`headRefName` as reported by `gh pr view`).
- Refuse if the head branch equals the repository default branch
  (`refused-default-branch`, exit 2).
- Fixes are applied only to source files identified by the CI log — no
  speculative refactors outside the failure scope.
- Commit messages must pass `validate-commit.sh` before the push is made.

---

## Failure Signature

The script computes a failure signature via `compute_signature()`:

```
sig = sha256( workflow_name + "|" + normalize(first_failing_step) )
    truncated to the first 16 hex characters
```

Normalization of the step name: lowercase, collapse whitespace runs to a single
space, strip leading/trailing whitespace.

The resulting 16-hex string (e.g. `a1b2c3d4e5f60789`) uniquely identifies the
combination of workflow and first failing step. The recurrence cap
(`--ci-max-same-failure`) triggers when the script finds that this signature
already appears in the audit log at least `N-1` times before the current
iteration — meaning the N-th occurrence of the same root cause triggers bail.

This definition of "same failure" deliberately ignores transient differences
(log verbosity, timestamps, line numbers outside the step name) and fires only
when the same workflow step keeps failing, which is the strongest signal that
auto-fixing is not converging.

---

## When NOT to Use `--ci`

- **Documentation-only PRs**: no CI failure to fix; `--ci` adds overhead with
  no benefit.
- **Novel third-party service configuration**: infra and secret failures bail
  immediately, but if the entire PR is CI infrastructure changes, auto-fixing
  risks introducing incorrect config.
- **When the user wants to review every change**: `--ci` applies fixes and pushes
  autonomously within the caps; use it only when that level of automation is
  acceptable.
- **Default branch PRs**: the script refuses (`refused-default-branch`); do not
  pass `--ci` when working directly on `main`/`master`.
- **Repos without `gh` CLI access**: the script depends on `gh`; it will fail
  on invocation if `gh` is not authenticated or the repo is inaccessible.

---

## Release Mode

Used by `releaser` Phase 8.5 (`--ci`) **only** after a successful
`--publish --confirm` has actually created/edited the release on GitHub. The
loop watches the release-event workflow run for the published tag, not a PR.

### Differences from PR mode

| Aspect                 | PR mode (`ci-monitor.sh`)             | Release mode (`release-ci-monitor.sh`)                                                                                                 |
| ---------------------- | ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Resource watched       | `gh pr checks <pr>` on PR head branch | `gh run list --event=release --workflow <name>` filtered to tag, or `refs/tags/<tag>`                                                  |
| Required args          | `--pr`, `--branch`, `--base`          | `--tag`                                                                                                                                |
| Auto-detected args     | (none)                                | `--workflow` from `.github/workflows/*release*.yml`; `--repo` from `gh repo view`                                                      |
| Re-cut on `handoff`    | `git push` to PR head branch          | Preferred branch-ref dispatch with exact run ID/SHA; destructive re-cut only with explicit `--ci-recut=destructive` and original gates |
| Default-branch refusal | Refuses if head == default branch     | N/A (release runs are not branch-scoped); but loop still refuses to push fixes to default branch when branch protection forbids it     |
| Audit log key field    | `"pr": <number>`                      | `"tag": "<vX.Y.Z>"`                                                                                                                    |
| Audit log path         | `~/.claude/session-data/ci-watch/`    | `~/.claude/session-data/release-ci-watch/`                                                                                             |
| Fix-agent type         | `pr-comment-fixer`                | `release-fix-applier`                                                                                                              |

All other contract surfaces — exit codes, `RESULT=` values, classification
categories, signature computation, recurrence/timeout/push caps, JSONL schema
columns (other than the `pr` vs `tag` key) — are identical.

### Retry strategy on `handoff`

After pushing a fix, dispatch a verified non-publishing `workflow_dispatch` path
on the fix-bearing release branch, never on the unchanged tag. Phase 8.5.0 checks
that branch-ref dispatch validates that commit without modifying published releases,
artifacts or tags. Prefer that path when safe; otherwise use the destructive
fallback below only with explicit `--ci-recut=destructive` and the original gates.
Without either authorized path, exit `loop-blocked`.

```
gh workflow run <file> --repo <owner/name> --ref <fix-bearing release branch>
```

Correlate a unique new dispatch run by workflow, branch, expected commit SHA and
dispatch time. Then re-invoke `release-ci-monitor.sh` with the same tag, repo, caps
and log plus `--run-id <id> --expected-sha <40-hex fix SHA> --workflow <file>`.
These selectors are optional and paired; existing tag-mode callers stay unchanged.
Exact-run mode verifies the ID, head SHA, event (`workflow_dispatch` for a
dispatch retry, `release` for the post-re-cut run) and workflow
path before watching, and returns `RESULT=not-found` with
`REASON=retry-correlation-mismatch` on any mismatch or unavailable metadata. This is
terminal, not a reason to fall back to tag selection. `rerun-pending` keeps the same
run ID and SHA. The release and tag remain unchanged; green validates the fix-bearing
commit, not a replacement published release.

**Fallback: destructive re-cut (requires `--ci-recut=destructive`).** When safe
branch-ref dispatch is unavailable, only after the original publish-success,
8.5.1 authorization, `/goal` re-confirmation and branch-protection gates:

```
gh release delete <tag> --cleanup-tag --yes
.claude/skills/releaser/scripts/publish-release.sh \
  <tag> <notes-path> --mode=create --confirm
```

The release and tag are deleted and re-created, triggering the release event.
Never take this path without explicit `--ci-recut=destructive`. Deleting a release
does not delete old runs, so do not resume plain tag-mode selection: after
re-publication, resolve the new `release`-event run whose head SHA equals the
pushed fix and whose creation time is at or after re-publication, then monitor it
with the same exact-run selectors (the event check accepts `release` for this
path; the expected SHA is the fix commit, so the pre-re-cut run cannot match).
Ambiguity or no new run within the remaining budget stops the loop. Keep the same
audit log and caps. Dispatch failure or correlation mismatch does not authorize a
re-cut.

### Release-mode safety constraints (additional to PR-mode)

- **Never edit release artifacts**: `CHANGELOG.md`, files under `docs/releases/`,
  and version manifests (`package.json` version field, `pyproject.toml` version,
  `Cargo.toml` version) are immutable for the lifetime of the loop. The fix
  agent (`release-fix-applier`) refuses these paths.
- **Never edit `.github/workflows/*.yml`** unless the failed step is itself a
  workflow-file syntax error.
- **Branch-protection check before push**: before committing on `main` (or
  whatever branch the loop is running on), call
  `gh api repos/<owner>/<name>/branches/<branch>/protection`. If
  `required_pull_request_reviews` is set, exit with `loop-blocked` and surface
  a PR-based remediation instead of force-pushing or bypassing protection.

### Release-mode handoff output (extra fields)

`release-ci-monitor.sh` emits the same fields as `ci-monitor.sh` on a handoff
(`RUN_ID`, `WORKFLOW`, `JOB`, `CATEGORY`, `SIGNATURE`, `LOG_EXCERPT_FILE`,
`SUGGESTED_COMMIT_TYPE`, `SUGGESTED_COMMIT_SCOPE`), plus:

- `WORKFLOW_FILE` — basename of the resolved workflow file (e.g. `release.yml`),
  used to locate the dispatch workflow.
- `STEP` — normalized first-failing step name. Useful for the fix agent's
  reasoning about which file is implicated.
- `TAG` — the released tag (mirrors the `--tag` input for convenience).
- `REPO` — `owner/name` resolved at startup.

---

## References

- `.claude/skills/_shared/scripts/ci-monitor.sh` — PR-mode execution script
- `.claude/skills/_shared/scripts/release-ci-monitor.sh` — release-mode execution script
- `.claude/skills/_shared/scripts/lib/ci-classify.sh` — shared failure classification + signature lib
- `.claude/skills/git-workflow/SKILL.md` — Phase 6 (PR CI monitoring loop)
- `.claude/skills/prp-pr/SKILL.md` — Phase 7 (PR CI monitoring loop)
- `.claude/skills/pr-autofix/SKILL.md` — Phase 7 (PR CI monitoring loop)
- `.claude/skills/releaser/SKILL.md` — Phase 8.5 (release CI monitoring loop)
- `.claude/skills/git-workflow/scripts/validate-commit.sh` — commit message validator
