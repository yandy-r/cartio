# Review Checklist (Shared Reference)

## Contents

- Severity Rubric
- Local / Quick Review — Single-Pass Checklist (Path A)
- PR Review — Single-Pass Checklist (Path A, 7 Categories)
- Parallel Reviewer Roster (Path B / Path C)
- Standard Findings Format (Reviewer Output)
- Merge Procedure (Path B / Path C)

> Canonical review checklist, severity rubric, parallel-reviewer roster, and
> standard findings format. Consumed by `code-review` (local + PR modes)
> and `quick-review`. Skills that delegate here must not duplicate the
> content below — reference this file instead.

---

## Severity Rubric

| Severity     | Meaning                                     | Action                  |
| ------------ | ------------------------------------------- | ----------------------- |
| **CRITICAL** | Security vulnerability or data loss risk    | Must fix before merge   |
| **HIGH**     | Bug or logic error likely to cause issues   | Should fix before merge |
| **MEDIUM**   | Code quality issue or missing best practice | Fix recommended         |
| **LOW**      | Style nit or minor suggestion               | Optional                |

Findings are grouped by severity in the artifact under `### CRITICAL`,
`### HIGH`, `### MEDIUM`, `### LOW`, in that order.

---

## Local / Quick Review — Single-Pass Checklist (Path A)

Applied to uncommitted changes on `HEAD`. Read each changed file in full.
Flag only gaps that affect correctness or the stated requirements. Checklist
items are prompts for investigation, not automatic findings or style quotas.

**Security Issues (CRITICAL):**

- Hardcoded credentials, API keys, tokens
- SQL injection vulnerabilities
- XSS vulnerabilities
- Missing input validation
- Insecure dependencies
- Path traversal risks

**Code Quality (HIGH):**

- Functions > 50 lines
- Files > 800 lines
- Nesting depth > 4 levels
- Missing error handling
- `console.log` statements
- TODO/FIXME comments
- Missing JSDoc for public APIs

**Best Practices (MEDIUM):**

- Mutation patterns (use immutable instead)
- Emoji usage in code/comments
- Missing tests for new code
- Accessibility issues (a11y)

---

## PR Review — Single-Pass Checklist (Path A, 7 Categories)

Applied to PR head-revision file contents. Each category is independent —
a single finding may touch several. Flag only gaps that affect correctness or
the stated requirements.

| Category               | What to Check                                                                 |
| ---------------------- | ----------------------------------------------------------------------------- |
| **Correctness**        | Logic errors, off-by-ones, null handling, edge cases, race conditions         |
| **Type Safety**        | Type mismatches, unsafe casts, `any` usage, missing generics                  |
| **Pattern Compliance** | Matches project conventions (naming, file structure, error handling, imports) |
| **Security**           | Injection, auth gaps, secret exposure, SSRF, path traversal, XSS              |
| **Performance**        | N+1 queries, missing indexes, unbounded loops, memory leaks, large payloads   |
| **Completeness**       | Missing tests, missing error handling, incomplete migrations, missing docs    |
| **Maintainability**    | Dead code, magic numbers, deep nesting, unclear naming, missing types         |

---

## Parallel Reviewer Roster (Path B / Path C)

Three standalone `code-reviewer` sub-agents, dispatched via `Agent` without
`name` or `run_in_background` (Path B), or agent-team teammates spawned via
`Agent` with `name` (Path C). See
`.claude/skills/_shared/references/standalone-dispatch.md`
for the full `Agent` spawn/return contract that Path B must follow. The **focus
split** differs between local/quick and PR modes because local mode has a
narrower category set.

### Local / Quick Mode Roster

| Reviewer               | Focus           | Checklist Items                                                                                                               |
| ---------------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `correctness-reviewer` | Code Quality    | Functions > 50 lines, files > 800 lines, nesting > 4 levels, missing error handling, `console.log`, TODO/FIXME, missing JSDoc |
| `security-reviewer`    | Security Issues | Hardcoded credentials/keys/tokens, SQL injection, XSS, missing input validation, insecure deps, path traversal                |
| `quality-reviewer`     | Best Practices  | Mutation patterns, emoji in code/comments, missing tests, accessibility (a11y)                                                |

### PR Mode Roster

| Reviewer               | Categories                             | What to Check                                                                                                                                                             |
| ---------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `correctness-reviewer` | Correctness, Type Safety, Completeness | Logic errors, off-by-ones, null handling, edge cases, race conditions, type mismatches, unsafe casts, `any` usage, missing generics, missing tests, incomplete migrations |
| `security-reviewer`    | Security, Performance                  | Injection, auth gaps, secret exposure, SSRF, path traversal, XSS, N+1 queries, missing indexes, unbounded loops, memory leaks, large payloads                             |
| `quality-reviewer`     | Pattern Compliance, Maintainability    | Project conventions (naming, file structure, error handling, imports), dead code, magic numbers, deep nesting, unclear naming, missing types                              |

---

## Standard Findings Format (Reviewer Output)

Each parallel reviewer returns findings in this shape (pre-merge, before
finding-ID assignment):

```markdown
## Findings

### CRITICAL

- `file.ts:42` — [description] [category]
  - Suggested fix: [fix]

### HIGH

- ...

### MEDIUM

- ...

### LOW

- ...
```

The `[category]` tag applies to PR mode; local/quick mode may omit it (the focus
split already implies a category).

---

## Merge Procedure (Path B / Path C)

After all 3 reviewers return (Path B only — see
`.claude/skills/_shared/references/standalone-dispatch.md`
for the result-delivery and batch-completion contract):

1. For any reviewer whose `Agent` return was empty, missing, or malformed,
   treat that reviewer as a failure for that role (do not fabricate findings
   for it). In Path B only, the scratch file at
   `docs/prps/reviews/.review-scratch/<run-id>/<reviewer-name>.md` is the
   calling skill's own copy of the inline returns it already received, not a
   second source to recover from.
2. Combine by severity (CRITICAL first, then HIGH, MEDIUM, LOW).
3. De-duplicate findings at the same `file:line` (if two reviewers flagged the
   same issue, keep the more severe one and annotate which reviewers concurred).
4. Sort within each severity by file path.
5. Attach the reviewer source to each finding (`[correctness]`, `[security]`,
   `[quality]`) for traceability.

Pass the merged findings to the REPORT phase as if they came from a single-pass
review.

**Cleanup**: after a successful merge, the calling skill should remove the
`docs/prps/reviews/.review-scratch/<run-id>/` directory.

---
