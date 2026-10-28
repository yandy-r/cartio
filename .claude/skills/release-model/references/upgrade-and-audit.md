# Upgrade and audit

## Contents

- Upgrade
- Audit

Detailed procedures for `release-model --upgrade` and `--audit`. `$STATE`, `$UPDATE`,
`$SIGNALS`, and `$TEMPLATE` are the paths defined in `SKILL.md`.

## Upgrade

Moves an existing `trunk-only` `RELEASING.md` to `release-branches` without touching
human prose. Precondition: `$STATE get` exits 0 with `model=trunk-only`.

### 1. State

Copy the file to a scratch repo dir and change the model there:

```bash
WORK=$(mktemp -d)
cp "$ROOT/RELEASING.md" "$WORK/RELEASING.md"
$UPDATE --repo "$WORK" --set-model release-branches
```

This rewrites only the state block and the Current state table. `support`, `tracker`,
and `tracker_ref` keep their current values; the summary states the support window in
effect (`latest-minor` unless the block already said otherwise).

### 2. Reference renderings

Render the template twice into scratch files with identical inputs — the state's
`trunk`, `tracker`, `tracker_ref`, `support`, and the placeholders from a fresh
`$SIGNALS` run — once as `trunk-only` (`OLD`) and once as `release-branches` (`NEW`),
using the create-mode renderer (SKILL.md Phase 3, steps 2–4).

In the template every `{{#IF_RELEASE_BRANCHES}}` block that has a trunk-only
counterpart sits directly next to its `{{#IF_TRUNK_ONLY}}` block. Each such pair is one
**swap unit**: the rendered trunk-only fragment (`F_old`) and the rendered
release-branches fragment (`F_new`). Today the swap units are:

| Section                       | `F_old` (trunk-only)                                  | `F_new` (release-branches)                                   |
| ----------------------------- | ----------------------------------------------------- | ------------------------------------------------------------ |
| `## Rules`                    | rule 4, "Every release comes from …"                  | the cherry-pick sentence under rule 3 plus rule 4 "Fix on …" |
| `## Where does my change go?` | the "Every change targets the next release" paragraph | the decision table                                           |
| `## Releasing`                | `### Every release — from …` subsection               | the minor/major and patch subsections                        |

`## Backporting` is release-branches only (no `F_old`).

### 3. Merge, section by section

Split the working file into sections at level-2 headings (`##` plus a space at the start
of a line, ignoring lines inside fenced code blocks). Then:

1. **Current state** — already handled in step 1. Everything between the table end
   marker and the next level-2 heading is human-owned: leave it byte-for-byte.
2. **Sections with a swap unit** — if the whole section body equals the `OLD` rendering
   of that section, replace it with the `NEW` rendering. Otherwise, if `F_old` occurs
   exactly once, verbatim, in the section, replace that occurrence with `F_new` and leave
   every other line alone. Otherwise change nothing and record "update by hand" with the
   section name and `F_new`.
3. **`## Backporting`** — when the file has no `## Backporting` heading, insert the `NEW`
   section immediately before the `## Releasing` heading (or at the end of the file when
   there is none), with one blank line on each side.
4. **Every other section** — identical in both models or added by a human: untouched.

Apply the edits with the Edit tool on `$WORK/RELEASING.md`, then confirm
`$STATE --repo "$WORK" check` reports no table drift and `grep -n '{{' "$WORK/RELEASING.md"`
prints nothing.

### 4. Pointer sections and labels

- Pointer sections: for each file `$TARGETS --repo "$ROOT"` lists (`CLAUDE.md` is omitted
  when it imports `@AGENTS.md`), replace a `## Branching & releases` section with the
  release-branches text from `pointer-and-labels.md` only when it equals the trunk-only
  text byte-for-byte; otherwise record "update by hand".
- Labels doc: SKILL.md Phase 5.

### 5. Preview and write

Show `diff -u "$ROOT/RELEASING.md" "$WORK/RELEASING.md"` plus the other diffs, ask once
(skip with `--yes`), then `cp "$WORK/RELEASING.md" "$ROOT/RELEASING.md"` and write the
other files. Print the cut-over commands from SKILL.md Phase 7 for the latest tag's
`X.Y` and every "update by hand" item.

