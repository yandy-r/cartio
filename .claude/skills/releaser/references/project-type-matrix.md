# Project Type Matrix

## Contents

- Node / TypeScript
- Python
- Go
- Rust
- Docker / Container Images
- Mixed / Monorepo
- Generic (fallback)

Reference for `detect-project.sh` output and for Phase 5 manifest edits. Each row
lists the detection signals, the manifest file(s) that carry the version string, the
default OS/arch matrix for release artifacts, and the recommended CI generator template.

---

## Node / TypeScript

| Field          | Value                                                                                                           |
| -------------- | --------------------------------------------------------------------------------------------------------------- |
| Detect signals | `package.json` at repo root                                                                                     |
| Build systems  | `npm` (default), `pnpm` (if `pnpm-lock.yaml`), `yarn` (if `yarn.lock`), `bun` (if `bun.lockb`)                  |
| Version file   | `package.json` → `version`                                                                                      |
| Default OS     | `linux,darwin,windows` (when packaging binaries via `pkg`/`nexe`); else `linux` only for pure library publishes |
| Default arch   | `amd64,arm64`                                                                                                   |
| CI template    | `ci-templates/node-release.yml`                                                                                 |
| Publish target | npm registry (`npm publish`) and/or GitHub Release assets                                                       |

Notes:

- For **library** publishes, the matrix is irrelevant — release from one runner.
- For **CLI tool** publishes with native binaries, cross-compile with `pkg` or
  `@vercel/ncc` and upload per-platform archives.
- Respect `publishConfig.registry` when present; do not swap to the default registry.

---

## Python

| Field          | Value                                                                                                                |
| -------------- | -------------------------------------------------------------------------------------------------------------------- |
| Detect signals | `pyproject.toml` or `setup.py` or `setup.cfg`                                                                        |
| Build systems  | `uv` (if `uv.lock`), `poetry` (if `[tool.poetry]`), `hatch` (if `[tool.hatch]`), `setuptools` (fallback)             |
| Version file   | `pyproject.toml` → `[project].version` (PEP 621) or `[tool.poetry].version`; `setup.py` / `setup.cfg` as last resort |
| Default OS     | `linux` (sdist + wheels) — `darwin`, `windows` only when building extensions                                         |
| Default arch   | `amd64` — add `arm64` for macOS if `cibuildwheel` is configured                                                      |
| CI template    | `ci-templates/python-release.yml`                                                                                    |
| Publish target | PyPI (`twine upload`) or TestPyPI — prefer Trusted Publishing (OIDC) over API tokens                                 |

Notes:

- For packages with compiled extensions, the template pulls in `cibuildwheel`.
- If the repo uses a dynamic version source (`hatch-vcs`, `setuptools-scm`), skip the
  manifest edit in Phase 5 — the tag IS the version.

---

## Go

| Field          | Value                                                      |
| -------------- | ---------------------------------------------------------- |
| Detect signals | `go.mod` at repo root                                      |
| Build systems  | Go modules; `goreleaser` (if `.goreleaser.yml` exists)     |
| Version file   | None — tag is the version. Phase 5 is skipped.             |
| Default OS     | `linux,darwin,windows`                                     |
| Default arch   | `amd64,arm64`                                              |
| CI template    | `ci-templates/go-release.yml` (uses goreleaser by default) |
| Publish target | GitHub Release assets; pkg.go.dev picks up the tag         |

Notes:

- `goreleaser` is the idiomatic choice. If the repo already has `.goreleaser.yml`, the
  template references it; otherwise the template seeds a starter config.
- For libraries, the release IS the tag — no artifacts required.

---

## Rust

| Field          | Value                                                                                |
| -------------- | ------------------------------------------------------------------------------------ |
| Detect signals | `Cargo.toml` at repo root                                                            |
| Build systems  | `cargo`; `cargo-dist` (if `[workspace.metadata.dist]` or `dist-workspace.toml`)      |
| Version file   | `Cargo.toml` → `[package].version` (or `[workspace.package].version` for workspaces) |
| Default OS     | `linux,darwin,windows`                                                               |
| Default arch   | `amd64,arm64`                                                                        |
| CI template    | `ci-templates/rust-release.yml`                                                      |
| Publish target | crates.io (`cargo publish`) and/or GitHub Release assets                             |

Notes:

- Prefer `cargo-dist` when available — it handles the matrix, installers, and
  homebrew/scoop formulae.
- For workspace crates, bump each published crate and tag `<crate>-v<version>`
  individually unless the repo uses a workspace-wide version.

---

## Docker / Container Images

| Field          | Value                                                            |
| -------------- | ---------------------------------------------------------------- |
| Detect signals | `Dockerfile` at repo root AND no other language manifests        |
| Build systems  | `docker buildx`                                                  |
| Version file   | Dockerfile label `org.opencontainers.image.version` (if present) |
| Default OS     | `linux` only                                                     |
| Default arch   | `amd64,arm64` (multi-arch manifest)                              |
| CI template    | `ci-templates/docker-release.yml` (ghcr.io by default)           |
| Publish target | GitHub Container Registry (`ghcr.io/<owner>/<repo>`)             |

Notes:

- Always build with `--provenance=true` and `--sbom=true`.
- Tag the image with both `v<semver>` and `latest` unless the project opts out.

---

## Mixed / Monorepo

If multiple manifests are present (e.g. `package.json` + `pyproject.toml`), the
detector reports `language: "mixed"` and lists all manifests. The skill requires
`--platform=<name>` to disambiguate, or operates in a per-package mode when given an
explicit subdirectory via `--platform=generic` plus a pre-selected manifest list.

---

## Generic (fallback)

| Field          | Value                                                       |
| -------------- | ----------------------------------------------------------- |
| Detect signals | None of the above match                                     |
| Build systems  | User-provided or none                                       |
| Version file   | `VERSION` file if present, else tag-only                    |
| Default OS     | `linux` only                                                |
| Default arch   | `amd64`                                                     |
| CI template    | `ci-templates/generic-release.yml` (tag-triggered uploader) |
| Publish target | GitHub Release assets                                       |

Notes:

- The generic template triggers on `push: tags: ['v*']` and expects the user to wire in
  their own build steps. The template calls out the TODO regions clearly.
