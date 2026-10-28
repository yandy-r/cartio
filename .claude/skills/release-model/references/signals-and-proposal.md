# Signals and proposal

How `release-model` turns `collect-signals.sh` output into a proposal. The script
header documents every key; this file covers how the skill uses them.

## Model suggestion

`collect-signals.sh` sets `suggested_model` and `suggested_reason` with this precedence:

| Condition                    | Suggestion         | Reason text (example)                                                                      |
| ---------------------------- | ------------------ | ------------------------------------------------------------------------------------------ |
| `older_line_patched=1`       | `release-branches` | "v0.5.3 was tagged after v0.6.0, so an older release line already receives patches."       |
| `release_branches` not empty | `release-branches` | "Maintenance branches already exist (release/0.5)."                                        |
| no semver tags               | `trunk-only`       | "No release tags yet, so every release can come from main."                                |
| semver tags, all in order    | `trunk-only`       | "All 15 tags were cut in order from one line up to v4.1.0, and no older line was patched." |

The skill shows the reason verbatim. It may add, never replace, context:

- `gh_releases` > 0 with `tags_count` far larger → many tags were never published.
- `publish_workflow` set → releases publish from CI on a tag; the Releasing section names it.
- `long_lived_branches` / `sync_merges_recent > 0` → advisory: the model forbids both
  (`branching-model.md#rules`, rules 2 and 3). They never change the suggestion.

A user may pick either model regardless of the suggestion. Choosing `trunk-only` while
`release_branches` exist is allowed; say that those branches stay untouched and are not
part of the state (audit will list them).

## Trunk

`--trunk` wins. Otherwise propose `default_branch`. If `ci_trigger_branches` does not
include the proposed trunk, mention it: CI may not run on pushes to the trunk.

## Support window

Release-branches only. `latest-minor` (default) keeps one maintenance branch;
`latest-two-minors` keeps two. Free text is allowed but disables automatic freezing
(`branching-model.md#state-block`). Trunk-only always writes the default.

## Tracker detection

Collect candidates:

| Candidate           | Evidence                                                                                      |
| ------------------- | --------------------------------------------------------------------------------------------- |
| `linear-labels`     | `linear_refs=1`                                                                               |
| `github-milestones` | `gh api "repos/{owner}/{repo}/milestones?state=all" --jq length` prints a number > 0          |
| `github-labels`     | `gh label list --limit 500 --json name --jq '.[].name'` lists a name starting with `release:` |

Skip the `gh` probes when `gh_releases=unknown` (gh unusable).

- **No candidate** → `tracker: none`. Not ambiguous.
- **One candidate** → use it. For `linear-labels`, `tracker_ref` is the label-group name;
  ask for it only as part of the tracker question, otherwise leave it empty.
- **Two or more** → ambiguous: ask (question budget permitting), listing the candidates
  plus `none`. With `--yes`, use `none` and say so in the summary.

## Question budget

At most three `AskUserQuestion` questions, in this order, each skipped when a flag or
`--yes` already fixes it:

1. Model — suggestion first, "(Recommended)".
2. Trunk — `default_branch` first.
3. Support window (release-branches) or tracker (only when ambiguous).

Under release-branches with an ambiguous tracker, the third slot goes to the tracker and
the support window defaults to `latest-minor`; the proposal says so, so the user can
override it in the free-text answer.
