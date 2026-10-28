# Fix Report

## Contents

- Report location
- Template
- Worktree summary

## Report location

Write the fix report to `$(git rev-parse --show-toplevel)/docs/prps/reviews/fixes/`. When in a worktree this resolves to the worktree top-level; otherwise to the main repo.

```bash
mkdir -p "$(git rev-parse --show-toplevel)/docs/prps/reviews/fixes"
```

Derive the report filename from the source review:

| Source                                              | Report                                                   |
| --------------------------------------------------- | -------------------------------------------------------- |
| `docs/prps/reviews/pr-42-review.md`                 | `docs/prps/reviews/fixes/pr-42-fixes.md`                 |
| `docs/prps/reviews/local-20260408-143022-review.md` | `docs/prps/reviews/fixes/local-20260408-143022-fixes.md` |

After writing the fix report, stage and commit it to the PR branch alongside the fix commits:

```bash
FIX_REPORT_REL="docs/prps/reviews/fixes/<name>-fixes.md"
git add "$FIX_REPORT_REL"
git commit -m "docs(review): add fix report for PR #${PR_NUMBER}"
git push
```

## Template

```markdown
# Fix Report: <source-name>

**Source**: <source review path>
**Applied**: <ISO date>
**Severity threshold**: <CRITICAL|HIGH|MEDIUM|LOW>
**Mode**: Sequential | Parallel sub-agents (N batches, max width W) | Agent team (N batches, max width W)

## Summary

- **Total findings in source**: N
- **Already processed before this run**:
  - Fixed: A
  - Failed: B
- **Eligible this run**: M
- **Applied this run**:
  - Fixed: X
  - Failed: Y
- **Skipped this run**:
  - Below severity threshold: C
  - No suggested fix: D
  - Missing file: E

## Fixes Applied

| ID   | Severity | File             | Line | Status | Notes                                                               |
| ---- | -------- | ---------------- | ---- | ------ | ------------------------------------------------------------------- |
| F001 | CRITICAL | src/auth.ts      | 42   | Fixed  |                                                                     |
| F002 | HIGH     | src/api.ts       | 17   | Fixed  |                                                                     |
| F003 | HIGH     | src/db/query.ts  | 8    | Failed | Type error after edit; suggested fix referenced non-existent method |
| F005 | HIGH     | src/utils/fmt.ts | 112  | Fixed  | Same-file group with F004                                           |
| F004 | HIGH     | src/utils/fmt.ts | 78   | Fixed  | Processed in descending line order                                  |

## Files Changed

- `src/auth.ts` (Fixed F001)
- `src/api.ts` (Fixed F002)
- `src/utils/fmt.ts` (Fixed F004, F005)

## Failed Fixes

### F003 — `src/db/query.ts:8`

**Severity**: HIGH
**Category**: Security
**Description**: Unescaped input in raw SQL query
**Suggested fix (from review)**: Use `db.query('... WHERE id = $1', [userId])`
**Blocker**: `Property 'query' does not exist on type 'Connection'`. The connection type in this codebase exposes `execute`, not `query`.
**Recommendation**: Update the review's Suggested fix to use `db.execute(...)` and re-run `/review-fix`.

## Validation Results

| Check      | Result                |
| ---------- | --------------------- |
| Type check | Pass / Fail / Skipped |
| Tests      | Pass / Fail / Skipped |

## Next Steps

- Re-run `/code-review <same target>` to verify the remaining open findings and confirm fixes resolved the issues
- Address failed fixes manually using the Blocker + Recommendation notes above
- Run `/git-workflow --commit` to commit the changes when satisfied
```

## Worktree summary (only when `WORKTREE_ACTIVE=true`)

At the end of REPORT, append to the fix report:

```markdown
## Worktree Summary

<output of list-worktrees.sh>

### Next steps

- Review the parent worktree: `git -C <PARENT_PATH> log --oneline -10`
- Push to update the PR: `git -C <PARENT_PATH> push origin HEAD`
- Clean up when done: `git worktree remove <PARENT_PATH>`
```

Populate the `<output of list-worktrees.sh>` block by running:

```bash
bash ".claude/skills/_shared/scripts/list-worktrees.sh" \
  "${REPO}" "${SLUG}"
```

---
