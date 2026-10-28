# Release Preparation: Preflight through Manifest Review

## Contents

- Phase 0: Preflight
- Phase 1: Detect project
- Phase 2: Propose version and compute bump
- Branch check
- Backport gate (patch under release-branches)
- Phase 3: Resolve release target matrix
- Phase 4: Draft changelog and release notes
- Internal vs. user-facing commits
- Phase 5: Bump version in manifests

## Phase 0: Preflight

Before anything else, verify:

1. Working directory is a git repo: `git rev-parse --show-toplevel` succeeds.
2. Working tree is clean: `git status --porcelain` is empty. If dirty, STOP and ask
   whether to stash or abort — never silently move forward with uncommitted changes.
3. `gh` is installed and authenticated when `--ci-config` is absent but the user
   asked for a GitHub release. Also required when `--ci` is set, since the
   auto-fix loop calls `gh run list`, `gh workflow run`, and `gh release` under
   the hood. Surface install/login hints if missing.
4. If `version` was provided, it matches the regex
   `^v?[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?(\+[A-Za-z0-9.-]+)?$`. Reject malformed
   input with a clear error.
5. Read the project's release model:

   ```
   .claude/skills/_shared/scripts/release-state.sh get
   ```

   - `present=0` (no `RELEASING.md`): say so once and offer to run `/release-model`
     first. If the user declines, continue exactly as this skill always has.
   - Exit 2 (malformed state block): STOP. Show the parse error and point at
     `/release-model --audit`. Never guess a trunk from a broken block.
   - `present=1`: keep `model` and `trunk`. The model's rules are in
     `.claude/skills/_shared/references/branching-model.md`; how this skill
     applies them is in
     `.claude/skills/releaser/references/release-branches.md`.

If any check fails, STOP and surface a specific remediation — do not continue past
Phase 0 with known-bad preconditions.

## Phase 1: Detect project

Run:

```
.claude/skills/releaser/scripts/detect-project.sh
```

The helper emits a single JSON document to stdout describing:

- `language` — primary language (`node`, `python`, `go`, `rust`, `docker`, `mixed`,
  `generic`).
- `build_system` — `npm` / `pnpm` / `yarn` / `poetry` / `uv` / `hatch` / `setuptools` /
  `cargo` / `go-modules` / `docker` / `none`.
- `manifest_files` — list of version-bearing files.
- `default_os` and `default_arch` — sensible defaults for that language.
- `existing_ci` — list of `.github/workflows/*.yml` files already present.
- `current_version` — extracted from the manifest, or `null` if not found.
- `latest_tag` — output of `git describe --tags --abbrev=0 2>/dev/null` or `null`.

If `--platform` was passed, override the detected `language` field before continuing.

`latest_tag` is the nearest tag reachable from `HEAD`. Under release-branches that is
the latest `vX.Y.*` tag on `release/X.Y`, but the previous minor's `.0` tag (or a
pre-release tag) on the trunk, because patch tags are not reachable from the trunk. See
"Previous tag and changelog range" in `references/release-branches.md` for when to pass
an explicit from-ref instead.

See `references/project-type-matrix.md` for the full language → toolchain → default
matrix.

## Phase 2: Propose version and compute bump

If the user did NOT supply a version:

1. Collect commits since `latest_tag` (or the last 50 if no tag exists):
   `git log <latest_tag>..HEAD --format="%s"`.
2. Classify each commit against conventional-commit types. Determine the bump:
   - Any `!` marker, `BREAKING CHANGE` footer, or `major:` → major.
   - Any `feat:` → minor.
   - Otherwise → patch.
3. Propose the next semver and ASK for confirmation before writing anything.

If the user DID supply a version, sanity-check the bump magnitude against commit
history. Surface a concern if they are mismatched (e.g. `feat:` commits but a patch
bump) and require explicit confirmation before continuing.

The release model (from `release-state.sh get`) decides how commit types map to the bump:

- **No RELEASING.md, or trunk-only:** the rules above apply as written. Every release is
  cut from the trunk, so `feat:` commits mean the next release is a minor; a patch
  requested over `feat:` commits gets the mismatch concern.
- **release-branches, on `release/X.Y`:** the branch bounds the bump to the next
  `X.Y.Z` patch. Everything on the branch is a deliberate backport, so a `feat:`
  cherry-pick is expected and does NOT raise the mismatch concern or make it a minor.
  Only a breaking change (`!` / `BREAKING CHANGE`) on the branch gets a warning: it
  should not have been backported. The patch is still the only valid version there.
- **release-branches, on the trunk:** propose only a minor or major. A fixes-only trunk
  still ships as the next minor; patches come from `release/X.Y`.

### Branch check

Once the version is confirmed, run:

