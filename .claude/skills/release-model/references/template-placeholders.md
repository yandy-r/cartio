# RELEASING.md template placeholders

`templates/RELEASING.md.tmpl` uses the same syntax as `init` templates: `{{NAME}}`
placeholders and `{{#IF_X}}…{{/IF_X}}` conditional blocks on their own lines. The template
already contains empty table markers; after rendering, run `release-state-update.sh --render`
to fill the Current state table from the block.

## Conditional blocks

| Block                                               | Kept when                     |
| --------------------------------------------------- | ----------------------------- |
| `{{#IF_RELEASE_BRANCHES}}…{{/IF_RELEASE_BRANCHES}}` | `model` is `release-branches` |
| `{{#IF_TRUNK_ONLY}}…{{/IF_TRUNK_ONLY}}`             | `model` is `trunk-only`       |

Drop the marker lines of the kept block and the whole dropped block.

## Placeholders

| Placeholder           | Meaning                 | Source signal (`collect-signals.sh`)                        | Fallback                           |
| --------------------- | ----------------------- | ----------------------------------------------------------- | ---------------------------------- |
| `{{PROJECT_NAME}}`    | Human project name      | repo directory / manifest `name`                            | repository directory name          |
| `{{TRUNK}}`           | Trunk branch            | `default_branch`, confirmed by the user                     | `main`                             |
| `{{STATE_BLOCK}}`     | The state block         | `release-state-update.sh --print-block …` (block part only) | — (required)                       |
| `{{TAG_PATTERN}}`     | Tag naming              | `tag_pattern`                                               | `vX.Y.Z`                           |
| `{{VERSION_FILES}}`   | Files bumped on release | `version_files`, each in backticks                          | `none (tags only)`                 |
| `{{CHANGELOG_FILE}}`  | Changelog location      | `changelog`, in backticks                                   | `none yet`                         |
| `{{PUBLISH_STEP}}`    | What CI does on a tag   | `publish_workflow` ("`<path>` publishes on the tag.")       | `Nothing publishes automatically.` |
| `{{TRACKER_SECTION}}` | How targets are tracked | `tracker` / `tracker_ref`, see below                        | the `none` text                    |

## Tracker sections

- **github-milestones:** "Every issue gets a target release at triage as a GitHub milestone
  named after the version (`v0.6.0`). For bugs, triage also decides whether the fix needs a
  backport."
- **github-labels:** as above, with a `release:vX.Y.Z` label instead of a milestone.
- **linear-labels:** "Every issue gets a target release at triage as a label from the
  single-select **{tracker_ref}** label group (`v0.5.x` for the patch line, `v0.6.0` for the
  next minor). Add a label to the group when a new version is planned."
- **none:** "Targets are decided in the PR description: say which release a change is for
  and, for fixes, whether it needs a backport."

Drop the backport sentences under `trunk-only`.
