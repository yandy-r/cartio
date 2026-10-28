# Release Workflow Generation and Audit

## Phase 6: CI workflow file (optional)

This phase generates or audits the **release.yml** file itself. It is separate from
the **Phase 8.5 release-CI auto-fix loop** (which watches a live workflow run after
publish). Three modes:

### --ci-config=generate

Fails if any file in `existing_ci` looks release-related (matches
`release|publish|deploy`). Otherwise, write a new `.github/workflows/release.yml` by
copying the matching template:

- Node → `references/ci-templates/node-release.yml`
- Python → `references/ci-templates/python-release.yml`
- Go → `references/ci-templates/go-release.yml` (uses goreleaser)
- Rust → `references/ci-templates/rust-release.yml` (uses cargo-dist if configured,
  else actions-rs matrix)
- Docker → `references/ci-templates/docker-release.yml`
- Generic → `references/ci-templates/generic-release.yml` (tag-triggered, uploads
  artifacts via `softprops/action-gh-release`)

Substitute the Phase 3 matrix and project name into the template. Also emit a short
`.github/workflows/README.md` entry documenting the new workflow — required (see
`references/ci-optimization-checklist.md`, "Documentation" section).

### --ci-config=audit

Delegate to the `releaser` agent. Pass the list of existing workflow files, the
detected language and build system, the expected `{os × arch}` matrix, and the full
contents of `references/ci-optimization-checklist.md` inline in the dispatch prompt
(including every numbered section and severity table, not a path or summary).
The agent returns a structured report (Findings → Severity → Fix). The skill
summarizes the top items and writes the full report to
`docs/prps/reviews/ci-release-audit.md` for later consumption by `review-fix`.

### No --ci-config flag

Skip Phase 6 entirely. Print a one-line reminder: "Skipped CI workflow generation.
Run with `--ci-config=generate` or `--ci-config=audit` to include it. (This is
separate from `--ci`, which monitors a live release workflow after publish.)"
