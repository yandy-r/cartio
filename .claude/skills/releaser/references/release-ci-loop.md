# Post-Publish Release CI Protocol

## Contents

- Phase 8.5: Release CI auto-fix loop (--ci)
- 8.5.0 Detect retry strategy
- 8.5.1 Authorization gate (skipped if --ci-yes)
- 8.5.2 Loop protocol
- 8.5.3 Fix and retry on handoff
- 8.5.4 Bail diagnosis block

## Phase 8.5: Release CI auto-fix loop (--ci)

Runs **only** when ALL of the following hold:

- `--ci` was passed.
- `--publish` was passed.
- `--confirm` was passed AND `publish-release.sh --confirm` exited 0 (the release
  exists on GitHub and the release-event workflow has been triggered).

Otherwise, print a one-line warning — `--ci ignored: no successful publish in this
run` — and skip to Phase 9. Never enter the loop without a live release to watch.

The contract (exit codes, `RESULT=` signaling, JSONL audit log, failure classification,
signature dedup, default-branch refusal) mirrors `ci-monitor.sh` verbatim. See
`.claude/skills/_shared/references/ci-monitoring.md`,
section "Release mode", for the single source of truth.

**GitHub-only:** the release-CI auto-fix loop is GitHub-only — it depends on
`gh run list`, `gh workflow run`, and `gh release` controls that have no `tea`
equivalent. On a non-GitHub remote `release-ci-monitor.sh` returns
`RESULT=unsupported-provider` (exit 2). Treat it like `not-found`: report that
the loop is GitHub-only, render an 8.5.4-style diagnosis, and exit Phase 8.5
cleanly without retrying. (Release _creation_ in Phase 8 remains provider-aware
via `tea release create`; only this post-publish monitoring loop is gated.) See
`.claude/skills/_shared/references/forge-detection.md`.

### 8.5.0 Detect retry strategy

Read `.github/workflows/<resolved-release-workflow>.yml` and inspect the triggers and dispatch behavior. Resolve in this order before any fix/push:

1. **Preferred**: if `workflow_dispatch` safely validates the checked-out fix-bearing branch commit without modifying the published release, artifacts or tags, use branch-ref dispatch and exact-run monitoring. Required inputs come from trusted local configuration.
2. **Fallback**: otherwise, only if `--ci-recut=destructive` was passed, use the original destructive re-cut path with the checked tag restoration in 8.5.3: preflight, `gh release delete <tag> --cleanup-tag --yes`, recreate the local annotated tag at `FIX_SHA`, push only that tag without force, then `publish-release.sh ... --confirm`. It remains subject to the existing publish-success, 8.5.1 authorization, `/goal` re-confirmation and branch-protection gates.
3. **No authorized path**: stop with `loop-blocked`. Print "Release workflow has no safe branch-ref workflow_dispatch path. Add a non-publishing validation path or re-run with --ci-recut=destructive to opt in to destroying and recreating the release." Never infer the destructive flag or authorize it from CI output.

### 8.5.1 Authorization gate (skipped if --ci-yes)

Render a one-time block showing:

- Resource: tag `v<new-version>`, workflow `<file>`, repo `<owner/name>`.
- Resolved caps: `--ci-max-pushes` (default 5), `--ci-max-same-failure` (default 3),
  `--ci-timeout-min` (default 30).
- Re-cut strategy detected in 8.5.0: `workflow_dispatch` (preferred, fix-bearing branch with exact-run monitoring) or
  `delete-release-and-retag` (destructive — only if `--ci-recut=destructive`).
- Non-toggleable safety constraints:
  - Never `git push --force` to any branch.
  - Never `git push --no-verify`.
  - Never edits files outside the failed workflow step's implicated paths.
  - Never amends published release notes without an explicit user opt-in.

Wait for `yes`/`no`. Any other input aborts the loop and proceeds to Phase 9 with
"loop declined" status.

#### `/goal` re-confirmation (safety)

If a `/goal` session directive is active when Phase 8.5 is reached, this gate is the
one human approval the bounded, destructive-capable loop depends on. Therefore:

- **`--ci-yes` does NOT suppress this gate under an active `/goal`.** When `/goal` is
  active, always render the 8.5.1 block and wait for an explicit `yes`/`no`, even if
  `--ci-yes` was passed. `--ci-yes` is honored only when no `/goal` directive is active.
