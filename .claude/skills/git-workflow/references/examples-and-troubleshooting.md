# Quality checklists, examples, troubleshooting

## Contents

- Quality checklists
- Examples
- Troubleshooting

## Quality checklists

**Commit message**: conventional format; appropriate type; imperative subject; subject ≤50 chars; no trailing period; body explains what and why (if needed); breaking changes in footer (if applicable).

**Documentation**: feature docs for new features; CLAUDE.md only if critical and directory-specific; architecture docs for system-wide changes; API docs for public API changes; concise and actionable; none for trivial changes.

**Agent deployment**: all agents in one message; clear non-overlapping scopes; file list in each prompt; conventional commits; each agent commits separately; no agent pushes (the main skill pushes).

**Pull request**: conventional title; summary of changes; clear testing instructions; documentation noted; breaking changes marked; related issues referenced; draft vs ready appropriate; all commits conventional.

**Overall**: all changes committed (nothing left unstaged); commits atomic and focused; docs match scope; push successful (if requested); summary provided.

## Examples

1. **Small change** — 1 file, 10 lines, `/git-workflow --commit`: message `fix(auth): prevent null email validation bypass`; `git add src/services/auth.ts && git commit -m "..."`.
2. **Large change** — 8 files, 450 lines: 3 agents (auth backend, auth frontend, auth tests), 3 commits, feature docs created.
3. **Docs-only** — 2 doc files, `--commit`: direct commit `docs(api): update authentication endpoint examples`; no docs agents.
4. **Full workflow with PR** — `/git-workflow --pr` (implies `--push` and `--commit`), 12 files: analyze, deploy agents (payment backend, frontend, tests), each commits with docs, push, create PR; 3 commits, feature docs, PR URL displayed.
5. **Draft PR** — `/git-workflow --pr --draft` (`--draft` is not an action flag): commit, push, create draft PR; later `gh pr ready [number]`.
6. **Push, no PR** — `/git-workflow --push`: commit and document, push, stop; create the PR later with `--pr` (uses existing commits).
7. **No flags** — `/git-workflow`: renders the menu; reply `2` resolves `{commit, push}`; flow analyze → commit → push → summary.
8. **CI monitoring**:
   - A: PR already open, add a commit and watch — `--push --ci`: commit+push, Phase 5 skipped (`pr ∉ actions`), Phase 6 discovers the PR via `gh pr list --head <branch>`, auth prompt, bounded loop.
   - B: nothing new to push — `--ci` (bare): Phases 3, 4, 5 skip (actions empty), Phase 6 discovers the PR and monitors already-pushed commits.
   - C: `--pr --ci` with an existing PR: commit and push, Step 22a prompts "PR #N already exists. Skip PR creation and run --ci against it? (yes/no)"; `yes` skips `create-pr.sh` and runs Phase 6 on PR #N; `no` exits cleanly with no monitoring.

## Troubleshooting

- **Validation script fails**: check commit format, valid type, subject ≤50 chars.
- **Too many agents suggested**: group related changes, combine scopes, or commit sequentially.
- **Unclear what changed**: `git diff`, `git log`, ask the user.
- **Conflicts between agent commits**: should not occur with scope isolation; resolve manually and recommit.
- **Documentation decision unclear**: skip docs for minor changes, create them for substantial features.
- **MCP detected but GitHub operation fails**: fall back to `gh`. If both fail, check repository permissions, `gh auth status`, branch pushed, MCP server configuration.
- **PR creation: `gh` not installed**: use MCP if available; else install (macOS `brew install gh`; Linux per the GitHub CLI install docs).
- **PR creation: not authenticated**: `gh auth login`.
- **PR creation: no upstream branch**: `git push -u origin [branch-name]`, then retry `--pr`.
- **PR already exists**: `gh pr view`, or create a new branch (`git checkout -b [new-branch-name]`) for a separate PR.
- **Draft to ready**: `gh pr ready [PR-number]`.
