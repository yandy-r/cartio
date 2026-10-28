# CI Release-Pipeline Optimization Checklist

## Contents

- 1. Triggering
- 2. Permissions (Supply-Chain Hardening)
- 3. Action Pinning
- 4. Caching
- 5. Build Matrix
- 6. Artifacts & Provenance
- 7. Release Notes
- 8. Error Handling
- 9. Documentation
- 10. Observability
- Generator Quality Gate
- Compatibility with `--ci` (Phase 8.5 auto-fix loop)

Audit criteria used by the `releaser` agent in `--ci-config=audit` mode, and the
default quality gate that generated workflows must meet in `--ci-config=generate`
mode. Each item is categorized by severity.

Findings emitted by the audit agent use this format:

```
### <Short title>
- **Severity:** critical | high | medium | low
- **File:** <path>:<line-range>
- **Finding:** <what's wrong>
- **Fix:** <specific remediation, with a snippet if short>
```

---

## 1. Triggering

| Check                                                                                                                                                                                                                                                            | Severity |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| Workflow triggers on `push: tags: ['v*']` or an equivalent explicit event                                                                                                                                                                                        | high     |
| Does NOT trigger on every push to `main` or `release/**` (runs only on tag/release)                                                                                                                                                                              | high     |
| `workflow_dispatch` is included for manual re-runs (REQUIRED if the project uses `/releaser --ci` non-destructively; the auto-fix loop re-triggers via `gh workflow run`)                                                                                    | high     |
| Concurrency group set (prevents duplicate releases racing)                                                                                                                                                                                                       | medium   |
| Under the release-branches model (`RELEASING.md`), triggers also cover `release/X.Y`: tag triggers are branch-agnostic, but any `branches:` filter on a workflow the release depends on (tests, build, `needs:` gates) includes `release/**`, not only the trunk | high     |

## 2. Permissions (Supply-Chain Hardening)

| Check                                                                       | Severity |
| --------------------------------------------------------------------------- | -------- |
| Top-level `permissions:` is `{}` (read-all), scoped up per-job              | critical |
| Jobs that publish to GHCR use `packages: write` only in that job            | critical |
| Jobs that upload release assets use `contents: write` only in that job      | critical |
| OIDC-based publishing where supported (PyPI Trusted Publishing, cargo-dist) | high     |
| No long-lived API tokens committed as plain secrets when OIDC is an option  | high     |

## 3. Action Pinning

| Check                                                                | Severity |
| -------------------------------------------------------------------- | -------- |
| Third-party actions pinned to full SHA (not `@v2` or `@main`)        | critical |
| First-party (`actions/*`) actions pinned to a release tag at minimum | high     |
| Dependabot or equivalent is enabled for action updates               | medium   |

## 4. Caching

| Check                                                                                                                                                                                          | Severity |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| Language-specific setup actions use their built-in cache (`setup-node` cache input, `setup-python` cache input, cargo cache via `Swatinem/rust-cache`, Go module cache via `actions/setup-go`) | high     |
| Cache keys include lockfile hashes, not just OS                                                                                                                                                | medium   |
| Release artifacts use `actions/upload-artifact@v4` (v3 is deprecated)                                                                                                                          | high     |

## 5. Build Matrix

| Check                                                                                                                    | Severity |
| ------------------------------------------------------------------------------------------------------------------------ | -------- |
| Matrix covers the declared `{os × arch}` set from the skill's arguments                                                  | high     |
| `fail-fast: false` unless the project explicitly wants all-or-nothing                                                    | medium   |
| `timeout-minutes` is set on each job (prevents hung workers eating budget)                                               | medium   |
| Cross-compilation uses the language's idiomatic tool (goreleaser / cargo-dist / cibuildwheel) rather than ad-hoc scripts | medium   |

## 6. Artifacts & Provenance

| Check                                                            | Severity |
| ---------------------------------------------------------------- | -------- |
| Each artifact is named `<project>-<version>-<os>-<arch>.<ext>`   | medium   |
| SHA256 checksums are generated and uploaded alongside binaries   | high     |
| SBOM is attached for container images                            | high     |
| SLSA provenance attestation is generated for published artifacts | medium   |
| Docker images are signed with cosign (keyless/OIDC)              | medium   |

## 7. Release Notes

| Check                                                                        | Severity |
| ---------------------------------------------------------------------------- | -------- |
| Release body is sourced from a committed notes file (not inline in YAML)     | medium   |
| `gh release create` is invoked with `--notes-file`, not generated by default | medium   |
| Pre-release tags (`-rc.N`, `-beta.N`, `-alpha.N`) set `prerelease: true`     | high     |

## 8. Error Handling

| Check                                                                 | Severity |
| --------------------------------------------------------------------- | -------- |
| Failed builds do NOT create the GitHub release (use `needs:` to gate) | critical |
| Rollback path is documented in the workflow comment header            | medium   |
| Notification step on failure (Slack/email/issue comment) is present   | low      |

## 9. Documentation

| Check                                                                                                                                                | Severity |
| ---------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| `.github/workflows/README.md` (or equivalent) documents: trigger, required secrets, local reproduction steps                                         | high     |
| Release runbook exists: repo-root `RELEASING.md` (preferred; `release-model` generates it), or `docs/releasing.md` / `CONTRIBUTING.md#releasing` | medium   |
| Changelog file is updated as part of the release commit (not only the GitHub release body)                                                           | medium   |

## 10. Observability

| Check                                                                                    | Severity |
| ---------------------------------------------------------------------------------------- | -------- |
| Workflow emits a job summary via `$GITHUB_STEP_SUMMARY` (final artifact list, checksums) | low      |
| Long-running steps have `id:` values for downstream `outputs` references                 | low      |

---

## Generator Quality Gate

When `--ci-config=generate` writes a new workflow, it MUST satisfy:

- Every **critical** item above.
- Every **high** item above.
- At least one section on Documentation (item 9) — the skill always writes a
  `.github/workflows/README.md` entry when generating a new workflow.

Medium/low items are opportunistically included but not required for the initial
scaffold. They appear as inline `# TODO(releaser):` comments in the generated YAML so
a later `--ci-config=audit` run can surface them explicitly.

---

## Compatibility with `--ci` (Phase 8.5 auto-fix loop)

The release-CI auto-fix loop driven by `/releaser --ci` needs a non-publishing
validation path after a fix lands. It dispatches `gh workflow run <file> --ref
<fix-bearing release branch>` and monitors the exact run ID and expected commit
SHA. Require `workflow_dispatch:` and verify branch-ref checkout tests that
commit without modifying published releases, artifacts or tags. The audit agent
flags missing or unsafe dispatch support as **high**.

Without that safe dispatch path, the destructive re-cut fallback remains available
only with explicit `--ci-recut=destructive` and the original gates: delete the
release with tag cleanup, then re-publish through `publish-release.sh --confirm`.
Without that opt-in, stop with `loop-blocked`; validate manually.