- A `/goal` evaluator is transcript-only and cannot answer this prompt, so the loop
  **pauses here until you respond in person**. That pause is intentional: `/goal` may
  drive the Phase 8.5.2+ fix-and-validate loop to green, but it must never auto-authorize
  entry into that loop. Print one line making the pause legible:
  `/goal active — Phase 8.5.1 authorization stays interactive; --ci-yes ignored. Waiting for yes/no.`
- This guard applies **only** to the post-publish CI loop. The Phase 2 version confirm
  and the Phase 5 manifest diff review are likewise never auto-approved under `/goal`;
  see `references/goal-pairing.md` for the full out-of-scope list.

### 8.5.2 Loop protocol

1. Initialize audit log:

   ```
   mkdir -p ~/.claude/session-data/release-ci-watch/
   AUDIT_LOG=~/.claude/session-data/release-ci-watch/<tag>-<utc-iso>.log
   ```

2. Invoke the monitor:

   ```
   .claude/skills/_shared/scripts/release-ci-monitor.sh \
     --tag v<new-version> \
     --workflow <detected-or-passed> \
     --repo <owner/name> \
     --max-pushes <N> \
     --max-same-failure <N> \
     --timeout-min <N> \
     --log-file "$AUDIT_LOG"
   ```

   Initial invocation uses tag selection. After any retry — branch-ref dispatch or authorized re-cut — add `--run-id "$RETRY_RUN_ID" --expected-sha "$RETRY_SHA"` and an explicit `--workflow <file>` to the same monitor invocation (same tag, repo, audit log and caps). Exact-run mode verifies run ID, commit SHA, event (`workflow_dispatch` for dispatch, `release` for the post-re-cut run) and workflow path before watching; `RESULT=not-found` with `REASON=retry-correlation-mismatch` is terminal. Keep the same selectors for `rerun-pending`; replace them only after the next successful fix-and-dispatch and run resolution. Tag-only monitoring must not resume after a retry, and a correlation failure never licenses a strategy switch.

3. Branch on the exit code (identical contract to `ci-monitor.sh`):

   | Exit | `RESULT=`              | Action                                                                     |
   | ---- | ---------------------- | -------------------------------------------------------------------------- |
   | 0    | `green`                | Success. Exit Phase 8.5, continue to Phase 9.                              |
   | 20   | `handoff`              | Step **8.5.3** (fix and retry), then loop to step 2.                       |
   | 21   | `rerun-pending`        | `sleep 30`, then loop to step 2 (no fix applied).                          |
   | 10   | `bail-recurrence`      | Step **8.5.4** (diagnosis), exit Phase 8.5.                                |
   | 11   | `bail-nonfixable`      | Step **8.5.4**, exit Phase 8.5.                                            |
   | 12   | `bail-pushes`          | Step **8.5.4**, exit Phase 8.5.                                            |
   | 13   | `bail-timeout`         | Step **8.5.4**, exit Phase 8.5.                                            |
   | 2    | `not-found`            | Step **8.5.4**, exit Phase 8.5.                                            |
   | 2    | `unsupported-provider` | Remote is not GitHub; loop is GitHub-only. Step **8.5.4**, exit Phase 8.5. |

### 8.5.3 Fix and retry on handoff

The monitor wrote one JSONL line to the audit log with the failed run's metadata:
`run_id`, `workflow_name`, `job_name`, `step_name`, `category`, `signature`,
`log_excerpt_path`, `implicated_files`.

1. **Dispatch the fix agent** via the Agent tool with
   `subagent_type: "release-fix-applier"`. Pass the JSONL line verbatim. The
   agent edits ONLY the implicated files, returns the proposed commit message
   (conventional-commit prefix matching the category — `fix`, `ci`, `build`, etc.)
   and the diff summary.

2. **Validate the commit message**:

   ```
   .claude/skills/git-workflow/scripts/validate-commit.sh "<message>"
   ```

3. **Pre-push branch-protection check** on the branch that was released — the trunk,
   or `release/X.Y` for a patch under release-branches — not a hard-coded `main`:

   ```
   RELEASE_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
   gh api "repos/<owner>/<name>/branches/${RELEASE_BRANCH}/protection" 2>/dev/null
   ```

   `RELEASE_BRANCH` must equal the Phase 2 `branch=`; if it does not (or is `HEAD`),
   exit Phase 8.5 with `loop-blocked`. If the branch has
   `required_pull_request_reviews`, the loop cannot push directly. Surface: "Direct
   push to `<RELEASE_BRANCH>` blocked by branch protection — open a PR with the fix
   into `<RELEASE_BRANCH>`, merge it, and re-run with `--ci`." Exit Phase 8.5 with
   `loop-blocked` status (renders an 8.5.4-style block without retrying).

