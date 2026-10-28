# Branch Prep Helper Contract

Use `prepare-feature-branch.sh` before any worktree setup or implementor-agent dispatch, except in dry-run or plan-only paths that do not touch git. Worktree setup adopts the prepared `feat/<slug>` branch when it exists.

```bash
FEATURE_BRANCH=$(bash .claude/skills/_shared/scripts/prepare-feature-branch.sh "${FEATURE_SLUG}")
```

The helper echoes the prepared branch name on success.

"Trunk" means `main`/`master`/`trunk`/`develop`, plus the `trunk` named in `RELEASING.md` when the repo has one (`branching-model.md` in this directory). Rows marked _(RELEASING.md)_ apply only when `release-state.sh get` reports `present=1`; without the file the helper behaves exactly as it did before the release model.

| Current State                                                      | Helper Behavior                                                                                                                                        |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| On `feat/<slug>`                                                   | Idempotent no-op; echoes branch and exits 0                                                                                                            |
| On a trunk, clean or plan-only dirty, branch exists                | `git checkout feat/<slug>`; echoes branch                                                                                                              |
| On a trunk, clean or plan-only dirty, branch missing               | `git checkout -b feat/<slug>` from HEAD; echoes branch                                                                                                 |
| _(RELEASING.md)_ On a trunk, branch missing                        | `git fetch origin <trunk>`, then `git checkout --no-track -b feat/<slug> origin/<trunk>` (the state trunk, even when the current trunk is another one) |
| _(RELEASING.md)_ Fetch fails, or plan-only dirty files block it    | Warns on stderr and falls back to `git checkout -b feat/<slug>` from HEAD                                                                              |
| _(RELEASING.md)_ On `release/*`                                    | Exits 2 asking whether this is a release-only fix; `--allow-existing-feature-branch` does not bypass it                                                |
| _(RELEASING.md)_ On `release/*` with `--allow-release-branch`      | Switches to an existing local `feat/<slug>`, else `git checkout -b feat/<slug>` from the release branch (no fetch)                                     |
| _(RELEASING.md)_ Malformed state block (`release-state.sh` exit 2) | Exits 1 with the parse error; fix it with `/release-model --audit`                                                                                 |
| On another feature branch                                          | Exits 2; re-run with `--allow-existing-feature-branch` after confirming with the user                                                                  |
| On trunk with unrelated dirty files                                | Exits 1; stop and ask the user to stash or commit first                                                                                                |

If the helper exits 2, surface the message to the user and ask:

- On a feature branch: whether to reuse it; on confirmation re-invoke with `--allow-existing-feature-branch`.
- On `release/*`: whether this is a release-only fix (a bug in code the trunk has since rewritten). If yes, re-invoke with `--allow-release-branch`; otherwise check out the trunk and re-run, and backport the fix after it merges.

If it exits 1, stop and show the error: the user cleans the tree, or fixes the state block, before agents are dispatched.
