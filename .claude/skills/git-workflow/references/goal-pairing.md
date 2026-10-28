# /goal pairing (--ci mode only)

Pair this skill with the `/goal` session directive only in `--ci` mode. Without `--ci`, `/git-workflow` may stop after a local commit, push, or PR creation, and no long-running loop exists for `/goal` to drive. In `--ci` mode, Phase 6 runs the bounded CI auto-fix loop through the shared `ci-monitor.sh` contract, and `/goal` can keep the session moving through every `handoff` fix-and-push and `rerun-pending` flake retry.

`RESULT=` markers tell the evaluator when to loop and when to stop:

- **Keep looping** — `handoff` (apply the fix per the classification table, commit, push to the PR head branch, re-invoke the monitor) and `rerun-pending` (flake suspected; sleep, re-invoke). Recoverable progress markers, not endpoints.
- **Done** — `RESULT=green` (`CI_GREEN: PASS`), PR URL printed in the final report.
- **Stop** — any `bail-*` (`bail-recurrence`, `bail-nonfixable`, `bail-pushes`, `bail-timeout`). All terminal: `ci-monitor.sh` exhausts recoverable `rerun-pending` retries internally before emitting a bail, so a printed `bail-*` means no further automatic progress is possible. The evaluator distinguishes "loop again" from "stop" by `green` vs `bail-*` in the Step 37 report, never by re-classifying bail codes. Taxonomy: `.claude/skills/_shared/references/ci-monitoring.md`.

Recommended condition template (`--ci` mode):

```
/goal Run /git-workflow --ci using the git-workflow workflow, continuing through
every handoff and rerun-pending iteration of the Phase 6 CI loop without returning control
to me. Done when the transcript shows the final CI report with the PR number and URL,
followed by the Goal Signals printed verbatim — PR_IN_SCOPE: PASS, PR_URL_PRINTED: PASS,
and CI_GREEN: PASS. If CI_GREEN prints FAIL alongside CI_BAIL_VISIBLE: PASS (a terminal
bail-* fired), stop and report the bail — do not re-run. If any other signal prints FAIL,
keep fixing and re-running until all are PASS. Stop after 25 turns if not achieved.
```

The transcript-output contract and shared caveats (worktree cwd, interactive failure prompts, platform availability) are in `.claude/skills/_shared/references/goal-pairing.md`; read it before relying on a `/goal` loop.