```
.claude/skills/releaser/scripts/check-release-branch.sh <new-version>
```

- Exit 0: keep its `kind`, `branch`, `creates_maintenance`, `backport_label` and
  `forward_port` output for Phases 5, 7 and 8. With `present=0` it only prints a note
  on stderr that repeats Phase 0's; do not show it a second time.
- Exit 1: STOP. Show its stderr verbatim; it names the right branch and the exact fix
  commands (e.g. `git switch release/0.5`, or `git switch <trunk>`). Do not switch
  branches for the user.
- Exit 2: STOP with the parse error, as in Phase 0.

### Backport gate (patch under release-branches)

When the branch check reports `model=release-branches` and `kind=patch`, confirm that
every fix meant for the line reached it **before** writing any file:

```
.claude/skills/_shared/scripts/backport-audit.sh release/X.Y
```

Follow "Backport gate" in `references/release-branches.md`: `missing` and `in-review`
rows STOP the release, `unlabelled` rows need the user's decision, and a gate that cannot
run (`gh` unavailable) fails closed. Never skip it, and never answer its questions for the
user, including under `/goal` or `--dry-run` (which reports the result without asking).

## Phase 3: Resolve release target matrix

Build the final `{os × arch}` matrix:

1. Start with `default_os` / `default_arch` from Phase 1.
2. If `--os` or `--arch` was passed, replace the corresponding axis entirely.
3. Reject combinations with no known build recipe (e.g. `windows × arm` for languages
   where cross-compilation is unsupported) — list the invalid combos and ask whether to
   drop or abort.

Store the matrix for Phases 5, 6, and 7.

## Phase 4: Draft changelog and release notes

Unless `--skip-notes` was passed:

1. Invoke the changelog helper and capture its stdout:

   ```
   .claude/skills/releaser/scripts/draft-changelog.sh [--exclude-internal] [--template <path>] <new-version> [<from-ref>]
   ```

   Pass an explicit `<from-ref>` when the default (`git describe --tags --abbrev=0`)
   picks the wrong previous tag — on `release/X.Y`, or for a final `X.Y.0` after trunk
   pre-releases; see `references/release-branches.md`.
   Pass `--exclude-internal` if the user requested it. Pass `--template <path>` if the
   user supplied a custom template path. The helper fills the template placeholders
   (`{{VERSION}}`, `{{DATE}}`, `{{HIGHLIGHTS}}`, `{{BREAKING}}`, `{{FEATURES}}`,
   `{{FIXES}}`, `{{INTERNAL}}`, `{{COMMITS}}`, `{{COMPARE_URL}}`, `{{PREVIOUS_TAG}}`)
   from git history and writes the complete rendered document to stdout.

2. Write the captured stdout to `docs/releases/<new-version>.md` (or
   `.github/releases/<version>.md` if the repo already uses that path). This is the
   notes file that the `gh release create` command consumes.

3. Update `CHANGELOG.md` by prepending a slim version of the same content — Summary and
   per-category sections only, no `<details>` blocks — above any `## [Unreleased]`
   marker. If no `CHANGELOG.md` exists, create one using the template seeded from
   `.claude/skills/releaser/references/release-notes-template.md`.

Ask the user to review the drafted Summary and Upgrade Notes sections before moving on.

### Internal vs. user-facing commits

Commits typed `docs`, `chore`, `test`, `build`, `ci`, `style`, and `refactor` are
classified as Maintenance. The helper surfaces them in a collapsible `<details>` block
so they are visible but not dominant. Pass `--exclude-internal` to drop the Maintenance
section entirely — use this only when the release genuinely contains no internal-only
churn worth surfacing; otherwise keep the section so reviewers have full context.

## Phase 5: Bump version in manifests

Edit only the files named in `manifest_files` from Phase 1. Common targets:

- `package.json` → `version` field.
- `pyproject.toml` → `[project].version` or `[tool.poetry].version`.
- `Cargo.toml` → `[package].version`.
- `go.mod` does not carry a version; the tag IS the version. Skip the bump step.
- `Dockerfile` or `docker-compose.yml` labels → update if the repo uses
  `org.opencontainers.image.version`.

NEVER edit files outside that list — with one exception: when the Phase 2 branch check
printed a non-empty `creates_maintenance=release/X.Y`, also record the new maintenance
branch so the state change lands in the release commit:

```
.claude/skills/_shared/scripts/release-state-update.sh --add-maintenance release/X.Y
```

It edits only `RELEASING.md` (and may move a branch past the support window to
`frozen`); it never commits. Include `RELEASING.md` in the diff summary. Under
`--dry-run`, add `--dry-run` so it prints the diff instead of writing.

After edits, emit a diff summary and STOP for user review before continuing.
