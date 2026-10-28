# Output Templates

## Contents

- [Phase 2d in-progress message](#phase-2d-in-progress-message)
- [Phase 2e filter results](#phase-2e-filter-results)
- [Phase 3 eligible-comments table](#phase-3-eligible-comments-table)
- [Phase 3 approval prompt](#phase-3-approval-prompt)
- [Phase 8 local report](#phase-8-local-report)
- [Phase 8 final stdout and Goal Signals](#phase-8-final-stdout-and-goal-signals)

## Phase 2d in-progress message

```
⏳ Detected an in-progress automated review on PR #<N>. Try again in a few minutes.
```

## Phase 2e filter results

```
Filter Results (PR #<N>):
  Total comments fetched:        T
  Already resolved:              R   (kept: --include-resolved <yes|no>)
  Outdated:                      O   (kept: --include-outdated <yes|no>)
  Dropped by author filter:      A
  Below severity threshold:      S
  Eligible for processing:       E
```

## Phase 3 eligible-comments table

```
PR #<N> — eligible comments (E)

| # | Author             | Severity | Path:Line              | Source         | Summary                       |
|---|--------------------|----------|------------------------|----------------|-------------------------------|
| 1 | coderabbitai[bot]  | CRITICAL | src/auth.ts:42         | review_thread  | Inverted auth check           |
| 2 | sonarcloud[bot]    | HIGH     | src/api.ts:17          | review_thread  | Missing null guard            |
| 3 | reviewer-alice     | MEDIUM   | (top-level)            | issue_comment  | Consider extracting helper    |
| ... |
```

Summary: first non-empty line of `body`, truncated to 60 chars after sanitization.

## Phase 3 approval prompt

One AskUserQuestion per comment (or grouped same-file batches):

```
Comment #<i>: <author> — <path:line>
<sanitized summary>

Action:
  ✅ Apply       — dispatch fixer
  💬 Skip        — reply with reason, then resolve thread (unless --no-reply-on-skip)
  ⏭️ Defer       — leave thread untouched, do not reply
  ❌ Abort       — stop the entire run
```

For `Skip`, a second AskUserQuestion collects a free-form reason (max ~140 chars), sanitized before posting.

## Phase 8 local report

Write to `$(git rev-parse --show-toplevel)/docs/prps/reports/pr-autofix-${PR_NUMBER}-$(date -u +%FT%H%M%SZ).md`:

```markdown
# PR Autofix Report: PR #<N>

**Applied**: <ISO date>
**Branch**: <HEAD_BRANCH> → <BASE_BRANCH>
**Mode**: Sequential | Parallel
**Filters**:

- severity ≥ <MIN>
- author <pattern | bot-only | human-only | all>
- include-resolved: <yes|no>
- include-outdated: <yes|no>

## Summary

- Comments fetched: T
- Eligible after filter: E
- Applied (Fixed): X
- Applied (Failed): Y
- Skipped (with reply): S
- Deferred (untouched): D
- Threads resolved: R
- Threads left open: L (Failed + Deferred)

## Results

| #   | Author            | Severity | Path:Line      | Decision | Status | Thread state | Notes          |
| --- | ----------------- | -------- | -------------- | -------- | ------ | ------------ | -------------- |
| 1   | coderabbitai[bot] | CRITICAL | src/auth.ts:42 | Apply    | Fixed  | Resolved     |                |
| 2   | sonarcloud[bot]   | HIGH     | src/api.ts:17  | Apply    | Failed | Open (reply) | <BLOCKER>      |
| 3   | reviewer-alice    | MEDIUM   | (top-level)    | Skip     | -      | Reaction 👍  | "Not in scope" |
| ... |

## Files Changed

- `src/auth.ts` (Fixed comment #1)
- ...

## CI Result (if --ci)

| Metric       | Value            |
| ------------ | ---------------- |
| Final result | green / bail-... |
| Iterations   | <N>              |
| Auto-pushes  | <M>              |
| Audit log    | <path>           |

## Failures

### Comment #2 — src/api.ts:17 (sonarcloud[bot])

**Blocker**: <text>
**Recommendation**: <text>
```

## Phase 8 final stdout and Goal Signals

```
## PR Autofix Complete

PR:        #<PR_NUMBER>
Branch:    <HEAD_BRANCH>
Mode:      <Sequential|Parallel>
Report:    docs/prps/reports/pr-autofix-<N>-<ts>.md

### Comments
  Fetched:  T
  Eligible: E
  Fixed:    X
  Failed:   Y
  Skipped:  S
  Deferred: D

### Threads
  Resolved:    R
  Left open:   L

### CI (if --ci)
  Result:      <green | bail-...>
  Iterations:  <N>
  Auto-pushes: <M>

### Next steps
  gh pr view <N> --web    # inspect PR
  /pr-autofix <N>     # re-run after new comments

### Goal Signals (machine-readable — printed verbatim for /goal)
EVERY_THREAD_HANDLED: <PASS|FAIL>
NO_FORCE_PUSH: <PASS|FAIL>
NO_NO_VERIFY: <PASS|FAIL>
NO_RAW_PROMPTS_OUTBOUND: <PASS|FAIL>
REPORT_CREATED: <PASS|FAIL>
CI_GREEN: <PASS|FAIL|n/a>
CI_BAIL_VISIBLE: <PASS|n/a>
```

Print every signal verbatim as the last lines of the Phase 8 output, one `KEY: PASS|FAIL` per line. `PASS` only when the matching success criterion in SKILL.md holds; otherwise `FAIL`. The two CI signals are `n/a` when `--ci` was not set. With `--ci`, `CI_GREEN` is `PASS` only on `Result: green`, and `CI_BAIL_VISIBLE` is `PASS` when a `bail-*` and its `REASON=` were printed (otherwise `n/a`).
