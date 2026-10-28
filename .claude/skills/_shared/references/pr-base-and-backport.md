# PR Base, Sync Guard and Backport Prompt

## Contents

- PR base
- Sync-merge guard
- Backport prompt
- Checking an existing PR
- Missing or malformed state

How `git-workflow` (Phase 5) and `prp-pr` pick a PR's base branch, refuse sync
merges, and offer a backport under the release model. `code-review` uses
[Checking an existing PR](#checking-an-existing-pr). The rules themselves live in
`branching-model.md` in this directory; this file only says how skills apply them.

One helper implements all three steps so both PR skills behave the same:

```bash
GUARD=".claude/skills/_shared/scripts/pr-guard.sh"
```

| Exit | Meaning                                                               |
| ---- | --------------------------------------------------------------------- |
| 0    | Success (`sync-check`: `result=ok` or `result=skipped`)               |
| 1    | Usage error, or a ref the check needs is missing (fetch it and retry) |
| 2    | `RELEASING.md` state block is malformed — stop (see below)            |
| 3    | `sync-check` refused the PR                                           |

Run `git fetch origin` before these steps so `origin/<base>`, `origin/<trunk>` and
`origin/release/*` are current.

## PR base

```bash
BASE=$(bash "$GUARD" base)                 # no explicit base
BASE=$(bash "$GUARD" base --base develop)  # explicit base always wins
```

1. An explicit base (`git-workflow --base <branch>`, `prp-pr <base-branch>`) is used as is.
2. With `RELEASING.md`: `release-state.sh base` — the maintenance branch whose own
   commits HEAD contains (HEAD was cut from `origin/release/X.Y`), else the state trunk.
   A `release/X.Y` with no commits of its own yet cannot be told apart from the trunk; pass
   the base explicitly for a release-only fix made right after the cut.
3. Without `RELEASING.md`: the forge default branch (`forge_default_branch`: the forge API,
   then `origin/HEAD`, then `main`) — the same answer the skills used before the model.

Show the resolved base in the PR summary (`<head> → <base>`).

## Sync-merge guard

```bash
bash "$GUARD" sync-check --base "$BASE"
```

Skipped (`result=skipped`, exit 0) without `RELEASING.md`. Otherwise a **long-lived
branch** is the state `trunk` or any `release/*` branch, and the check refuses (exit 3)
when either holds:

- **Long-lived head:** the head branch (`git branch --show-current`) is long-lived, the
  base is long-lived, and they differ — for example a PR from `release/0.5` into `main`.
- **Sync merge in the commits:** for each merge commit `M` in
  `git rev-list --merges origin/<base>..HEAD`, with second parent `P = git rev-parse M^2`:
  - if `git merge-base --is-ancestor P origin/<base>` succeeds, `M` only merged the base
    back in — allowed;
  - otherwise, if `git merge-base --is-ancestor P <tip>` succeeds for any tip in
    `origin/<trunk>` plus `git for-each-ref --format='%(refname:short)' 'refs/remotes/origin/release/*'`
    (the base's own tip excluded), `M` brings another long-lived branch into this PR —
    refused.

Merging another topic branch passes. Backport PRs pass: they are cherry-picks
(`git cherry-pick -x`) and contain no merge commits.

On refusal, do **not** create the PR. Show the helper's `reason=` line, cite
`branching-model.md` ("Rules") (rule 3: never merge one long-lived
branch into another), and suggest the fix: rebase the topic branch onto `origin/<base>`
without the merge, or move a fix between the trunk and a maintenance branch with
`/backport`. A refusal also skips any `--ci` loop, since no PR was created.

## Backport prompt

```bash
bash "$GUARD" backport-label --base "$BASE" --title "$PR_TITLE" [--labels "type:bug,area:x"]
```

It prints `backport=0` with a `reason=` line, or `backport=1` with `branch=` and `label=`.
It answers `backport=1` only when all of these hold:

- `RELEASING.md` exists with `model: release-branches` and a non-empty `maintenance` list;
- the base is the state trunk (a PR into `release/X.Y` is already the release-only fix);
- the change is a fix: the PR title matches `^fix(\([^)]*\))?!?:`, a commit subject in
  `origin/<base>..HEAD` does, or the PR carries the `type:bug` label.

`branch` is `latest_maintenance`; `label` is `backport_label` with `{X.Y}` replaced (for
example `backport:0.5`).

When `backport=1`, ask before creating the PR:

```
Backport to <branch>? (yes/no)
```

On **yes**, after the PR exists:

1. Add the label. GitHub: `gh pr edit <N> --add-label <label>` (or
   `gh api -X POST repos/{owner}/{repo}/issues/<N>/labels -f 'labels[]=<label>'`, or the
   MCP equivalent). Forgejo/Gitea: add it in the web UI. If the label does not exist on the
   repo, say so and offer `gh label create <label> --description "Backport to <branch>"`;
   `releaser` normally creates it with the minor release.
2. Tell the user: `After #<N> merges, run /backport <N> to open the <branch> PR.`

On **no**, create the PR without the label. Never run the backport before the merge.

## Checking an existing PR

`code-review` PR mode compares the PR's `baseRefName` with the base the state
expects for its head:

```bash
git fetch origin "<headRefName>"   # a fork PR: git fetch origin "pull/<N>/head"
bash ".claude/skills/_shared/scripts/release-state.sh" get   # present=0 → skip
EXPECTED=$(bash ".claude/skills/_shared/scripts/release-state.sh" base --head FETCH_HEAD)
```

When `EXPECTED` differs from `baseRefName`, report a MEDIUM finding naming both branches
and citing `branching-model.md` ("Where does my change go").
`release-state.sh base` cannot recognize a head cut from a `release/X.Y` that has no
commits of its own yet, so when the PR targets `release/X.Y` and `EXPECTED` is the trunk,
say in the finding that the base is correct if this is a release-only fix. A malformed
block (exit 2) skips the comparison; mention the parse error and `/release-model --audit`
in the review summary instead.

## Missing or malformed state

Follows `branching-model.md` ("Missing or malformed state"):

- **No `RELEASING.md`:** base = forge default branch, the guard is skipped, no backport
  prompt. Add one line to the summary suggesting `/release-model` to record the
  branching model.
- **Malformed block** (exit 2 from any subcommand): stop before creating the PR, show the
  parse error, and point at `/release-model --audit`. Never guess a base from a broken
  block; an explicit base does not bypass this, because the guard still reads the state.
- **A branch named in the state is missing on `origin`:** `base` falls back to the trunk;
  mention the missing branch and continue.
