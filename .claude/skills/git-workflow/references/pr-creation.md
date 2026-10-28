# Phase 5 — Pull Request Creation (Steps 22–31)

## Contents

- Prerequisites
- Step 22a: detect an existing PR
- Step 22b: base, sync guard, backport
- Steps 23–24: template and context
- Step 25: PR title
- Step 26a: link Linear and GitHub issues
- Step 26: PR description
- Step 27: draft vs ready
- Step 28: create the PR (MCP, then CLI)
- Step 29: results and failures
- Step 30: post-PR summary
- Step 31: repository PR template check

## Prerequisites

Runs only when `pr ∈ actions`. Phase 0.5 guarantees `push` and `commit` are in the set, so commits exist and the branch was pushed in Step 12 or Step 21.

- Branch is pushed to the remote.
- GitHub MCP tools available (`mcp__github__*`, nothing more needed) OR `gh` installed and authenticated.

## Step 22a: Detect an existing PR

Before generating a description or running `create-pr.sh`:

```bash
EXISTING_PR=$(gh pr list --head "$CURRENT_BRANCH" --json number,url --jq '.[0]' 2>/dev/null || echo "")
```

With MCP, prefer `mcp__github__list_pull_requests` with `head=<branch>`.

- **None found**: continue to Step 23.
- **Found, `--ci ∈ flags`**: prompt

  ```
  PR #<num> already exists for this branch (<url>).
  Skip PR creation and run --ci against the existing PR? (yes/no):
  ```

  - `yes`: record `PR_NUMBER=<num>` and `PR_URL=<url>`, skip Steps 23–31 (do not call `create-pr.sh`), fall through to Phase 6.
  - `no`: exit cleanly with `PR already exists; --ci not run. Use 'gh pr view <num>' to inspect.` Do not run Phase 6.

- **Found, `--ci ∉ flags`**: stop with `PR already exists for this branch: #<num>. View: gh pr view <num>. Edit: gh pr edit <num>.`

`create-pr.sh` also detects an existing PR and exits 1 as a defensive fallback.

## Step 22b: Resolve base, guard sync merges, offer backport

Follow `.claude/skills/_shared/references/pr-base-and-backport.md` with `GUARD=.claude/skills/_shared/scripts/pr-guard.sh`:

1. `git fetch origin`, then `BASE=$(bash "$GUARD" base [--base <branch>])` — pass `--base` only when the user gave it. Use `$BASE` everywhere below.
2. `bash "$GUARD" sync-check --base "$BASE"` — exit 3: do not create the PR; show the reason, cite `branching-model.md#rules`, stop (Phase 6 does not run).
3. After the title is drafted (Step 25): `bash "$GUARD" backport-label --base "$BASE" --title "<title>"`; on `backport=1` ask `Backport to <branch>? (yes/no)` and remember the answer for Step 29.

Exit 2 from any call (malformed `RELEASING.md`) stops Phase 5 with the parse error and `/release-model --audit`.

## Steps 23–24: Template and context

Read `.claude/skills/git-workflow/templates/pr-template.md` (title guidelines, description structure, review checklist, testing guidance, when to use draft).

Gather context:

```bash
.claude/skills/git-workflow/scripts/create-pr.sh --analyze --base "$BASE"
```

Output: current branch, base (`$BASE`), commit history since divergence, changed-files summary, documentation changes, suggested title and scope.

**Forge detection**: `create-pr.sh` detects the provider on `origin` and routes through the matching CLI — `gh` for `github`, `tea` for `forgejo`/`gitea` (`gh pr create` ↔ `tea pull create`). The MCP-first path applies only to `github`; on forgejo/gitea the script drives `tea` directly. Algorithm and equivalence map: `.claude/skills/_shared/references/forge-detection.md`. Log the resolved provider and CLI so the user sees which forge the PR targeted.

## Step 25: PR title

Must follow `<type>(<scope>): <description>`.

- Single commit: use its (already validated) subject.
- Multiple related commits: write a descriptive title in the same format.

Validate with the same rules as commits; revise until it passes:

```bash
.claude/skills/git-workflow/scripts/validate-commit.sh "<pr-title>"
```

Examples:

```
feat(auth): implement JWT authentication system
fix(api): resolve multiple payment processing issues
docs: update API documentation and examples
refactor(database): migrate to new query builder pattern
```

## Step 26a: Link Linear and GitHub issues

Contract (collection, verification, keywords, skip message): `.claude/skills/_shared/references/artifact-store.md` § PR linking. Applies in both store modes.

