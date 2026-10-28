# Phases 0–4 — Analysis, Documentation, Commit, Summary

## Contents

- Phase 0: analyze changes
- Phase 1: strategy and dry-run report
- Phase 2: documentation decision
- Phase 3a: direct commit
- Phase 3b: agent deployment
- Phase 4: summary and push

## Phase 0: Analyze changes

Step 1 — state:

```bash
git status --porcelain
git diff --stat
git diff --cached --stat
```

Determine staged vs unstaged, total files, file types (source, test, docs, config), lines added/removed.

Step 2 — structured analysis:

```bash
.claude/skills/git-workflow/scripts/analyze-changes.sh
```

Gives change categorization, file count and metrics, recommended strategy (direct vs agents), scope suggestions.

Step 3 — for each modified file: which feature or fix, single or multiple features, whether docs are warranted. Group related changes for commits.

## Phase 1: Strategy

Step 4 — decision tree:

```
Small change? (1-3 files, single feature, minor)
├─ YES → Direct Commit (Phase 3a): write message yourself, stage+commit in one operation, no agents
└─ NO (4+ files, multiple features, substantial)
   → Agent Deployment (Phase 3b): parallel docs-git-committer agents, one per feature/significant change, each does docs + commit
```

Small: single feature, <4 files, focused scope, minimal docs needs, bug fix or minor improvement. Large: multiple features, 4+ files with substantial changes, cross-cutting changes, new features needing docs, breaking changes or API modifications.

Step 5 — `--dry-run`: display this, then STOP (no changes):

```markdown
# Dry Run: Git Workflow Analysis

## Changes Detected

**Files Changed**: [count]
**Insertions**: [count]
**Deletions**: [count]

### By Type

- Source files: [count]
- Test files: [count]
- Documentation: [count]
- Configuration: [count]

## Recommended Strategy

**Approach**: [Direct Commit / Agent Deployment]

**Reasoning**: [why this strategy was chosen]

## Proposed Actions

[For Direct Commit]

- Commit message: [proposed message]
- Documentation: [yes/no] - [reasoning]

[For Agent Deployment]

- Agent 1: [feature/scope] - docs + commit
- Agent 2: [feature/scope] - docs + commit
- Total agents: [count]

## Next Steps

Remove --dry-run flag to execute the workflow.
```

## Phase 2: Documentation decision

Step 6 — read `.claude/skills/git-workflow/templates/documentation-decision.md` (when feature docs are needed, when CLAUDE.md updates are appropriate (rarely), when architecture/API docs apply, what not to document).

Step 7 — per changed file or feature:

- **Feature docs** (`docs/features/`): new user-facing features, significant API changes, complex data flows, breaking changes. NOT internal refactoring, bug fixes (unless behavior changes), style/formatting, minor performance work.
- **CLAUDE.md updates** (rarely): new critical patterns specific to a directory, security-boundary changes within a directory, major architectural decisions affecting a directory. NOT root CLAUDE.md (never update root), NOT feature-specific details (feature docs), NOT most changes.
- **Skip docs if**: `--no-docs`, purely internal refactoring, only tests modified, only docs modified, trivial or obvious changes.

## Phase 3a: Direct commit

Step 8 — read `.claude/skills/git-workflow/templates/commit-types.md`.

Step 9 — craft `<type>(<scope>): <subject>`: type from the valid list; scope optional; subject imperative, no period, max 50 chars; body optional, wrap at 72, what and why; footer optional (breaking changes, issue refs). Example:

```
feat(auth): implement JWT-based authentication

Add login endpoint with JWT token generation and validation
middleware for protected routes.

BREAKING CHANGE: Session-based auth is no longer supported.
Existing sessions will be invalidated.
```

Step 10 — validate; if it fails, revise and re-validate:

```bash
.claude/skills/git-workflow/scripts/validate-commit.sh "<commit-message>"
```

Step 11 — stage and commit in a single operation (avoids race conditions):

```bash
git add [files...]
git commit -m "$(cat <<'EOF'
<commit-message>
EOF
)"
```

Step 12 — if `push ∈ actions` (resolved in Phase 0.5): `git push`. Then Phase 4; if `pr ∈ actions`, continue through Phase 4 into Phase 5.

## Phase 3b: Agent deployment

Step 13 — identify scopes: group files by feature or area; each scope independently committable; note relationships. Examples: "User authentication system", "Payment integration", "API documentation updates", "Test infrastructure improvements".

Step 14 — read `.claude/skills/git-workflow/templates/agent-prompts.md`.

Step 15 — deploy all agents in a SINGLE message with MULTIPLE Agent tool calls, one `docs-git-committer` per scope:

| Field         | Value                                                             |
| ------------- | ----------------------------------------------------------------- |
| subagent_type | `docs-git-committer`                                              |
| description   | "Commit [feature-name] with docs"                                 |
| prompt        | Complete instructions including scope, files, documentation needs |

Prompt template:

```
You are handling the git commit and documentation for: [FEATURE SCOPE]

## Changed Files in Your Scope

[List of files related to this feature]

## Context

[Brief description of what changed and why]

## Your Tasks

1. Review the changes in your assigned files
2. Determine if documentation updates are needed (see templates)
3. Update or create documentation if appropriate:
   - Feature docs: docs/features/[name].doc.md
   - CLAUDE.md: Only if critically needed in specific directory
   - Architecture docs: docs/architecture/ if system-wide changes
4. Stage all files (source + docs) and commit with conventional message
5. Return summary of what you committed

## Important

- Use conventional commit format
- Combine source + docs in ONE commit
- Use git add + commit in single command
- Do NOT push (main skill handles that)
- Focus ONLY on your assigned scope
```

Step 16 — todos per agent: `agent-N: "[Feature N] - docs-git-committer - status: in_progress"`.

Step 17 — all agents run in parallel, each owns its scope and its own commit; wait for all.

Step 18 — mark todos completed; validate each agent commit:

```bash
.claude/skills/git-workflow/scripts/validate-commit.sh "$(git log -1 --format=%s <commit-hash>)"
```

Amend any non-conformant message. Across a range:

```bash
git rebase -x '.claude/skills/git-workflow/scripts/validate-commit.sh "$(git log -1 --format=%s HEAD)" || echo "NEEDS FIX"' <merge-base>..HEAD
```

Single commit: `git commit --amend -m "<corrected-message>"`. Also check for conflicts or issues and verify documentation quality. Do not proceed to Phase 4 until all agent commits pass validation.

## Phase 4: Summary and push

Step 19 — consolidate. Direct: commit hash and message, files changed, doc updates. Agents: commits made, agents deployed and results, docs created/updated, issues.

Step 20 — display:

```markdown
# Git Workflow Complete

## Commits Made

### Commit 1: [hash]

**Message**: [subject line]
**Files**: [count] files changed
**Changes**: +[insertions] -[deletions]

[If multiple commits, repeat above]

## Documentation Updates

[If docs were updated]

- Created: docs/features/[name].doc.md
- Updated: [other docs]

[If no docs]

- No documentation updates needed (minor changes)

## Summary

- **Total Commits**: [count]
- **Files Changed**: [count]
- **Documentation**: [yes/no]
- **Pushed**: [yes/no]

## Next Steps

[If not pushed]

1. Review the commits: `git log --oneline -[count]`
2. View changes: `git show HEAD`
3. Push when ready: `git push`

[If pushed]
Changes have been pushed to remote
```

Step 21 — if `push ∈ actions` and not already pushed in Step 12: `git push`; display the result.