## Audit

Read-only. Gather: `$STATE --repo "$ROOT" get` (exit code included),
`$STATE --repo "$ROOT" check`, `$SIGNALS --repo "$ROOT"`, and — only when
`gh_releases` is not `unknown` — `gh label list --limit 500 --json name --jq '.[].name'`.

| #   | Check                                     | When                            | Severity | Suggested fix                                                                                                    |
| --- | ----------------------------------------- | ------------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------- |
| 1   | State block parses                        | always                          | error    | Fix the line named in the parse error (`branching-model.md#state-block`)                                         |
| 2   | Table matches the block                   | parsed                          | warn     | `$UPDATE --render`                                                                                               |
| 3   | Trunk and maintenance exist on origin     | parsed                          | error    | `git push origin <tag>^{commit}:refs/heads/<branch>` or remove it from the state                                 |
| 4   | Each maintenance `X.Y` has a tag          | release-branches                | warn     | Tag `vX.Y.0` was never cut: freeze the branch (`$UPDATE --freeze`) or tag it                                     |
| 5   | Newest minor tag has a maintenance branch | release-branches                | error    | Cut-over commands (SKILL.md Phase 7) for the newer `X.Y`, then `$UPDATE --add-maintenance`                       |
| 6   | Every `release/*` is in the state         | release-branches                | warn     | `$UPDATE --add-maintenance <branch>` or retire it (`/git-cleanup`)                                           |
| 7   | Trunk-only still fits                     | trunk-only                      | info     | `older_line_patched=1` or `release_branches` set → `/release-model --upgrade`                                |
| 8   | No long-lived branches                    | `long_lived_branches` set       | warn     | Land the work in small PRs behind a flag (`branching-model.md#rules`, rule 2)                                    |
| 9   | No sync merges on the trunk               | `sync_merges_recent > 0`        | warn     | List them with `git log --first-parent --merges -n 200 --format='%h %s' origin/<trunk>`; stop the habit (rule 3) |
| 10  | Backport labels exist                     | release-branches, gh usable     | warn     | `gh label create "<label>" --description "Cherry-pick to release/X.Y"`                                           |
| 11  | Trunk is the forge default branch         | parsed                          | warn     | Align the forge default branch or `$UPDATE --set-trunk <default_branch>`                                         |
| 12  | Pointer sections present                  | `CLAUDE.md` / `AGENTS.md` exist | info     | Add the section from `pointer-and-labels.md#pointer-section` to each file `$TARGETS` lists                       |
| 13  | Patch steps gate the tag on backports     | release-branches                | warn     | Insert step 0 of the template's patch section (`$TEMPLATE`, "Confirm every backport landed")                     |

Details:

- **Check 5:** compare the `X.Y` of `latest_tag` with the `X.Y` of `latest_maintenance`
  numerically; report when the tag's line is newer. Pre-release tags (`-beta.N`) do not
  count.
- **Check 9:** the count comes from the last 200 first-parent commits of the default
  branch, matching `Merge branch '<long-lived>' into …` or `sync … into …`.
- **Check 10:** the expected label for `release/X.Y` is `backport_label` with `{X.Y}`
  replaced. When `gh` is unusable, report "skipped: gh unavailable" instead of a finding.
- **Check 12:** run `$TARGETS --repo "$ROOT"` and check only those files for the
  pointer heading. A live `@AGENTS.md` import makes `AGENTS.md` the canonical target;
  absence of a heading in the importing `CLAUDE.md` is not drift. Report a dangling
  import as an error, not a satisfied pointer. Without the import, check every
  existing legacy target as before.
- **Check 13:** find the `### Patch release` heading in `## Releasing`. Report when it is
  missing or its steps (up to the next heading) mention neither `Backport of #` nor
  `backport --audit`: the file predates the pre-tag backport check
  (`branching-model.md#before-tagging-a-patch`). The fix renders step 0 from `$TEMPLATE`
  with the state's `trunk` and places it before step 1.
- A malformed block (check 1) skips checks 2–6, 10, 11, and 13; the signal-based checks
  still run.

Report as one table (`# | Severity | Finding | Fix`), errors first, then a one-line
verdict: "clean", or "N errors, M warnings". Change nothing, even with `--yes`.
