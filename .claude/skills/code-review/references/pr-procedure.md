# PR Review Procedure

## Contents

- SETUP
- FETCH / CONTEXT / REVIEW
- VALIDATE / DECIDE
- REPORT / PUBLISH / OUTPUT
- Edge cases

## Phase 0½ — SETUP (PR mode default; skipped with `--no-worktree`)

Skip this phase entirely when `WORKTREE_MODE=false` (i.e., `--no-worktree` was passed).

### PR mode with `--worktree`

1. Resolve `<repo>`:

   ```bash
   REPO=$(basename "$(git rev-parse --show-toplevel)")
   ```

2. Resolve `<pr-head-branch>`:

   ```bash
   PR_HEAD=$(gh pr view <N> --json headRefName --jq .headRefName)
   ```

3. Compute slug = `pr-<N>`.

4. Check out the PR head branch into an isolated worktree:

   ```bash
   PARENT_WORKTREE_PATH=$(
     bash ".claude/skills/_shared/scripts/setup-worktree.sh" \
       parent "${REPO}" "pr-<N>" --base-ref "${PR_HEAD}"
   )
   ```

   The script is idempotent: re-running it on an existing worktree on the same branch reuses the path.

5. Record `WORKTREE_ACTIVE=true`, `PARENT_BRANCH=<PR_HEAD>`, and `PARENT_WORKTREE_PATH=<captured path>`.

6. If the checkout fails (e.g., the branch is already checked out in the main repo or conflicts exist), abort with:

   ```
   Error: could not check out PR head '<PR_HEAD>' into a worktree.
   Likely cause: the branch is currently checked out in the main repository.
   Switch away from it (git switch <other-branch>) and re-run.
   ```

### Behavior summary

| Flag state                              | Effect                                                                     |
| --------------------------------------- | -------------------------------------------------------------------------- |
| `WORKTREE_MODE=false` (`--no-worktree`) | No worktree. Files read from main working tree (local) or GitHub API (PR). |
| `WORKTREE_MODE=true` (default), local   | Warning printed; no worktree created; behaves as `WORKTREE_MODE=false`.    |
| `WORKTREE_MODE=true` (default), PR      | Parent worktree created; files read from `$PARENT_WORKTREE_PATH`.          |

### Phase 1 — FETCH

Parse input to determine PR:

| Input                          | Action                                   |
| ------------------------------ | ---------------------------------------- |
| Number (e.g. `42`)             | Use as PR number                         |
| URL (`github.com/.../pull/42`) | Extract PR number                        |
| Branch name                    | Find PR via `gh pr list --head <branch>` |

```bash
gh pr view <NUMBER> --json number,title,body,author,baseRefName,headRefName,changedFiles,additions,deletions
gh pr diff <NUMBER>
```

If PR not found, stop with error. Store PR metadata for later phases.

### Phase 2 — CONTEXT

Build review context:

1. **Project rules** — Read `CLAUDE.md`, `RELEASING.md` (when present), `.claude/docs/`, and any contributing guidelines
2. **PRP artifacts** — Check `docs/prps/reports/` and `docs/prps/plans/` (including `completed/`) for implementation context related to this PR
3. **PR intent** — Parse PR description for goals, linked issues, test plans
4. **Changed files** — List all modified files and categorize by type (source, test, config, docs)
5. **PR base vs. release model** — Skip silently when `release-state.sh get` prints `present=0`. Otherwise fetch the head (`git fetch origin <headRefName>`, or `pull/<N>/head` for a fork) and compare `baseRefName` with `release-state.sh base --head FETCH_HEAD`, per `.claude/skills/_shared/references/pr-base-and-backport.md` (section "Checking an existing PR"). On a mismatch, record a **MEDIUM** finding anchored at `RELEASING.md` (Category: Pattern Compliance) naming both branches, with the suggested fix `gh pr edit <N> --base <expected>` (a retarget, not a code change).

### Phase 3 — REVIEW

Read each changed file **in full** (not just the diff hunks — you need surrounding context).

For PR reviews, fetch the full file contents at the PR head revision:

**When `WORKTREE_ACTIVE=true`**: read files directly from the parent worktree using the `Read` tool — the worktree has the PR head branch checked out, so the files on disk match the PR head revision. Skip the `gh api` fetch entirely:

```bash
# Instead of: gh api "repos/{owner}/{repo}/contents/$file?ref=<head-branch>"
# Read from:  ${PARENT_WORKTREE_PATH}/$file
```

**When `WORKTREE_ACTIVE=false`** (the existing path): continue to use `gh api` to fetch each file at the PR head ref:

```bash
gh pr diff <NUMBER> --name-only | while IFS= read -r file; do
  gh api "repos/{owner}/{repo}/contents/$file?ref=<head-branch>" --jq '.content' | base64 -d
done
```

### Phase 4 — VALIDATE

Run available validation commands.

Detect the project type from config files (`package.json`, `Cargo.toml`, `go.mod`, `pyproject.toml`, etc.), then run the appropriate commands:

**Node.js / TypeScript** (has `package.json`):

```bash
npm run typecheck 2>/dev/null || npx tsc --noEmit 2>/dev/null  # Type check
npm run lint                                                    # Lint
npm test                                                        # Tests
npm run build                                                   # Build
```

**Rust** (has `Cargo.toml`):

```bash
cargo clippy -- -D warnings  # Lint
cargo test                   # Tests
cargo build                  # Build
```

**Go** (has `go.mod`):

```bash
go vet ./...    # Lint
go test ./...   # Tests
go build ./...  # Build
```

**Python** (has `pyproject.toml` / `setup.py`):

```bash
pytest  # Tests
```

Run only the commands that apply to the detected project type. Record pass/fail for each.

### Phase 5 — DECIDE

Form recommendation based on findings:

| Condition                                    | Decision                          |
| -------------------------------------------- | --------------------------------- |
| Zero CRITICAL/HIGH issues, validation passes | **APPROVE**                       |
| Only MEDIUM/LOW issues, validation passes    | **APPROVE** with comments         |
| Any HIGH issues or validation failures       | **REQUEST CHANGES**               |
| Any CRITICAL issues                          | **BLOCK** — must fix before merge |

Special cases:

- Draft PR (with `--keep-draft`) → Always use **COMMENT** (not approve/block). Without `--keep-draft`, the PR is auto-promoted to ready before this decision runs, so normal severity-based decisions apply.
- Only docs/config changes → Lighter review, focus on correctness
- Explicit `--approve` or `--request-changes` flag → Override decision (but still report all findings)

### Phase 6 — REPORT

Assign finding IDs and initial Status after merge per the shared artifact
schema (`.claude/skills/_shared/references/review-artifact-format.md`,
Finding ID rules: ordered by severity, CRITICAL first, then by file path).

#### Draft Promotion

At the very start of Phase 6, in PR mode, run the draft→ready promotion unless `--keep-draft` was set:

```bash
if [[ "$KEEP_DRAFT" != "true" ]]; then
  IS_DRAFT=$(gh pr view "$PR_NUMBER" --json isDraft --jq .isDraft)
  if [[ "$IS_DRAFT" == "true" ]]; then
    gh pr ready "$PR_NUMBER"
    echo "Promoted PR #$PR_NUMBER from draft to ready for review."
  fi
fi
```

#### Artifact Write

Write the artifact to `$(git rev-parse --show-toplevel)/docs/prps/reviews/pr-<N>-review.md`. When `WORKTREE_ACTIVE=true` this resolves to the worktree top-level (e.g., `<repo-root>/.claude/worktrees/<repo>-pr-<N>/docs/prps/reviews/pr-<N>-review.md`); when `--no-worktree` is in effect, it resolves to the main repo. The artifact intentionally lives **with the PR branch** so it travels with the review history.

```bash
mkdir -p "$(git rev-parse --show-toplevel)/docs/prps/reviews"
```

Use the shared Review Artifact Format at
`.claude/skills/_shared/references/review-artifact-format.md`.
The artifact must include finding IDs and `Status: Open` on every finding so that `/review-fix` can later update the file in place.

- Write the artifact to `$(git rev-parse --show-toplevel)/docs/prps/reviews/pr-<N>-review.md`. When `WORKTREE_ACTIVE=true` this resolves to the worktree top-level (e.g., `<repo-root>/.claude/worktrees/<repo>-pr-<N>/docs/prps/reviews/pr-<N>-review.md`); when `--no-worktree` is in effect, it resolves to the main repo. The artifact intentionally lives **with the PR branch** so it travels with the review history.
- **Note**: the artifact will be committed to the PR branch (see commit step below). If you do not want the review record on the PR branch, add `docs/prps/reviews/` to `.gitignore` — the skill detects this and skips the commit.

#### Artifact Commit + Push

After the artifact has been written, in PR mode when `WORKTREE_ACTIVE=true`, commit and push it to the PR branch before posting the review:

```bash
if [[ "$WORKTREE_ACTIVE" == "true" ]]; then
  ARTIFACT_REL="docs/prps/reviews/pr-${PR_NUMBER}-review.md"
  pushd "$PARENT_WORKTREE_PATH" >/dev/null
  if git check-ignore -q "$ARTIFACT_REL"; then
    echo "Artifact path is gitignored; skipping commit. Worktree preserved at $PARENT_WORKTREE_PATH."
    KEEP_WORKTREE=true
  else
    git add "$ARTIFACT_REL"
    git commit -m "docs(review): add review artifact for PR #${PR_NUMBER}"
    git push
    echo "Committed and pushed review artifact to PR #${PR_NUMBER} branch."
  fi
  popd >/dev/null
fi
```

If `PARALLEL_MODE=true` (Path B was used), remove the
`docs/prps/reviews/.review-scratch/pr-<NUMBER>/` scratch directory now that
the artifact has been written (and, when `WORKTREE_ACTIVE=true`, committed
and pushed) successfully.

### Phase 7 — PUBLISH

Post the review to GitHub:

```bash
# If APPROVE
gh pr review <NUMBER> --approve --body "<summary of review>"

# If REQUEST CHANGES
gh pr review <NUMBER> --request-changes --body "<summary with required fixes>"

# If BLOCK (GitHub has no dedicated BLOCK event; map to request-changes)
gh pr review <NUMBER> --request-changes --body "<blocking issues must be fixed before merge>"

# If COMMENT only (draft PR or informational)
gh pr review <NUMBER> --comment --body "<summary>"
```

For inline comments on specific lines, use the GitHub review comments API:

```bash
gh api "repos/{owner}/{repo}/pulls/<NUMBER>/comments" \
  -f body="<comment>" \
  -f path="<file>" \
  -F line=<line-number> \
  -f side="RIGHT" \
  -f commit_id="$(gh pr view <NUMBER> --json headRefOid --jq .headRefOid)"
```

Alternatively, post a single review with multiple inline comments at once:

```bash
gh api "repos/{owner}/{repo}/pulls/<NUMBER>/reviews" \
  -f event="COMMENT" \
  -f body="<overall summary>" \
  --input comments.json  # [{"path": "file", "line": N, "body": "comment"}, ...]
```

#### Worktree Cleanup

After `gh pr review` succeeds, remove the worktree unless `--keep-worktree` is set or the worktree is dirty:

```bash
if [[ "$WORKTREE_ACTIVE" == "true" && "$KEEP_WORKTREE" != "true" && -n "$PARENT_WORKTREE_PATH" ]]; then
  DIRTY=$(git -C "$PARENT_WORKTREE_PATH" status --porcelain)
  if [[ -n "$DIRTY" ]]; then
    echo "Worktree has uncommitted changes; skipping cleanup. Inspect: $PARENT_WORKTREE_PATH"
  else
    git worktree remove "$PARENT_WORKTREE_PATH"
    echo "Removed worktree $PARENT_WORKTREE_PATH."
  fi
fi
```

The local PR branch is intentionally left alone; branch deletion is out of scope.

### Phase 8 — OUTPUT

Report to user:

```
PR #<NUMBER>: <TITLE>
Decision: <APPROVE|REQUEST_CHANGES|BLOCK>

Issues: <critical_count> critical, <high_count> high, <medium_count> medium, <low_count> low
Validation: <pass_count>/<total_count> checks passed

Artifacts:
  Review: docs/prps/reviews/pr-<NUMBER>-review.md
  GitHub: <PR URL>

Next steps:
  - <contextual suggestions based on decision>
```

---

## Edge Cases

- **No `gh` CLI and no GitHub MCP**: Fall back to local-only review (read the diff, skip GitHub publish). Warn user.
- **Diverged branches**: Suggest `git fetch origin && git rebase origin/<base>` before review.
- **Large PRs (>50 files)**: Warn about review scope. Focus on source changes first, then tests, then config/docs.
- **Stale PR head (default worktree mode)**: the worktree is a snapshot of the PR head at SETUP time. If new commits land on the PR after the review starts, re-run to resync.
- **Concurrent runs**: two `/code-review <N>` invocations on different PR numbers never collide (each gets its own `<repo>-pr-<N>/` directory). Two invocations on the SAME PR number share the same parent worktree (idempotent), which is by design.
- **Cleanup**: worktrees are removed automatically after the review is posted (Phase 7 Worktree Cleanup). Pass `--keep-worktree` to retain the worktree. If the worktree is dirty, cleanup is skipped and you are told where to inspect. Use `--no-worktree` for the previous behavior (no worktree created or removed).

---
