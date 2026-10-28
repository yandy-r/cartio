# Question Policy — Shared Contract

## Contents

- Detection rule
- Gate classes
- Hard rules
- Declared defaults
- Text-mode fallback
- Runtime modes

Reference for every ycc skill that asks the user a question. Each asking skill declares
its gate class(es) and its headless default in one **Questions** line that links here. Fix
the shared behavior here, not per skill.

## Detection rule

<!-- ycc:verbatim-block:begin -->

Pick the question surface per run, in this order:

1. **Question tool.** If a question tool is in your current tool list (Claude Code's
   built-in question tool, OMP `ask`), ask with it. Never call a question tool that is not in
   the list, and never treat its absence as an error.
2. **Chat turn.** No question tool, but the user can reply in the next turn (for example
   Codex or Cursor chat): use the text-mode fallback below.
3. **Headless.** No question tool and no chat turn (OMP `-p`, `--mode json`, `--mode rpc`;
   CI; any print-mode run): apply the skill's declared headless default. Never wait for
   input that cannot arrive.

**Subagents never ask.** A subagent returns the open choices, the evidence and its
recommended option to the parent, and the parent applies this policy.

<!-- ycc:verbatim-block:end -->

## Gate classes

| Class                 | Typical gate                                                     | Interactive | Headless default                                                                                                 |
| --------------------- | ---------------------------------------------------------------- | ----------- | ---------------------------------------------------------------------------------------------------------------- |
| `clarify`             | interviews, "STOP and ask" ambiguity gates, missing input path   | ask         | Print the open questions, write nothing, stop.                                                                   |
| `confirm-destructive` | delete, apply, install, overwrite, continue a cherry-pick + push | ask         | Do not act. Take the read-only path (report, dry-run, save) and print `rerun with <flag>`.                       |
| `confirm-publish`     | tag, release, publish, post a review, write release policy       | ask         | Stop before the side effect and print `rerun with <flag>`.                                                       |
| `choose-path`         | pick an action or execution mode                                 | ask         | Take the declared non-destructive option (save, sequential, enhance). If none exists, use `confirm-destructive`. |
| `checkpoint`          | review artifacts before continuing                               | ask         | Continue (artifacts are docs only) and report the checkpoint in the final summary.                               |
| `failure-recovery`    | batch or validation failure mid-run                              | ask         | Stop, preserve completed work, and print the failing batch plus the resume command. Never auto-retry or skip.    |

"Interactive" covers both a question tool and the text-mode fallback.

## Hard rules

1. Destructive and publishing gates never auto-approve without an explicit flag the skill
   documents (`--yes`, `--apply`, `--ci-yes`, `--publish`, …). A headless run is not
   consent.
2. The headless stop message is one plain-text line naming the exact rerun, for example:
   `Stopped before publish: headless run. Rerun with --publish --confirm.`
3. The declared default, not the `recommended` marker, governs a headless run.
   `recommended` is an interactive hint only and may differ from the declared default.
4. Never mark a destructive or publishing choice `recommended` in a `confirm-destructive` or
   `confirm-publish` question.
5. Auto-select on timeout is a user opt-in interactive behavior (for example OMP
   `ask.timeout > 0`, off by default). It picks the `recommended` option, which in a
   `choose-path` question may edit files (quick-review recommends "Apply fixes" or "Write
   file and apply fixes"). Rule 4 guarantees only that a timeout never confirms a
   destructive or publishing gate; it does not make a timed-out `choose-path` read-only.
6. A declared default never widens scope: it may only skip, stop, report, or take a
   non-destructive path.

## Declared defaults

Each asking skill carries one line near its arguments:

```
**Questions:** class `<class>`; headless (no question tool or chat turn): <default>. See `.../question-policy.md`.
```

A skill with several gates lists each class with its default. A skill-specific default
overrides the class default above only by being narrower. Declared defaults never replace
an interactive prompt: with a chat turn, use the text-mode fallback instead.

## Text-mode fallback

For chat runtimes without a question tool:

1. Print the question and the numbered choices, mark the `recommended` one, and end the turn.
2. Parse the next reply case-insensitively against the choice labels and the skill's
   documented synonyms.
3. Ambiguous reply: re-prompt once. Still ambiguous: take the least destructive choice
   (discard, cancel, or the read-only path).

## Runtime modes

<!-- ycc:verbatim-block:begin -->

| Runtime and mode                                           | Question surface         | Behavior                               |
| ---------------------------------------------------------- | ------------------------ | -------------------------------------- |
| Claude Code interactive                                    | built-in question tool   | ask; mark one `recommended` hint       |
| OMP TUI and `--mode rpc-ui`                                | `ask`                    | ask; mark one `recommended` hint       |
| Codex and Cursor chat                                      | none; text-mode fallback | print choices, wait for the reply      |
| OMP `-p`, `--mode json`, `--mode rpc`; CI; print-mode runs | none (headless)          | declared default, or stop + rerun hint |
| Any subagent                                               | none                     | return choices to the parent           |

OMP registers `ask` only when a UI is attached, so the same skill sees it in the TUI and
not in `-p`. Detect per run from the tool list; never assume from the runtime name.

<!-- ycc:verbatim-block:end -->
