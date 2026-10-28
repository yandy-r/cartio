# Review Artifact Format (Shared Reference)

## Contents

- File locations
- Template
- Quick-mode variant
- Finding ID rules
- Status field rules
- Required fields per finding

Canonical schema for review artifacts written to `docs/prps/reviews/`.
`/code-review` writes it (both modes). `/review-fix`, `/quick-fix`,
and `/quick-review` parse it. Do not deviate — downstream skills parse
these fields mechanically.

## File locations

| Mode  | Path                                                  |
| ----- | ----------------------------------------------------- |
| Local | `docs/prps/reviews/local-{YYYYMMDD-HHMMSS}-review.md` |
| PR    | `docs/prps/reviews/pr-{NUMBER}-review.md`             |
| Quick | `docs/prps/reviews/quick-{YYYYMMDD-HHMMSS}-review.md` |

## Template

```markdown
# [Local Review | PR Review #<NUMBER>] — <TITLE or "Uncommitted Changes">

**Reviewed**: <ISO date>
**Mode**: Local | PR
**Author**: <author | "local">
**Branch**: <head> → <base>
**Decision**: APPROVE | REQUEST CHANGES | BLOCK | COMMENT

## Worktree Setup

<!-- Only emitted when WORKTREE_ACTIVE=true. Lists only the feature worktree; fixers run inside this path. -->

- **Parent**: <repo-root>/.claude/worktrees/<repo>-pr-<N>/ (branch: <pr-head-branch>)

## Summary

<1-2 sentence overall assessment>

## Findings

### CRITICAL

- **[F001]** `file.ts:42` — <description>
  - **Status**: Open
  - **Category**: <Security | Correctness | Type Safety | Performance | Pattern Compliance | Completeness | Maintainability>
  - **Suggested fix**: <concrete, actionable fix>

### HIGH

- **[F002]** `file.ts:73` — <description>
  - **Status**: Open
  - **Category**: ...
  - **Suggested fix**: ...

### MEDIUM

- **[F003]** ...
  - **Status**: Open
  - ...

### LOW

- **[F004]** ...
  - **Status**: Open
  - ...

## Validation Results

| Check      | Result                |
| ---------- | --------------------- |
| Type check | Pass / Fail / Skipped |
| Lint       | Pass / Fail / Skipped |
| Tests      | Pass / Fail / Skipped |
| Build      | Pass / Fail / Skipped |

## Files Reviewed

- `file1.ts` (Modified)
- `file2.ts` (Added)
- `file3.ts` (Deleted)
```

`## Worktree Setup` sits between `**Decision**:` and `## Summary`, and only when
the review ran with worktree isolation.

## Quick-mode variant

Quick reviews omit `## Validation Results` (no toolchain runs) and
`## Worktree Setup` (no worktree is ever created). All other sections — Header,
Summary, Findings (with full `[F###]`, `Status:`, `Category:`,
`Suggested fix:` fields per finding), Files Reviewed — are identical to the
standard template, so `/review-fix` parses them without special-casing.

## Finding ID rules

- Ordering: IDs are assigned ordered by severity (CRITICAL first), then by
  file path.
- Sequential: IDs start at `F001` and increment by one.
- Stable per file: once assigned in a given review artifact, an ID is never
  renumbered, even after a fix is applied.
- Per-artifact: IDs are scoped to a single review file. A fresh code-review
  pass generates a new file with its own `F001`-restart counter.
- Assigned in REPORT phase: in both sequential and parallel paths, IDs are
  assigned during the REPORT phase (Phase 3 for local, Phase 6 for PR), AFTER
  merge and sort. Reviewer agents in parallel mode do NOT assign IDs.

## Status field rules

Every finding has a `Status` field. Valid values:

| Status | Meaning                                                                                                      |
| ------ | ------------------------------------------------------------------------------------------------------------ |
| Open   | Default on first write. Not yet processed by `/review-fix`, or below the fix skill's severity threshold. |
| Fixed  | Successfully fixed by `/review-fix`. Set by the fix skill — code-review itself never writes this.        |
| Failed | Attempted by `/review-fix` but the fix broke validation. Set by the fix skill.                           |

`/code-review` and `/quick-review` only ever write `Status: Open`. All
other states are set in place by `/review-fix` (or, for inline hand-offs,
reported by `/quick-fix` without editing any artifact).

## Required fields per finding

Every finding includes these four lines (in this order) so the artifact is
machine-parseable:

```
- **[F###]** `file:line` — <description>
  - **Status**: <Open|Fixed|Failed>
  - **Category**: <category>
  - **Suggested fix**: <concrete fix>
```

Findings missing a `Suggested fix` line are valid but are skipped by
`/review-fix` (flagged for human judgment).
