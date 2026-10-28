# Branching and Release Model

## Contents

- Rules
- Models
- Where does my change go?
- Backporting
- State block
- Missing or malformed state
- How skills use this

The canonical rules behind a project's `RELEASING.md`. Skills cite sections of this file
instead of restating them. `release-model` generates `RELEASING.md` from these rules;
`release-state.sh` reads the state block described in [State block](#state-block).

## Rules

These apply to both models.

1. **Decide the target release before work starts.** The target decides the base branch.
2. **Land work on the trunk in small, short-lived PRs.** Topic branches live days, not
   weeks. If one falls behind, rebase it. No long-lived feature or integration branches.
3. **Never merge one long-lived branch into another to "sync" it.** Code moves between the
   trunk and maintenance branches only by cherry-pick (see [Backporting](#backporting)).
4. **Hide unfinished user-visible work.** Put it behind a flag or setting that defaults to
   off, or leave it unwired until the last PR. The trunk is releasable after every merge.
   When a change cannot be hidden (a rename, a data-directory move), prepare it behind the
   scenes and flip it in one final PR shortly before the release.
5. **A release is a tag on a branch.** Never assemble a release by picking commits after
   the fact.
6. **Agents never tag or publish** unless a maintainer explicitly approves that step.

## Models

### trunk-only

- Every release (major, minor, patch) is tagged from the trunk.
- A fix ships in the next release. No maintenance branches, no backports.
- Fits projects with a single supported version: most personal projects, services
  deployed from the trunk, anything without users pinned to an older line.

### release-branches

- Minor and major releases are tagged from the trunk. Each one creates `release/X.Y` from
  its tag, plus the `backport:X.Y` label.
- Patches (`X.Y.Z`, Z > 0) are tagged from `release/X.Y`.
- A fix that must reach a shipped minor lands on the trunk first, carries the
  `backport:X.Y` label, and is cherry-picked to `release/X.Y` in its own PR.
- Only branches inside the support window receive patches. Older ones are frozen.

Upgrading from trunk-only to release-branches is `/release-model --upgrade`; it needs
no history rewrite.

## Where does my change go?

| Change                                    | Target       | PR into       | Backport            |
| ----------------------------------------- | ------------ | ------------- | ------------------- |
| Bug users hit in the shipped version      | Patch        | Trunk         | Yes, `backport:X.Y` |
| Security fix                              | Patch        | Trunk         | Yes, `backport:X.Y` |
| Bug only in unreleased trunk code         | Next release | Trunk         | No                  |
| Feature, refactor, perf, docs, tests      | Next release | Trunk         | No                  |
| Bug in code the trunk has since rewritten | Patch        | `release/X.Y` | n/a — explain in PR |
| Dependency bump                           | Next release | Trunk         | Only security bumps |

Under trunk-only every row is "Trunk, no backport".

## Backporting

After the trunk PR is squash-merged:

```bash
git fetch origin
git switch -c backport/X.Y/<slug> origin/release/X.Y
git cherry-pick -x <squash-commit-sha-on-trunk>
```

Open the PR into `release/X.Y` with the original title and `Backport of #N` in the body,
then squash-merge it. Resolve conflicts minimally; if the fix needs real rework for the
older code, write it as its own PR against `release/X.Y`. `/backport` automates this.

### Before tagging a patch

A patch `vX.Y.Z` is tagged only when `release/X.Y` has every fix meant for it. Right before
the tag, `backport-audit.sh release/X.Y` (run by `/releaser` and `/backport --audit`)
checks two things:

1. **Labelled:** every trunk PR with `backport:X.Y` has a **merged** twin in
   `release/X.Y`: a PR whose body says `Backport of #N`, or a commit carrying the
   `cherry picked from commit <sha>` trailer. An open or closed-unmerged backport PR does
   not count.
2. **Unlabelled:** every fix PR (`fix:` title or `type:bug` label) merged into the trunk
   since the line's previous tag either has a twin or gets an explicit decision: label and
   backport it, or confirm that it only fixes trunk code. Labels are applied by hand, so
   this is what catches a fix nobody labelled.

Pass `--since vX.Y.0` to sweep the whole line, for example the first time a line is audited.

## State block

`RELEASING.md` carries a block that is invisible when rendered:

```markdown
<!-- ycc-release-state
model: release-branches
trunk: main
maintenance: release/0.5
frozen: release/0.4
support: latest-minor
backport_label: backport:{X.Y}
tracker: github-milestones
-->
```

- One `key: value` per line. Blank lines and lines starting with `#` are ignored.
- Required: `model` (`trunk-only` | `release-branches`) and `trunk`.
- Optional, with defaults: `maintenance` (empty), `frozen` (empty), `support`
  (`latest-minor`; also `latest-two-minors` or free text, which disables automatic
  freezing), `backport_label` (`backport:{X.Y}`, must contain `{X.Y}`), `tracker`
  (`none` | `github-milestones` | `github-labels` | `linear-labels`), `tracker_ref`
  (free text, such as a label-group name).
- `maintenance` and `frozen` are comma-separated `release/X.Y` lists, newest first.
- Malformed: an unknown or duplicate key, a missing required key, an invalid `model` or
  `tracker`, a list entry that is not `release/X.Y`, or `maintenance` under `trunk-only`.

The human "Current state" table sits between `<!-- ycc-release-state:table:begin -->` and
`<!-- ycc-release-state:table:end -->`. It is always rendered from the block by
`release-state-update.sh`; never edit it by hand. Prose after the end marker is
human-owned and never rewritten.

### Scripts

- `.claude/skills/_shared/scripts/release-state.sh get` prints the state as
  `key=value` (`present=0` when there is no file).
- `.claude/skills/_shared/scripts/release-state.sh base` prints the PR base
  for HEAD: the maintenance branch it was cut from, else the trunk.
- `.claude/skills/_shared/scripts/release-state.sh check` reports table drift
  and branches missing on origin.
- `.claude/skills/_shared/scripts/release-state-update.sh <op>` changes the
  state and re-renders the table. It never commits.

## Missing or malformed state

- **No `RELEASING.md`:** every skill behaves exactly as it did before this model existed.
  Skills that pick branches or cut releases add one line suggesting `/release-model`.
  `init`, `blueprint` and `releaser` offer to run it.
- **Malformed block** (`release-state.sh` exits 2): stop, show the parse error, and point
  at `/release-model --audit`. Never guess a trunk from a broken block.
- **A branch named in the state is missing on origin:** skills that need that branch
  (backport, a patch release) stop with the command that creates it; others continue.

## How skills use this

| Skill                       | Reads                            | Behavior                                                                                     |
| --------------------------- | -------------------------------- | -------------------------------------------------------------------------------------------- |
| `prepare-feature-branch.sh` | `trunk`                          | New branches start from `origin/<trunk>`                                                     |
| `git-workflow`, `prp-pr`    | `base`, `model`, `maintenance`   | PR base, sync-merge guard, backport prompt                                                   |
| `code-review`               | `base`                           | Flags a PR whose base contradicts the state                                                  |
| `releaser`                  | `model`, `trunk`, `maintenance`  | Checks the release branch; creates `release/X.Y` on a minor; audits backports before a patch |
| `backport`                  | `maintenance`, `backport_label`  | Cherry-picks labelled PRs to maintenance branches; `--audit` runs the pre-tag check          |
| `git-cleanup`               | `trunk`, `maintenance`, `frozen` | Protects active branches; flags retired ones and sync merges                                 |
| `init`, `blueprint`         | presence                         | Offer `release-model` when the file is missing                                           |
