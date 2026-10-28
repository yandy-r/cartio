# Releasing Under a RELEASING.md Model

## Contents

- Branch check output
- Backport gate
- Previous tag and changelog range
- Recording the new maintenance branch
- Forward-porting a patch's CHANGELOG entry
- Release CI fixes on a release branch

How `releaser` applies a project's release model. The rules themselves (trunk-only
vs. release-branches, maintenance and frozen branches, backport labels) live in
`.claude/skills/_shared/references/branching-model.md`; this file only
covers what the releaser does with them. Without a `RELEASING.md`, none of this applies
and the releaser behaves as it always has.

## Branch check output

`.claude/skills/releaser/scripts/check-release-branch.sh <version>` prints
`key=value` lines on success (exit 0). Keep them for later phases.

| Key                   | Meaning                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------- |
| `present`             | `1` when `RELEASING.md` exists, `0` otherwise (no check was made)                        |
| `model`               | `trunk-only` or `release-branches` (empty when `present=0`)                              |
| `version`             | The version without a leading `v`                                                        |
| `kind`                | `major` (`X.0.0`), `minor` (`X.Y.0`) or `patch` (`X.Y.Z`, Z > 0); pre-releases included  |
| `branch`              | The branch being released; Phase 8 pushes to it                                          |
| `trunk`               | The trunk named in the state                                                             |
| `creates_maintenance` | `release/X.Y` for a final `X.Y.0` under release-branches, else empty                     |
| `backport_label`      | The resolved label for `X.Y` (e.g. `backport:0.6`) under release-branches, else empty    |
| `forward_port`        | `1` for a patch under release-branches: the CHANGELOG entry must be carried to the trunk |

Exit 1 means the version does not belong on this branch; stderr has the reason and the
exact fix commands. Exit 2 means the state block is malformed. Both stop the release.

A pre-release of a new line (`0.7.0-beta.1`) is cut from the trunk but does **not**
create `release/0.7`; the branch is created by the final `0.7.0`.

## Backport gate

A patch is tagged only when `release/X.Y` has every fix meant for it
(`branching-model.md#before-tagging-a-patch`). After a branch check with
`model=release-branches` and `kind=patch`, run:

```
.claude/skills/_shared/scripts/backport-audit.sh release/X.Y
```

Stdout is one `<status>\t<pr>\t<merge_sha>\t<title>\t<detail>` row per PR checked, then
`since=<ref>`. Show the non-`ok` rows as a table (status, PR, title, detail) and count the
`ok` rows.

| Result                   | Action                                                                                                                                               |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| exit 0                   | Print `Backport gate: clean (<n> backported PRs verified, fixes since <ref> reviewed)` and continue.                                                 |
| exit 3, `missing` rows   | STOP. The fix is labelled for this line but not on `release/X.Y`. Next step: `/backport --pending --to release/X.Y`, merge, re-run the releaser. |
| exit 3, `in-review` rows | STOP. The backport PR is open. Next step: review and merge it, then re-run the releaser.                                                             |
| exit 3, `unlabelled`     | Ask (below). Handle them after any `missing`/`in-review` rows are resolved.                                                                          |
| exit 1                   | STOP with its stderr. When `gh` is missing or not authenticated, also show step 0 of the project's patch steps in `RELEASING.md` (below).            |
| exit 2                   | STOP, as for a malformed state in Phase 0.                                                                                                           |

`--pending` skips a `missing` row whose detail is `backport #M closed unmerged`; retry
that one with `/backport <PR#> --to release/X.Y`.

For `unlabelled` rows, ask once (AskUserQuestion), listing every row:

```
These fixes merged into <trunk> since <ref> without the <label> label:
  #<pr> <title>
Do any of them affect vX.Y?
```

- **None; they only fix trunk code** → continue. Record the answer in the Phase 9 summary.
- **Some do; stop** → STOP and print, per PR the user names:
  `gh pr edit <pr> --add-label <label>`, then `/backport --pending --to release/X.Y`.

A STOP can be overridden only by an explicit user instruction to ship without specific
PRs. Ask again with those PR numbers listed, and name them under "Deferred backports" in
the Phase 9 summary. Never infer the override, and never apply it under `/goal`.

When the gate cannot run (exit 1 because `gh` is missing or not authenticated), the tag
still waits. The user either fixes `gh` and re-runs the releaser, or runs the manual
commands in step 0 of their `RELEASING.md` patch steps and confirms the result explicitly.

`--dry-run` runs the audit, which is read-only, and puts its table in the plan without
asking. The first gate on a line that was patched before this check existed should sweep
the whole line: re-run with `--since vX.Y.0` when the user asks for it or when
`RELEASING.md` has no step 0 (see `/release-model --audit`, check 13).

## Previous tag and changelog range

`detect-project.sh` reports `latest_tag` and `draft-changelog.sh` defaults its range to
`git describe --tags --abbrev=0`: the nearest tag reachable from `HEAD`. What that
returns depends on the branch:

| Releasing                              | `git describe` returns                                                                                  | Pass a from-ref?                                                                                      |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| trunk-only, any version                | The previous release tag                                                                                | No                                                                                                    |
| Patch on `release/X.Y`                 | The latest `vX.Y.*` tag: the branch starts at `vX.Y.0`, and newer trunk tags are not reachable from it  | Recommended, to pin the line: `git describe --tags --abbrev=0 --match 'vX.Y.*'`                       |
| Minor/major on the trunk               | The previous minor's `.0` tag: patch tags sit on `release/*` branches and are not reachable from trunk  | No. Fixes that were also backported into `X.Y.Z` patches appear again; they did land on the trunk too |
| Final `X.Y.0` after trunk pre-releases | The latest pre-release tag (`vX.Y.0-beta.N`), so the notes would cover only the commits since that beta | Yes, the previous final release: `git describe --tags --abbrev=0 --exclude '*-*'` (e.g. `v0.6.0`)     |

Use the same from-ref for the Phase 2 commit scan (`git log <from-ref>..HEAD`) and for
`draft-changelog.sh <new-version> <from-ref>`.

## Recording the new maintenance branch

When `creates_maintenance` is set, Phase 5 also runs:

```
.claude/skills/_shared/scripts/release-state-update.sh --add-maintenance release/X.Y
```

It adds `release/X.Y` as the newest maintenance branch, moves branches outside the
support window to `frozen`, and re-renders the Current state table. It never commits.
Add `RELEASING.md` to the Phase 8 `git add` list so the state change ships in the
release commit. In `--dry-run`, run it with `--dry-run` instead and show the diff.

The branch itself does not exist until the Phase 8 command pushes it from the tag. Other
skills read the state, so `release-state.sh check` reports it missing on origin until
then.

## Forward-porting a patch's CHANGELOG entry

Fixes reach `release/X.Y` by cherry-pick from the trunk, so the trunk already has the
code. What it lacks is the `vX.Y.Z` CHANGELOG entry and the version bump. Carry the
entry only; the trunk's manifests stay on the trunk's line. Insert the entry in version
order: directly above the `X.Y.(Z-1)` entry and below any later minor the trunk has
already released. Title the PR `chore(release): record vX.Y.Z in changelog`.

## Release CI fixes on a release branch

Phase 8.5 pushes a CI fix to the branch that was released. For a patch that is
`release/X.Y`, so the same fix usually also belongs on the trunk: open a trunk PR for it
after the loop finishes, following `branching-model.md` (fixes land on the trunk first;
here the order is reversed only because the release was already cut).