1. Collect: `bash ".claude/skills/_shared/scripts/collect-linear-keys.sh" --base "origin/$BASE"` (no `--team` flag; it defaults to the configured team). Rows are `<KEY>\t<source>\t<closes|refs>`; it exits 0 even when empty.
2. Add the `linear.issue` from the leading frontmatter of the cached plan implemented on this branch as a `closes` candidate: use the cache path known in the session, else `.ycc/artifacts/<slug>/{parallel-plan,prp-plan}.md` where `<slug>` is the branch name without a leading `feat/`. Check the current checkout, then the main checkout (parent of `git rev-parse --path-format=absolute --git-common-dir`), per the contract's step 1.
3. Verify, then emit `Closes` for `closes` keys and `Refs` for `refs`-only keys, plus `Closes #<n>` for linked GitHub issues, under `## Related Issues`; never duplicate a line already present in the body or repository template (Step 31). Use the contract's skip message when no team or MCP can verify.

`create-pr.sh --create` applies the same team filter without MCP verification and keeps the bare `Closes #` placeholder when nothing qualifies.

## Step 26: PR description

```markdown
## Summary

[2-4 sentence overview of what this PR does and why]

## Changes

- [Key change 1 with context]
- [Key change 2 with context]
- [Key change 3 with context]

## Documentation

[If docs were created/updated]

- Feature docs: docs/features/[name].doc.md
- API docs: docs/api/[endpoint].md
- Architecture docs: docs/architecture/[topic].md

[If no docs]

- No documentation changes needed

## Testing

[For each feature/change]

### [Feature Name]

**How to test:**

1. Step one
2. Step two
3. Expected result

**Edge cases:**

- Edge case 1 to verify
- Edge case 2 to verify

## Related Issues

[If applicable]
Closes #123
Fixes #456
Related to #789

## Breaking Changes

[If any]
BREAKING CHANGE: [Description]

**Migration steps:**

1. Step one
2. Step two

[If none]

- No breaking changes

## Checklist

- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Comments added for complex logic
- [ ] Documentation updated
- [ ] Tests added/updated
- [ ] All tests passing
- [ ] No new warnings
```

## Step 27: Draft vs ready

Use a draft PR (`--draft`) when work is incomplete, early feedback is wanted, tests are not passing, documentation is incomplete, or breaking changes need discussion. Use a regular PR when work is complete and tested, ready for immediate review, checks pass, and docs are complete.

## Step 28: Create the PR

### GitHub MCP available (preferred)

`mcp__github__create_pull_request` with:

- `owner`, `repo`: from `git remote get-url origin`
- `title`: Step 25; `body`: Step 26
- `head`: current branch; `base`: `$BASE` (Step 22b)
- `draft`: `true` if `--draft`, else `false`

It returns the PR number and URL.

### MCP unavailable or failed — `gh` CLI

Regular:

```bash
gh pr create \
  --title "[PR Title]" \
  --base "$BASE" \
  --body "$(cat <<'EOF'
[PR Description from Step 26]
EOF
)" \
  --web
```

Draft: same command with `--draft` added before `--web`. `--web` opens the PR in the browser for final review.

## Step 29: Results

**Success**: capture PR number and URL; show the link; note draft vs ready; if the Step 22b backport prompt was answered yes, add the `backport:X.Y` label and print `After #<N> merges, run /backport <N>` (see the shared reference).

**Failures**:

1. No upstream branch: offer `git push -u origin [branch]`.
2. Branch not pushed: push automatically and retry.
3. `gh` not installed: use MCP if available; else give install instructions (macOS `brew install gh`; Linux see the GitHub CLI install docs).
4. Not authenticated: use MCP if available; else guide to `gh auth login`.
5. PR already exists: offer to update (`gh pr edit`) or view (`gh pr view`); a separate PR needs a new branch.
6. MCP tool failed: fall back to `gh` and retry.

Also: MCP detected but failing and `gh` failing too — check repository permissions, `gh auth status`, branch pushed, MCP server configuration. Draft PR to ready: `gh pr ready [PR-number]`.

## Step 30: Post-PR summary

```markdown
# Pull Request Created

## PR Details

**Number**: #[number]
**Title**: [PR title]
**URL**: [GitHub URL]
**Status**: [Draft/Ready for Review]
**Base**: [base-branch] <- [current-branch]

## Commits Included

- [hash]: [commit message]
- [hash]: [commit message]

## Files Changed

**Total**: [count] files

- Source: [count]
- Tests: [count]
- Docs: [count]

## Next Steps

1. PR is ready for review
2. View PR: [URL]
3. Request reviewers in GitHub
4. Monitor CI/CD checks
5. Address review feedback

[If draft]

## Draft PR Status

This PR is marked as draft. To mark ready for review:

    gh pr ready [PR number]

Complete these before marking ready:

- [ ] All tests passing
- [ ] Documentation complete
- [ ] Self-review done
```

## Step 31: Repository PR template check

If the repo has `.github/PULL_REQUEST_TEMPLATE.md`, read it, make the generated description follow its structure, and fill any template-specific sections.
