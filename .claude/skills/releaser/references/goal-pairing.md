# Bounded Release CI Goal Pairing

## /goal pairing

Pair this skill with the `/goal` session directive **only for the bounded Phase 8.5 release-CI auto-fix loop** — never for the skill as a whole. The full `releaser` flow has four human approval gates that exist specifically to prevent unattended publishing and looping; `/goal` must never auto-satisfy them:

- **Phase 2 — version confirm.** The user must approve the proposed version bump.
- **Phase 2 — backport gate (patches).** The user decides each unlabelled fix and any shipping without a missing backport.
- **Phase 5 — manifest diff review.** The user must approve what changed before any commit.
- **Phase 8.5.1 — CI authorization gate.** The user must approve before the bounded, destructive-capable loop starts. `--ci-yes` does **not** bypass this gate under an active `/goal` (see the 8.5.1 `/goal` re-confirmation guard).

Because the `/goal` evaluator is transcript-only it cannot answer these prompts, so a `/goal` loop will **pause** at each gate until you respond in person. That is the intended behavior: only after you have published (`--publish --confirm`) and authorized the loop does `/goal` add value — driving Phase 8.5.2+ through every `handoff` fix-and-validate and `rerun-pending` retry to green without returning control between iterations. The `GATES_INTERACTIVE` Goal Signal records that none of these gates was auto-approved.

The Phase 8.5 loop's `RESULT=` markers (from `release-ci-monitor.sh`, sharing the `ci-monitor.sh` contract with `pr-autofix` and `prp-pr`) tell the evaluator when to keep looping versus when to stop:

- **Keep looping** — `handoff` (8.5.3 fix-and-validate) and `rerun-pending` (8.5.2 wait-and-retry). Recoverable progress markers, not endpoints.
- **Done** — `RESULT=green`, with the Phase 9 summary showing "Release CI loop outcome: `green`" and the audit-log path (`CI_GREEN: PASS`, `AUDIT_LOG_PRINTED: PASS`).
- **Stop** — any terminal `bail-*` (`bail-recurrence`, `bail-nonfixable`, `bail-pushes`, `bail-timeout`, `not-found`) or `loop-blocked`. `release-ci-monitor.sh` exhausts recoverable retries internally before emitting a bail, so a printed terminal marker means no further automatic progress is possible. The evaluator distinguishes "loop again" from "stop" by `green` vs `bail-*`/`loop-blocked` in the Phase 9 summary — never by re-classifying bail codes. See `.claude/skills/_shared/references/ci-monitoring.md` ("Release mode") for the authoritative bail taxonomy.

Recommended condition template (set this **after** you have approved Phases 2 and 5, published with `--publish --confirm`, and authorized the loop at 8.5.1):

```
/goal Drive the releaser Phase 8.5 release-CI loop to green, continuing through every
handoff and rerun-pending iteration without returning control to me. Done when the Phase 9
summary shows "Release CI loop outcome: green", the audit-log path is printed, and the Goal
Signals print verbatim — GATES_INTERACTIVE: PASS, CI_GREEN: PASS, and AUDIT_LOG_PRINTED:
PASS. If CI_GREEN prints FAIL alongside CI_BAIL_VISIBLE: PASS (a terminal bail-*, loop-blocked,
or declined), stop and report it — do not re-run. Never auto-approve the Phase 2 version
confirm, the Phase 5 manifest review, or the Phase 8.5.1 authorization. Stop after 25 turns
if not achieved.
```

The transcript-output contract and shared caveats (worktree cwd, interactive failure prompts, platform availability) live in the shared reference — read it before relying on a `/goal` loop:

```
.claude/skills/_shared/references/goal-pairing.md
```
