# Release Commands, Publish Modes and Final Output

## Contents

- Phase 8: Emit next-step commands
- Phase 9: Final summary
- Success Criteria

## Phase 8: Emit next-step commands

Always emit the following block. Substitute `<new-version>`, `<changed-files>`,
`<notes-path>`, and `<branch>` (the `branch=` from the Phase 2 branch check — the
branch being released) with real values. NEVER run these commands automatically.

```
Release prepared. No commits or tags have been created — review the diff, then run:

git add <changed-files>
git commit -m "chore(release): v<new-version>"
git tag -a v<new-version> -m "v<new-version>"
git push origin HEAD:<branch> --follow-tags

# Create the GitHub release from the drafted notes:
gh release create v<new-version> \
  --notes-file <notes-path> \
  --title "v<new-version>"

# If artifacts were produced locally and need uploading:
gh release upload v<new-version> <path-to-artifact> [...]
```

Under release-branches, append the steps the branch check asked for (see
`.claude/skills/_shared/references/branching-model.md`, "Models").
When `creates_maintenance=release/X.Y` (a final minor or major cut from the trunk):

```
# Create the maintenance branch from the tag, and its backport label (skip the
# label if it already exists):
git push origin vX.Y.0^{commit}:refs/heads/release/X.Y
gh label create "<backport_label>" --description "Cherry-pick to release/X.Y"
```

When `forward_port=1` (a patch cut from `release/X.Y`), carry the CHANGELOG entry — not
the version bump — to the trunk in its own PR:

```
# Forward-port the vX.Y.Z CHANGELOG entry to <trunk>:
git fetch origin
git switch -c chore/changelog-vX.Y.Z origin/<trunk>
# Copy the X.Y.Z section from `git show vX.Y.Z:CHANGELOG.md` into CHANGELOG.md,
# in version order. Leave the version manifests alone.
git commit -am "chore(release): record vX.Y.Z in changelog"
git push -u origin HEAD
gh pr create --base <trunk> --title "chore(release): record vX.Y.Z in changelog" \
  --body "Forward-port of the vX.Y.Z CHANGELOG entry from release/X.Y. No version bump."
```

See "Forward-porting a patch's CHANGELOG entry" in `references/release-branches.md`.

If `--ci-config=generate` ran, append:

```
# Verify the new workflow in CI by pushing the tag — the release workflow will
# build the matrix and attach artifacts to the GitHub release.
```

**Forge provider detection**: release creation is provider-aware.
`publish-release.sh` detects the forge provider on `origin` first and routes
through the matching CLI — `gh release create` for `github`, `tea release
create --tag <tag> [--note-file]` for `forgejo`/`gitea` (per the
operation-equivalence map). The `gh release create`/`gh release upload` commands
emitted in the Phase 8 block above are the `github` path; on a `forgejo`/`gitea`
remote the helper drives `tea release create` instead. Log the resolved provider
and CLI. See `.claude/skills/_shared/references/forge-detection.md`.

When the user passed `--publish[=create|edit|auto]`, after they review the rendered
notes, run:

```
.claude/skills/releaser/scripts/publish-release.sh v<new-version> <notes-path> --mode=<mode>
```

**Examples:**

```
# Preview only — prints the resolved gh command without executing it:
.claude/skills/releaser/scripts/publish-release.sh v1.4.0 docs/releases/v1.4.0.md --mode=auto

# Apply — executes the resolved gh command:
.claude/skills/releaser/scripts/publish-release.sh v1.4.0 docs/releases/v1.4.0.md --mode=auto --confirm
```

This prints the resolved `gh` command without executing it. Re-run with `--confirm` to
apply. Use `--mode=edit` only when intentionally replacing an existing release body —
this is destructive.

## Phase 9: Final summary

Report to the user:

- Detected language / build system / manifest files.
- Proposed vs. requested version.
- Release model and branch: `model` and `branch` from the Phase 2 check (or "no
  `RELEASING.md`"), plus the follow-ups it requires — the new `release/X.Y` branch and
  label, or the CHANGELOG forward-port PR.
- Backport gate (patches under release-branches): `clean` with the number of PRs verified
  and the `since=` window, or the rows the user chose to ship without, listed as
  "Deferred backports".
- Target `{os × arch}` matrix.
- Files modified (changelog, notes, manifests, workflow).
- CI workflow-file outcome (generate / audit / skipped) and report path if audit ran.
- Publish mode outcome: whether the release was published (applied with `--confirm`),
  previewed only (print-only via `--publish` without `--confirm`), or emit-only
  (no `--publish` flag).
- Release CI loop outcome (if `--ci` was set): `green` (final status), `bail-*`
  (with reason and cap that fired), `loop-blocked`, `declined`, or `skipped` (no
  successful publish). Include the audit log path so the user can inspect it.
- The exact command block from Phase 8.

Then, as the **last lines** of the summary, print the Goal Signals block verbatim:

```
Goal Signals (machine-readable — printed verbatim for /goal)
RELEASE_PUBLISHED: <PASS|FAIL|n/a>
GATES_INTERACTIVE: <PASS|FAIL>
CI_GREEN: <PASS|FAIL|n/a>
CI_BAIL_VISIBLE: <PASS|n/a>
AUDIT_LOG_PRINTED: <PASS|FAIL|n/a>
```

Print one `KEY: PASS|FAIL` per line. Use `PASS` only when the matching `## Success
Criteria` item holds; otherwise `FAIL`. See `## Success Criteria` for each key's exact
condition and `n/a` rules.

## Success Criteria

- **RELEASE_PUBLISHED**: `--publish --confirm` ran and `publish-release.sh --confirm` exited 0 (the release exists on GitHub). `n/a` when `--publish` was not set or ran in preview-only mode.
- **GATES_INTERACTIVE**: Every human gate that applied this run — Phase 2 version confirm, the Phase 2 backport gate decisions, Phase 5 manifest diff review, and the Phase 8.5.1 authorization — was satisfied by explicit user input and **none** was auto-approved or bypassed (including under an active `/goal`). `FAIL` if any applicable gate was skipped without a human `yes`. This is the safety invariant that keeps `/goal` from wrapping the whole skill.
- **CI_GREEN**: If `--ci` ran, the Phase 8.5 loop reached `RESULT=green` and the Phase 9 summary shows "Release CI loop outcome: `green`". `n/a` when `--ci` was not set or the loop was skipped (no successful publish).
- **CI_BAIL_VISIBLE**: If `--ci` ended without green, the summary states the terminal outcome (`bail-*`, `loop-blocked`, or `declined`) and the cap/constraint that fired. `n/a` when `--ci` reached green or was not set.
- **AUDIT_LOG_PRINTED**: If `--ci` ran, the Phase 9 summary printed the audit-log path. `n/a` when `--ci` was not set or the loop was skipped.

These keys are emitted verbatim in the Phase 9 Goal Signals block so a `/goal` evaluator can observe completion from the transcript alone.
