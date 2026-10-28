# `/goal` Pairing Example

Pair this skill with the `/goal` session directive to drive the `--ci` autofix loop to completion — through every `handoff` fix-and-re-push iteration and every `rerun-pending` flake retry — without returning control between iterations. The done condition references the Phase 8 Goal Signals block, not the report file.

The shared contract (transcript-output rules, condition skeleton, caveats) is `.claude/skills/_shared/references/goal-pairing.md`; read it before relying on a `/goal` loop.

## How the evaluator reads `RESULT=` markers

- **Keep looping** — `handoff` (apply the fix, commit, push, re-invoke the monitor) and `rerun-pending` (flake suspected; wait, then re-invoke). Recoverable progress markers, not endpoints.
- **Done** — `RESULT=green` (`CI_GREEN: PASS`).
- **Stop** — any `bail-*` (`bail-recurrence`, `bail-nonfixable`, `bail-pushes`, `bail-timeout`). All four are terminal: `ci-monitor.sh` exhausts recoverable retries (`rerun-pending`) internally before emitting a bail, so a printed `bail-*` means no further automatic progress is possible. The evaluator tells "loop again" from "stop" by `Result: green` vs `Result: bail-*` in the Phase 8 output, not by re-classifying bail codes. Bail taxonomy: `ci-monitoring.md`.

Safety signals `NO_FORCE_PUSH`, `NO_NO_VERIFY`, and `NO_RAW_PROMPTS_OUTBOUND` are terminal on FAIL. An additional iteration cannot undo an earlier violation; preserve FAIL and stop for human review. Retry guidance never overrides these constraints.

## Recommended condition template

```
/goal Run /pr-autofix <PR> --ci using the pr-autofix workflow, continuing
through every handoff and rerun-pending iteration of the Phase 7 CI loop without
returning control to me. Done when the transcript shows the Phase 8
"## PR Autofix Complete" output followed by the Goal Signals printed verbatim —
EVERY_THREAD_HANDLED: PASS, NO_FORCE_PUSH: PASS, NO_NO_VERIFY: PASS,
NO_RAW_PROMPTS_OUTBOUND: PASS, REPORT_CREATED: PASS, and CI_GREEN: PASS. If CI_GREEN
prints FAIL alongside CI_BAIL_VISIBLE: PASS (a terminal bail-* fired), stop and report
the bail — do not re-run. If NO_FORCE_PUSH, NO_NO_VERIFY, or NO_RAW_PROMPTS_OUTBOUND
prints FAIL, stop immediately and report the safety failure; no further fixes or pushes,
no retry, and never relabel that failure PASS in this run. Only EVERY_THREAD_HANDLED
and REPORT_CREATED failures may be repaired within the authorized scope and existing
caps. Stop after 25 turns if not achieved.
```