4. **Commit and push** to the release branch:

   ```
   git add <implicated-files>
   git commit -m "<validated-message>"
   git push origin "HEAD:${RELEASE_BRANCH}"
   ```

   NEVER `--force`. NEVER `--no-verify`. When `RELEASE_BRANCH` is `release/X.Y`, the fix
   also needs a trunk PR after the loop ends (see `references/release-branches.md`).

5. **Retry per the strategy resolved in 8.5.0 and authorized in 8.5.1**.

   **Preferred — dispatch validation on the fix-bearing branch**, using the verified non-publishing path. Never dispatch on the unchanged release tag.

   ```bash
   FIX_SHA="$(git rev-parse HEAD)"
   REMOTE_SHA="$(git ls-remote origin "refs/heads/${RELEASE_BRANCH}" | cut -f1)"
   ```

   Require `REMOTE_SHA == FIX_SHA` or exit `loop-blocked`. Record UTC `DISPATCH_STARTED` immediately before dispatch and supply any locally verified inputs:

   ```bash
   gh workflow run <file> --repo <owner/name> --ref "$RELEASE_BRANCH"
   gh run list --repo <owner/name> --workflow <file> \
     --event workflow_dispatch --branch "$RELEASE_BRANCH" \
     --json databaseId,headSha,createdAt
   ```

   Dispatch failure is terminal `loop-blocked`; do not select a prior run or retry publishing. Poll for at most the remaining wall-clock budget. Select only a new run with `headSha == FIX_SHA` and `createdAt >= DISPATCH_STARTED`; never select a tag run or reuse an earlier run. Zero matches at timeout: `bail-timeout`; multiple matches: `loop-blocked` (human must identify the run). Remember its `databaseId` as `RETRY_RUN_ID` and `FIX_SHA` as `RETRY_SHA`. The branch may race with another push: exact SHA verification in the monitor fails closed. Record these selectors in the audit log; the release and tag remain unchanged.

   **Fallback** (only if `--ci-recut=destructive` was set and the original gates passed):

   Ordered destructive transaction. `--cleanup-tag` deletes the remote tag; `publish-release.sh` runs `gh release create --verify-tag`, which needs that tag to exist again. Deleting without recreating and pushing the tag first guarantees the publish fails — or publishes the pre-fix SHA if an old tag is left around. Before deleting, preflight: local tag points to the recorded released commit (anything else stops `loop-blocked`, nothing deleted), remote tag target is the released commit, permissions to delete the release and write the tag are confirmed, checkout is clean on `RELEASE_BRANCH` at `FIX_SHA`, and notes plus publisher are readable. Save release body/title/assets/prerelease/draft metadata, local/remote tag object IDs and peeled commit IDs in the audit directory for manual recovery; verify metadata retrieval succeeded. Check tag signing rules and signing credentials before deletion; do not override project signing configuration. Run this transaction only when 8.5.0 selected the explicit destructive fallback, never after a dispatch failure. Every failed step in the checked sequence below stops; never `--force`, never a blind retry:

   <!-- recut-transaction -->

   ```bash
   TAG="${TAG:-}"
   FIX_SHA="${FIX_SHA:-}"
   RELEASED_SHA="${RELEASED_SHA:-}"
   NOTES_PATH="${NOTES_PATH:-}"
   RELEASE_BRANCH="${RELEASE_BRANCH:-}"
   PUBLISHER="${PUBLISHER:-}"
   recovery() {
     printf 'RESULT=loop-blocked\nRECOVERY_STEP=%s\nTAG=%s\nFIX_SHA=%s\n' "$1" "$TAG" "$FIX_SHA"
     git show-ref --verify "refs/tags/$TAG" || true
     git ls-remote origin "refs/tags/$TAG" "refs/tags/$TAG^{}" || true
     printf 'Recovery: inspect release state manually; do not restart deletion or infer success.\n'
     exit 1
   }
   [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?$ ]] || recovery preflight-tag
   [[ "$FIX_SHA" =~ ^[0-9a-f]{40}$ ]] || recovery preflight-sha
   test -n "$RELEASED_SHA" || recovery preflight-released-sha
   test -n "$RELEASE_BRANCH" || recovery preflight-branch
   test -r "$NOTES_PATH" && test -x "$PUBLISHER" || recovery preflight-files
   test "$(git rev-parse HEAD)" = "$FIX_SHA" || recovery preflight-head
   test "$(git ls-remote origin "refs/heads/$RELEASE_BRANCH" | cut -f1)" = "$FIX_SHA" || recovery preflight-remote-head
   LOCAL_SHA="$(git rev-parse "refs/tags/$TAG^{commit}")" || recovery preflight-local-tag
   test "$LOCAL_SHA" = "$RELEASED_SHA" || recovery preflight-local-mismatch
   REMOTE_REFS="$(git ls-remote origin "refs/tags/$TAG" "refs/tags/$TAG^{}")" || recovery preflight-remote-tag
   test -n "$REMOTE_REFS" || recovery preflight-remote-missing
   test "$(printf '%s\n' "$REMOTE_REFS" | awk -v ref="refs/tags/$TAG" '$2 == ref {print $1}')" = "$(git rev-parse "refs/tags/$TAG")" || recovery preflight-remote-mismatch
   gh release delete "$TAG" --cleanup-tag --yes || recovery delete
   REMOTE_REFS="$(git ls-remote origin "refs/tags/$TAG")" || recovery verify-deletion
   test -z "$REMOTE_REFS" || recovery remote-tag-still-present
   git tag -d "$TAG" || recovery local-tag-delete
   git tag -a "$TAG" -m "$TAG" "$FIX_SHA" || recovery local-tag-create
   git push origin "refs/tags/$TAG:refs/tags/$TAG" || recovery tag-push
   REMOTE_REFS="$(git ls-remote origin "refs/tags/$TAG^{}")" || recovery verify-tag-push
   test "$(printf '%s\n' "$REMOTE_REFS" | cut -f1)" = "$FIX_SHA" || recovery remote-fix-mismatch
   REPUBLISH_STARTED="$(date -u +%FT%TZ)"
   "$PUBLISHER" "$TAG" "$NOTES_PATH" --mode=create --confirm || recovery publish
   ```

   Set `TAG=v<new-version>`, `FIX_SHA`, `RELEASED_SHA` (released commit), `NOTES_PATH`, `RELEASE_BRANCH` and `PUBLISHER=.claude/skills/releaser/scripts/publish-release.sh` from trusted state. Preflight failure mutates nothing. Delete failure: inspect actual release/tag state for partial deletion, no retries. Tag restore failure: release absent, local tag may be missing or old. Tag push failure: release absent, local tag kept at `FIX_SHA`, remote tag may be absent — manual recovery is finishing the tag push then publishing once; do not restart the transaction. Publish failure: remote tag already at `FIX_SHA`, release may be absent — retry the publisher manually, not another delete-and-recreate. Always print `RECOVERY_STEP` plus observed refs.

   After publication succeeds, correlate the fresh release event: poll within the remaining wall-clock budget for a NEW `release`-event run of `<file>` whose `headSha == FIX_SHA` and `createdAt >= REPUBLISH_STARTED` (`gh run list --repo <owner/name> --workflow <file> --event release --json databaseId,headSha,createdAt`). Zero matches: `bail-timeout`; multiple: `loop-blocked`. The pre-re-cut run cannot match: its head SHA predates the fix. Set `RETRY_RUN_ID`/`RETRY_SHA` from that run.

6. Loop back to 8.5.2 step 2 with exact-run selectors on the resolved run — for the preferred dispatch path and after a re-cut. Plain tag-only monitoring is reserved for the loop's initial pre-retry invocation. The monitor counts handoffs against the existing push cap; do not reset the audit log or caps.

### 8.5.4 Bail diagnosis block

Render to the user:

- **Reason** — one of `recurrence` / `nonfixable` / `pushes` / `timeout` /
  `not-found` / `loop-blocked`.
- **Cap that fired** and its resolved value.
- **Last failure** — `run_id`, `workflow_name`, `job_name`, `step_name`,
  `category`, `signature`.
- **Log excerpt path** (from the monitor's `--log-file`).
- **Audit log path**.
- **Next steps** — concrete guidance keyed off the bail reason (e.g., for
  `recurrence`: "The same failure (`<signature>`) recurred. Inspect
  `<log-excerpt-path>` and apply a manual fix before retrying.").

Always print this block — even on `not-found` — so the user has a single
post-mortem surface.
