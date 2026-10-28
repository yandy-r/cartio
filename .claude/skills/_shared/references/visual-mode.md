# Visual Mode — Canonical Reference

## Contents

- 0. Default Behavior
- 1. Timing — runs AFTER write AND validation
- 2. Composition with other flags
- 3. Hand-off to `visual-plan`
- 4. `/plan --visual` special case — force a write FIRST
- 5. NAMING_CONVENTION

Used by `prp-plan`, `plan`, `parallel-plan`, and `plan-workflow`
to expose a uniform `--visual` decorator. This file documents the **single
`--visual` contract** shared by all four planning skills: when it runs, how it
composes with other flags, how `--dry-run` short-circuits it, and the hand-off
to `visual-plan`. Individual skills own their own flag plumbing and the path
where they write a plan; the shared **invariant** lives here.

`--visual` is a **terminal decorator step**, not a dispatch mode. It does not
change how a plan is researched, dispatched, or written — it only runs once,
**after** the plan exists and has been validated, to produce a visual rendering
of that plan.

See `worktree-strategy.md` in this directory for where plan artifacts live
and `agent-team-dispatch.md` in this directory for the team lifecycle
that `--team` layers on top of planning.

---

## 0. Default Behavior

`--visual` is **off by default** on all four planning skills. When omitted, the
skill behaves exactly as it does today and writes no visual output.

| Skill               | Writes a plan artifact by default?   | `--visual` target                         |
| ------------------- | ------------------------------------ | ----------------------------------------- |
| `prp-plan`      | yes — resolved `prp-plan` artifact   | the written plan file                     |
| `parallel-plan` | yes — feature-dir `parallel-plan.md` | the written plan file                     |
| `plan-workflow` | yes — feature-dir `parallel-plan.md` | the written plan file                     |
| `plan`          | **no** — plan kept in-chat           | a plan file `--visual` is forced to write |

The plan path differs per skill (and `plan` writes none by default). The
contract therefore never hardcodes `docs/prps/plans`; it passes whatever
absolute plan path the skill produced to `visual-plan` and lets that skill
derive its output directory. See §3 (hand-off) and §4 (`plan` special case).

---

## 1. Timing — runs AFTER write AND validation

`--visual` is the **last** step of a planning run. The order is fixed:

1. Research / dispatch / plan generation (unchanged by `--visual`).
2. Write the plan artifact to its skill-specific path.
3. **Validate** the plan per the skill's existing rules.
4. **Only then** run the `--visual` decorator step.

If steps 1–3 do not complete (research aborts, write fails, or validation
fails), the `--visual` step does **not** run. There is nothing valid to
visualize, so the skill reports the upstream failure and stops.

`--visual` never re-orders, re-runs, or substitutes for any earlier phase.

---

## 2. Composition with other flags

`--visual` is **orthogonal** to and **composes with** the dispatch/runtime
flags. It is applied once, at the end, regardless of which of these are present:

| Flag            | Interaction with `--visual`                                                                      |
| --------------- | ------------------------------------------------------------------------------------------------ |
| `--parallel`    | Independent. Parallel dispatch finishes, plan is written + validated, then `--visual` runs once. |
| `--team`        | Independent. Team lifecycle completes first; `--visual` decorates the final plan.                |
| `--enhanced`    | Independent. Enhancement affects plan content; `--visual` renders the enhanced result.           |
| `--no-worktree` | Independent. Worktree opt-out does not affect whether or how `--visual` runs.                    |
| `--dry-run`     | **Short-circuits** `--visual` (see §2.1).                                                        |

Because `--visual` is a terminal decorator, the combination
`--parallel --team --visual` (etc.) means: do the parallel/team planning, write

- validate the plan, then run the single `--visual` step on the result.

### 2.1 `--dry-run` short-circuit

When `--dry-run` is present, `--visual` is **short-circuited**: the skill prints

```
visual generation would run
```

and does **not** generate anything and does **not** invoke `visual-plan`.
`--dry-run` always wins over `--visual`; no visual artifacts, directories, or
links are produced in a dry run.

---

## 3. Hand-off to `visual-plan`

When `--visual` runs for real (not `--dry-run`), the planning skill invokes:

```
visual-plan ${STORE_FLAG} <absolute-plan-path>
```

passing the **absolute path** of the plan file it wrote and validated, plus the exact `--store=local|linear` override as `STORE_FLAG` when the user supplied one (otherwise empty). Parse and strip the override before deriving descriptions or slugs; never let visual-plan re-resolve a conflicting config because the caller dropped the flag.

Contract for `visual-plan`:

- **Accept ANY plan path.** It must not assume `docs/prps/plans` or any other
  fixed location. The plan path is supplied by the caller and may live anywhere
  (e.g. a local-mode `docs/prps/plans/<name>.plan.md`, a feature-dir
  `parallel-plan.md`, or a forced-write file from `plan`).
- **Resolve `visual/` by store.** Local mode (default) keeps the sibling
  `<dirname of plan>/visual/`. Linear mode resolves kind `feature-dir` for the
  source feature slug and writes its `visual/` child, even if the input plan
  lives elsewhere. Follow artifact-store.md **Size and diagrams**: visual
  surfaces are local-only, never uploaded, and never linked by editing the
  source plan.
- **Forward the store override to every resolver call.** Print `--shell` and read `ARTIFACT_STORE` first; in linear mode apply the Linear call gate before `--ensure` or any cache write.
- **Print the resulting link to stdout.** Exactly one of:
  - a hosted shareable URL, or
  - a localhost preview `http://127.0.0.1:PORT/...`, or
  - `local files only` when no server/host is available.
- **NEVER re-run research or dispatch.** It only consumes an existing plan.
- **NEVER edit the plan file.** It reads the plan and writes only into the
  derived `visual/` directory; the plan artifact is left byte-for-byte intact.

The planning skill surfaces whatever link `visual-plan` prints; it does not
re-derive the path itself.

---

## 4. `/plan --visual` special case — force a write FIRST

`plan` writes **no** artifact by default (the plan stays in-chat). There is
therefore nothing on disk to visualize. When `--visual` is passed to
`plan`:

1. `plan` **MUST force a plan-file write first** using kind `prp-plan`
   through the artifact-store helper, producing an absolute path on disk.
   Local mode keeps `docs/prps/plans/<slug>.plan.md`; linear mode follows
   artifact-store.md **Planning skills** to Save the plan and task sub-issues.
2. That forced write is then validated like any other plan.
3. Only after a valid file exists does `plan` hand the absolute path to
   `visual-plan ${STORE_FLAG} <absolute-plan-path>`.

Without the forced write there is no input for `visual-plan`, so the
force-write rule is mandatory — not optional — for `plan --visual`. Under
`--dry-run` this is moot: the §2.1 short-circuit applies and `plan` prints
`visual generation would run` without forcing any write.

---

## 5. NAMING_CONVENTION

In-body references to skill assets use the plugin-root variable, e.g.:

- `.claude/skills/visual-plan/SKILL.md`
- `.claude/skills/plan/SKILL.md`
- `.claude/skills/prp-plan/SKILL.md`
- `.claude/skills/parallel-plan/SKILL.md`
- `.claude/skills/plan-workflow/SKILL.md`

The hand-off is always expressed as the skill invocation
`visual-plan ${STORE_FLAG} <absolute-plan-path>`, with `<absolute-plan-path>` resolved by
the calling skill to a real, absolute path on disk and `STORE_FLAG` retaining any user override.
