# Vendored ycc skills

These skills and the agents in `../agents` are copied from the `ycc` plugin in
[yandy-r/ystack](https://github.com/yandy-r/ystack) (ycc 6.1.1, commit `b449460`), so cloud
sessions, which don't have the global plugin, can use them in Cartio.

Only the skills that fit Cartio are here:

| Area | Skills |
| -- | -- |
| Release | `release-model`, `backport`, `releaser` |
| Shipping | `git-workflow`, `prp-commit`, `prp-pr`, `pr-autofix` |
| Review | `code-review`, `quick-review`, `quick-fix`, `review-fix` |
| Python | `python-patterns`, `python-testing` |

`_shared` holds the references and scripts these skills load. The agents are the ones the skills
dispatch: code-reviewer, review-fixer, pr-comment-fixer, backport-conflict-resolver,
release-fix-applier, releaser, implementor, git-cleanup and test-strategy-planner.

## How they differ from the plugin

They were copied the way ycc's own installer makes standalone skills:

- `${CLAUDE_PLUGIN_ROOT}/skills/` became `.claude/skills/`, so scripts resolve from the repo root.
- The `ycc:` prefix was dropped. Run `/release-model`, not `/ycc:release-model`. A few skills
  still mention ycc skills that aren't vendored here; those references simply won't resolve.

Cloud sessions have no `gh` CLI, so steps that shell out to `gh` (mostly in `pr-autofix` and
`prp-pr`) need the GitHub tools instead.

## Refreshing

Don't edit these files by hand. To update, copy the same skills and agents from a newer ystack
`ycc/` tree, apply the two rewrites above, and bump the version and commit here.
