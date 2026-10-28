# `/goal` Pairing — Shared Contract

Reference for ycc skills that recommend pairing with the platform `/goal` session
directive for autonomous, loop-to-completion execution — currently `prp-implement`,
`implement-plan`, `review-fix`, `pr-autofix`, `prp-pr` (`--ci` mode only),
and `releaser` (the bounded Phase 8.5 release-CI loop only — never the human approval
gates). Each skill cites this doc so the transcript-output contract, the condition-template
shape, and the caveats stay convergent. Fix them here, not per skill.

## Transcript-output contract

The `/goal` evaluator judges **only the text printed to the transcript**. It never reads
files, inspects git state, or opens saved artifacts. Every signal a `/goal` done-condition
keys off MUST be **printed verbatim to the transcript** by the skill — writing it only to a
report file, or leaving it implicit in file state, is invisible to the evaluator.

Concretely:

- Emit a machine-readable **Goal Signals** block — one `KEY: PASS|FAIL` per line — in the
  skill's final OUTPUT, printed verbatim (not rendered only into a saved report).
- Use `PASS` only when the matching criterion in the skill's `## Success Criteria` holds;
  otherwise `FAIL`.
- Any intermediate marker a condition references (for example a per-batch
  `[done] Batch BN` progress log) must also be printed, not just recorded in a file.
- Never write a `/goal` condition that points at a file path or "the report" — point it at
  the printed signal keys.

## Recommended condition template

Reference the printed Goal Signals block, not file paths. Skeleton:

```
/goal <imperative: run the <skill> workflow on <input>>, continuing through every
CHECKPOINT without returning control to me. Done when the transcript shows
<final OUTPUT heading> followed by all <N> Goal Signals printed verbatim —
<KEY1>: PASS, <KEY2>: PASS, ... If any signal prints FAIL, keep fixing and re-running
until all are PASS. Stop after <budget> turns if not achieved.
```

Each skill fills the skeleton with its own signal keys (see that skill's
`## Success Criteria`).

## Caveats

1. **Worktree cwd**: when the skill runs in worktree mode (the default for both), reports
   and artifacts live under `<repo-root>/.claude/worktrees/<repo>-<slug>/...`. Conditions must key off
   printed signals, never main-repo file paths.
2. **Interactive failure prompts**: parallel/team batch failures hit the `failure-recovery`
   gate in `.claude/skills/_shared/references/question-policy.md`. `/goal`
   cannot answer it: interactively the loop stalls on the prompt; headless, the
   run stops with the failing batch and resume command. Expect manual intervention on
   batch failures.
3. **Platform**: `/goal` is available only in the Anthropic terminal (v2.1.139+) and the
   Codex CLI (v0.128.0+) — not in Cursor or opencode bundle invocations. Accept the trust
   dialog and keep hooks enabled in those runtimes.
