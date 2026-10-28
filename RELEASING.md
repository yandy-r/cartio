# Releasing Cartio

Cartio has not shipped a release yet. Until it does, everything lands on `main` and there are
no release branches, backports or tags to maintain.

## Model

| Setting | Value |
| -- | -- |
| Model | Trunk-based, pre-release |
| Trunk | `main` |
| Release branches | None yet. The first `release/X.Y` is cut only when a shipped version needs a patch line. |
| Versioning | SemVer, starting at `v0.1.0`. Until `v1.0.0`, a minor can break things. |
| Tags | None yet. A tag `vX.Y.Z` on `main` marks a release. |
| Release tracker | `linear-labels`: one label per release in the Linear group "cartio release" (team YAN) |
| Changelog | Generated at release time. Don't edit `CHANGELOG.md` in feature PRs. |
| Planning | Linear workspace yandy-r, team Yandy-r (YAN), project "Cartio -- v1" |

## Where does my change go?

1. **Pick the target release.** Every Linear issue carries one label from the "cartio release"
   group. New work targets the next planned version (today `cartio v0.1.0`).
2. **Code changes:** branch off `main` as `<area-or-type>/<issue-id>-<slug>`, for example
   `controller/YAN-1648-customers-engagements-sites`. Open a PR to `main` and squash-merge it on
   green CI.
3. **Docs-only changes** (README, ADR copies, this file's pointers) may be committed straight to
   `main`.
4. **Backports:** none while there are no release branches. Once `release/X.Y` exists, a fix for a
   patch-line target (`cartio vX.Y.x`) lands on `main` first, is labelled `backport:X.Y` and is
   cherry-picked to `release/X.Y` by a follow-up PR.

## Rules

- No direct or force pushes to `main` or `release/*` for code. Every code change goes through a PR.
- Never bypass checks: no `--no-verify`, no skipped or deleted tests, no admin merges on red CI.
- PRs say `Closes YAN-N` (and `Closes #N` when a GitHub issue exists) and name the target release.
- Security fixes: exploit details stay in Linear only, never in GitHub issues, PRs or commits.

## Unfinished work

`main` must stay releasable. Unfinished work that a user could reach either stays unwired (no URL,
no menu entry, no command) or sits behind a setting that is off by default, named
`CARTIO_ENABLE_<FEATURE>` and read in `cartio_controller/settings.py`. Remove the switch once the
feature ships.

## Cutting a release

1. Check that every issue labelled with the release is done or moved to the next release.
2. Tag `main` as `vX.Y.Z` and publish a GitHub release with generated notes.
3. Create the next release label in Linear and update "today" in rule 1 above.
