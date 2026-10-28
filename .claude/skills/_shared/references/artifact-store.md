# Artifact Store — Canonical Reference

## Contents

- Status
- Kinds
- Planning skills
- Implementation skills
- PR linking
- Layout
- Frontmatter
- Sync
- Store resolution
- Helper API
- Repository mapping
- Worktrees
- Gitignore
- Fresh clone
- Lookup
- Local mode
- Size and diagrams

Single source of truth for where planning artifacts live and how skills save, load, and sync them. The shell helper resolves config and paths only. Linear MCP calls live in skill bodies, never in the helper.

Shared script:

```bash
.claude/skills/_shared/scripts/resolve-artifact-store.sh
```

It sources `resolve-plans-dir.sh` from its own directory. Sourcing resolves from the current directory; an explicit `resolve_artifact_store <dir>` re-exports `PLANS_DIR` and `PLANS_ROOT` for that directory as well as the `ARTIFACT_*` variables.

## Status

Planning artifacts default to their existing local paths. Linear storage is opt-in until the default-store flip; see Store resolution.

| Piece                                       | Issue    | State             |
| ------------------------------------------- | -------- | ----------------- |
| `resolve-artifact-store.sh`                 | YAN-1153 | Shipped           |
| This reference                              | YAN-1152 | Shipped           |
| Planning skills and validators              | YAN-1154 | Migrated (opt-in) |
| Implementation skills and validators        | YAN-1155 | Migrated (opt-in) |
| Review and report skills                    | YAN-1156 | Not migrated      |
| Generators, capability matrix, `init`   | YAN-1157 | Shipped           |
| Existing `docs/plans` and `docs/prps` trees | YAN-1158 | Leave in place    |

## Kinds

| Kind                           | Linear object                                                       | Cache file                      |
| ------------------------------ | ------------------------------------------------------------------- | ------------------------------- |
| prd                            | Document on the feature Project                                     | `prd.md`                        |
| feature-research spec          | Document on the feature Project                                     | `feature-spec.md`               |
| prp-spec                       | Document on the feature Project                                     | `spec.md`                       |
| research                       | One Document per lens on the feature Project                        | `research/research-<lens>.md`   |
| shared-context                 | Document on the feature Project                                     | `shared.md`                     |
| plan-workflow / parallel-plan  | Document, plus one Issue per task                                   | `parallel-plan.md`              |
| prp-plan / `plan --visual` | Document, plus one Issue per task                                   | `prp-plan.md`                   |
| review                         | One Document per review, attached to the issue or feature Project   | `reviews/<review-id>-review.md` |
| report                         | Project status update, plus a Document when long                    | `report.md`                     |
| analysis                       | Local-only scratch; never pushed                                    | `analysis-*.md`                 |
| prp-plan research backstop     | Local-only scratch; never pushed                                    | `.prp-research/<role>.md`       |
| blueprint                      | Document on the feature Project                                     | `blueprint.md`                  |
| deep-research                  | Final report as research lens `deep-research`; scratch never pushed | `deep-research/` (scratch)      |

Document titles are `<slug> · <kind>`. `<kind>` is the cache filename stem, omitting the conventional `research/` and `reviews/` directories: `shared` → `shared.md`, `parallel-plan` → `parallel-plan.md`, `prp-plan` → `prp-plan.md`, `feature-spec` → `feature-spec.md`, `report` → `report.md`, `research-<lens>` → `research/research-<lens>.md`, and `<pr-N|local-<stamp>|quick-<stamp>>-review` → the matching `reviews/<review-id>-review.md`. `prd` and `spec` map to their same-named files. Load maps that title suffix back to the cache filename. Each split part uses `<basename>-part-N` as its kind and filename stem.

A review keeps its PR comment unchanged. With no reviewed issue, attach its Document to the feature Project; if no Project exists, keep the review local only.

`prp-implement` `{plan}-report.md` (kind `prp-report`), `implement-plan` (kind `report`) and `code-report` `report.md` (kind `report`) all use the report kind and share the `report.md` cache file. Local mode resolves `prp-report` to `docs/prps/reports/<slug>-report.md` under the current checkout and `report` to `<PLANS_DIR>/<slug>/report.md`; linear mode resolves both to `.ycc/artifacts/<slug>/report.md`. Store the status-update id in `linear.statusUpdate`; a long report also has `linear.document`.

### Plan task issues

- Plan tasks are sub-issues of one parent tracking issue. Use the verified branch-hint issue as parent when present; otherwise create one in the feature Project.
- `tasks` maps the literal task heading id (`1.1`, `Task 1`) to its Linear issue id. Warn that renumbering headings remaps ids and can associate issues with different tasks.
- The plan owns only each task issue's title, description, and `blocks` relations. Relations mirror dependency batches.
- Re-save updates mapped issues, creates only new task ids, and rebuilds a missing map from existing sub-issues by literal heading id before creating anything.
- Before updating a task issue, fetch its `updatedAt` and compare it with the timestamp stored under `tasks`. A mismatch, or no stored timestamp for an existing mapped issue, means Linear changed independently: skip the update and report it, or run Conflict. Never overwrite without resolving that state. After each successful update, re-stamp that task's `updatedAt`.
- Planning saves never change issue state except to cancel an **open** issue whose task was removed. Never cancel a Done or already-cancelled issue, and never delete an issue. Implementation skills move states only as in Implementation skills > Status sync.

## Planning skills

Planning skills resolve artifact paths with the helper rather than constructing cache paths themselves. Shell state does not persist between tool calls; retain the printed mode and resolved absolute paths. Parse and strip any `--store=local|linear` flag first, retain it as `STORE_FLAG` (empty when absent), and pass `${STORE_FLAG}` to every resolver call.

1. Resolve and **print** the mode; read `ARTIFACT_STORE=` from stdout:

   ```bash
   bash ".claude/skills/_shared/scripts/resolve-artifact-store.sh" ${STORE_FLAG} --shell
   ```

2. In linear mode only, apply the **Linear call gate** below before any write or `--ensure`. Dry runs stop before this step and only resolve paths without `--ensure`.
3. Once writing is permitted, resolve the destination:

   ```bash
   OUT="$(bash ".claude/skills/_shared/scripts/resolve-artifact-store.sh" ${STORE_FLAG} --ensure --kind=<kind> --slug=<slug>)"
   ```

In local mode (the default), write `$OUT` at the existing local path, without identity frontmatter or Linear calls. In linear mode, prepare identity as described below before writing, then Save.

| Skill               | Kind or path operation                                                                                                                                                                                                                                                        |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `prp-prd`           | `prd`                                                                                                                                                                                                                                                                         |
| `prp-spec`          | `spec`                                                                                                                                                                                                                                                                        |
| `prp-plan`          | `prp-plan`; research backstops use `prp-research` and remain local-only                                                                                                                                                                                                       |
| `plan --visual` | Forced source plan uses `prp-plan`                                                                                                                                                                                                                                            |
| `plan-workflow`     | Research uses `research-<lens>`, shared context uses `shared`, analysis uses `analysis-<name>`, and the final plan uses `parallel-plan`                                                                                                                                       |
| `parallel-plan`     | `parallel-plan`; prerequisites use the same research/shared/analysis kinds as `plan-workflow`                                                                                                                                                                                 |
| `shared-context`    | `shared`, with `research-<lens>` inputs                                                                                                                                                                                                                                       |
| `feature-research`  | `feature-spec` plus one `research-<lens>` document per research lens                                                                                                                                                                                                          |
| `deep-research`     | Linear working tree uses `deep-research`; save its final `report.md` as `research-deep-research`                                                                                                                                                                              |
| `blueprint`         | `blueprint`; derive the slug from `git rev-parse --path-format=absolute --git-common-dir`: its parent's basename when it is named `.git`, else its own basename minus `.git` (bare repo or submodule), else the current directory name outside Git; lowercase and sanitize it |
| `visual-plan`       | Reads the caller-selected `prp-plan` or `parallel-plan`; generated visual files follow Size and diagrams and are not uploaded                                                                                                                                                 |

`get_artifact_path <kind> <slug>` prints an absolute path without creating anything. Its `--kind=... --slug=...` CLI form requires both options. With a kind and slug, `--ensure` in linear mode calls `ensure_artifact_dir` to create the cache base and repository gitignore rule; in local mode it creates only the returned file's parent directory (or the returned directory for directory kinds), never an unrelated `PLANS_DIR`. Without a kind, `--ensure` retains the `ensure_artifact_dir` behavior below. Repo-level local kinds `prd`, `spec`, `prp-plan`, `prp-research`, and `blueprint` resolve under the current checkout's physical git toplevel, or the physical start directory outside git; feature kinds retain `PLANS_DIR`. Create any needed parent directory for the returned file when writing. An explicit deep-research `--output-dir` always wins. Local deep-research keeps `research/<subject>` and does not call `get_artifact_path deep-research`; its linear final Document is `<slug> · research-deep-research`, cached as `research/research-deep-research.md`.

For each uploaded planning document in linear mode:

1. Resolve the feature Project, apply the Linear call gate, and run `--ensure` before any cache write or pull. Read cached identity. If no `linear.document` identity exists (or a plan has no `tasks` identity), run Lookup steps 3–6 now, before writing; on a hit, Load the object first and preserve its identity so the new body replaces it through Conflict rather than creating a duplicate.
2. Write the body at the resolved path, preserving existing identity and non-identity frontmatter.
3. Run Save from the Sync section, including Lookup whenever document/task identity is missing and Conflict when remote state differs. Use Document title `<slug> · <kind>`.
4. For `prp-plan` and `parallel-plan`, also apply Plan task issues: create or update task sub-issues under the parent tracking issue and set `blocks` relations to mirror dependency batches and task dependencies. Keep task issue state and timestamp handling unchanged from that protocol.
5. Push only uploaded kinds. Analysis files, PRP research backstops, deep-research scratch, and visual output remain local-only.

Validators inspect content through `artifact_body` so leading identity frontmatter cannot satisfy or hide body headings. Research lookup uses `<feature>/research/` when that directory exists and `<feature>/` otherwise. Local mode retains the flat research filenames; linear mode writes them under `research/`.

Linear planning artifacts belong to the current worktree's `.ycc/artifacts/` cache. Do not move them between worktrees; Load pulls remote changes into a clean cache. Local mode retains the existing committed-docs worktree behavior.

Completion messages in linear mode print the implementation command with the feature slug or the cached plan path; see Implementation skills.

## Implementation skills

`implement-plan`, `prp-implement` and `orchestrate` consume planning artifacts through this store. The orchestrating skill alone talks to Linear; implementor subagents (`implementor`) read the hydrated cache file and never call a Linear MCP, tracker tool, or edit the plan or its frontmatter.

### Inputs

Each skill accepts, in addition to today's input (feature name, plan path, task description):

- `--store=local|linear`, parsed, stripped and forwarded to every resolver call exactly as in Planning skills.
- A Linear reference: an issue key matching `^[A-Z][A-Z0-9]{1,9}-[0-9]+$` exactly (uppercase; when `ARTIFACT_LINEAR_TEAM` is set its prefix must equal that team), or a `linear.app` issue, Project or Document URL. A bare Project slug or Document id counts as a reference only with an explicit `--store=linear`.
- A path to a cached plan. A file is a linear plan when it lies under `${ARTIFACT_ROOT}/.ycc/artifacts/` or its leading frontmatter has a `linear:` block or `pending: true`; any other path keeps local behavior unchanged and is never treated as a reference.

A Linear reference or a linear plan path selects linear mode for the run: when `STORE_FLAG` is empty, set `STORE_FLAG=--store=linear` before the first resolver call and forward it to every later call. Combined with `--store=local`, abort with: `A Linear plan reference requires linear mode; drop --store=local.` Anything else (a feature name such as `oauth-2`, a local plan path) follows normal Store resolution, and an existing local `${PLANS_DIR}/<name>/` wins over reference parsing, so the default (`local`) keeps today's behavior byte-for-byte: same paths, archive, report, no Linear calls.

### Resolve and hydrate

In linear mode, before Git branch preparation:

1. Resolve and print the mode (`--shell`), then apply the Linear call gate. A missing or unauthorized MCP stops the run with the gate message; there is no local fallback for a plan that exists only in Linear.
2. Run Lookup for the plan kind (`parallel-plan` for `implement-plan`, `prp-plan` for `prp-implement`). Map the hit to a slug: the Document title prefix before `·`, else the feature Project name. An issue reference with sub-issues is the parent tracking issue; any other issue resolves through its parent. Either way the run covers the whole plan. Lookup step 7 asks; never report "no plan" before querying Linear.
3. Hydrate once with Load into the current checkout's cache: `--ensure --kind=<kind> --slug=<slug>`, then pull the Document body and stamp identity. `implement-plan` also hydrates `shared` when that Document exists; a missing `shared` Document is a warning, like a missing local `shared.md`. Load's Conflict rule applies; never overwrite unpushed local edits.
4. Use the absolute cache path as `PLAN_PATH` for parsing, prompts and validation. Linear-mode plans are never moved into the feature worktree (`move-plan-to-worktree.sh` is skipped); worktree tasks read the plan by absolute path. Derive `WT_FEATURE_SLUG` (and any team context) from the cache slug, never from the cache basename, sanitized for branch preparation: lowercase, replace `[^a-z0-9-]` with `-`, collapse runs, trim `-`, truncate to 40 characters. Resolver calls keep the unsanitized cache slug. The cache itself is gitignored; the only dirty path `--ensure` can leave is a `.gitignore` (at `ARTIFACT_ROOT`, which may be a package directory) whose sole change adds the `.ycc/artifacts/` rule, possibly after a missing final newline; branch preparation accepts exactly that change and carries it onto the feature branch.
5. Read the `tasks` map from the plan's frontmatter. If it is missing, rebuild it in memory from the parent tracking issue's sub-issues by literal task heading id, as in Plan task issues, without creating issues or writing the map to the cache. A task with no issue still runs; list it as untracked in the report and suggest re-saving the plan with its planning skill.

### Status sync

The orchestrator moves issues by state type, not team-specific state names:

| When                                              | Change                                                                                                                                                               |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Run starts: immediately before the first dispatch | Replay pending transitions first (below). Parent tracking issue → `started` when `backlog`, `unstarted` or `triage`; Project → `started` when `backlog` or `planned` |
| Before a task or batch dispatches                 | Each task issue in it → `started` when `backlog`, `unstarted` or `triage`                                                                                            |
| Task passes its validation                        | Task issue → `completed`. `prp-implement` parallel runs mark a batch's issues after its between-batch checks pass                                                    |
| Task or batch fails                               | Leave the issue `started`; add one comment with the task id, the failing check and a short excerpt. Never paste secrets or full logs                                 |
| All tasks pass final validation                   | Parent tracking issue → `completed`; post the report (below)                                                                                                         |

- A task whose issue is already `completed` is treated as done and skipped, so a re-run resumes; a `canceled` issue's task is skipped as removed. Print both in the summary.
- Never move an issue backwards, reopen it, change its assignee, or touch issues outside the plan's `tasks` map and parent.
- Timestamps: before a transition or comment, fetch the issue's `updatedAt`. If it equals the stored `tasks.<id>.updatedAt`, re-stamp it from the response afterwards so a later planning re-save does not mistake the transition for an independent edit. If it differs, or no stamp is stored, apply the transition but leave the stored stamp unchanged so planning still sees the independent edit. Identity keys are outside `contentHash`, so re-stamping never marks the body dirty.
- `--dry-run` makes no Linear writes and no local writes: no `--ensure`, no cache write, no Load push or Conflict, and no branch or worktree preparation. It may run Lookup, reuse an existing cache file read-only, or parse a fetched body from a temporary file outside the repository, which then serves as `PLAN_PATH` for the dry-run output.

### Report and completion

- Write the run report to kind `prp-report` (`prp-implement`) or `report` (`implement-plan`, `orchestrate` with a Project) and Save it on success and failure alike: one Project status update with health `onTrack` when every task passed and `atRisk` otherwise, plus a Document when the report exceeds the status-update size. Record `linear.statusUpdate`.
- Linear mode replaces `prp-implement`'s archive move. Instead, close the issues as above and complete the Project only when its name equals the plan slug, it carries `ARTIFACT_LINEAR_LABEL` when one is set, it is not `ARTIFACT_LINEAR_PROJECT`, and every issue in it is `completed` or `canceled`. Otherwise leave the Project open and say why.
- The Goal Signal `PLAN_ARCHIVED` keeps its name. Local mode: the plan moved to `completed/`. Linear mode: the parent tracking issue and every tracked task issue are `completed` (or `canceled`) and the Project status update was posted.
- If the MCP becomes unreachable mid-run, keep implementing. At the moment a transition fails, resolve the report path with `--ensure`, append the transition under `## Linear sync pending` in that file and set its `pending: true`, so an interrupted run still leaves the record; the final report merges into the same file. Print the list at the end. The next run on the same plan replays the pending transitions before dispatching, following Offline, and removes each one it applies.

### Orchestrate

`orchestrate` builds its own decomposition. Its Linear behavior applies only when its input is a Linear issue reference (a `store: linear` config alone changes nothing): it reads that issue's title, description and sub-issues as the task description before deriving any slug, moves the issue to `started` immediately before the first dispatch, comments on failure, saves a `report` when the issue has a Project (on success and failure), and marks the issue `completed` only when the run's final verification passes. `--plan-only` keeps writing `docs/orchestration/`; that path is not an artifact-store kind.

## PR linking

`prp-pr` and `git-workflow` add Linear and GitHub issue links to the PR body's related-issues section:

1. Collect candidates with `collect-linear-keys.sh --base origin/<base>` (always the remote-tracking base, so commits already merged upstream are never scanned). It prints `<KEY>\t<source>\t<closes|refs>`: the branch hint (`branch`, `closes`) and commit keywords (`commit`; `Closes`/`Fixes`/`Resolves` → `closes`, `Refs` → `refs`; a key seen with both keeps `closes`). Also add the `linear.issue` of a linear plan implemented on this branch as `plan`, `closes`: use the cache path known in the session, else `.ycc/artifacts/<slug>/{parallel-plan,prp-plan}.md` where `<slug>` is the branch name without a leading `feat/`, checking the current checkout and then the main checkout (parent of `git rev-parse --path-format=absolute --git-common-dir`).
2. Verify each key in Linear when a Linear MCP is reachable; drop keys that do not resolve. Without an MCP, keep only keys whose team equals `ARTIFACT_LINEAR_TEAM`; with no team configured, keep none and print `Skipping Linear issue links: no ARTIFACT_LINEAR_TEAM configured and no Linear MCP to verify keys.` only when candidates existed, because unverified branch hints can be false positives (`fix/utf-8-…`).
3. Emit `Closes <KEY>-<n>` for each verified `closes` issue and `Refs <KEY>-<n>` for each `refs`-only issue, plus `Closes #<n>` for each GitHub issue attached to a closed Linear issue (Linear attachment URLs `https://github.com/<owner>/<repo>/issues/<n>` matching `origin`) or closed by a commit (`Closes|Fixes|Resolves #<n>`). Never duplicate a line already in the body or template.
4. In linear mode, link the Linear Project and plan Document URLs instead of `docs/prps` paths in the artifacts section.

This applies in both store modes: linking needs only the hint and, when available, the MCP.

## Layout

Linear mode stores a gitignored cache:

```text
.ycc/artifacts/<feature-slug>/
├── prd.md
├── feature-spec.md
├── spec.md
├── prp-plan.md
├── parallel-plan.md
├── shared.md
├── report.md
├── analysis-*.md
├── reviews/
│   ├── pr-<N>-review.md
│   ├── local-<stamp>-review.md
│   └── quick-<stamp>-review.md
├── research/
│   ├── research-business.md
│   ├── research-technical.md
│   ├── research-ux.md
│   ├── research-security.md
│   ├── research-practices.md
│   ├── research-external.md
│   ├── research-recommendations.md
│   └── research-deep-research.md
├── blueprint.md
├── deep-research/
│   └── report.md
└── .prp-research/
    └── <role>.md

```

`blueprint.md` is a sibling of `shared.md`. The `deep-research/` directory contains the skill's local-only objective, findings, synthesis, evidence, and working report; only the final report is uploaded as research lens `deep-research`.

`plan-workflow` and `parallel-plan` both write `parallel-plan.md`; there is no plan-workflow `plan.md`. `prp-plan` and the forced file from `plan --visual` use `prp-plan.md`. Feature-research and PRP specs remain distinct. Analysis files and PRP research backstops keep their current basenames under the slug directory but are never pushed.

| Cache file (linear)                                     | Today's path (local)                                                  |
| ------------------------------------------------------- | --------------------------------------------------------------------- |
| `.ycc/artifacts/<slug>/prd.md`                          | `docs/prps/prds/<name>.prd.md`                                        |
| `.ycc/artifacts/<slug>/feature-spec.md`                 | `docs/plans/<slug>/feature-spec.md`                                   |
| `.ycc/artifacts/<slug>/spec.md`                         | `docs/prps/specs/<name>.spec.md`                                      |
| `.ycc/artifacts/<slug>/prp-plan.md`                     | `docs/prps/plans/<name>.plan.md`                                      |
| `.ycc/artifacts/<slug>/parallel-plan.md`                | `docs/plans/<slug>/parallel-plan.md`                                  |
| `.ycc/artifacts/<slug>/shared.md`                       | `docs/plans/<slug>/shared.md`                                         |
| `.ycc/artifacts/<slug>/research/research-<lens>.md`     | `docs/plans/<slug>/research-<lens>.md`                                |
| `.ycc/artifacts/<slug>/reviews/pr-<N>-review.md`        | `docs/prps/reviews/pr-<N>-review.md`                                  |
| `.ycc/artifacts/<slug>/reviews/local-<stamp>-review.md` | `docs/prps/reviews/local-<stamp>-review.md`                           |
| `.ycc/artifacts/<slug>/reviews/quick-<stamp>-review.md` | `docs/prps/reviews/quick-<stamp>-review.md`                           |
| `.ycc/artifacts/<slug>/report.md`                       | `docs/plans/<slug>/report.md` or `docs/prps/reports/<plan>-report.md` |
| `.ycc/artifacts/<slug>/analysis-*.md`                   | `docs/plans/<slug>/analysis-*.md`                                     |
| `.ycc/artifacts/<slug>/.prp-research/<role>.md`         | `docs/prps/plans/.prp-research/<slug>/<role>.md`                      |
| `.ycc/artifacts/<slug>/blueprint.md`                    | `docs/blueprint.md`                                                   |
| `.ycc/artifacts/<slug>/deep-research/`                  | `research/<subject>/` or the skill's explicit `--output-dir`          |

### Slugs

1. Use the skill's feature name when it has one. In particular, `code-report` uses its supplied feature name.
2. For an artifact with no feature name, verify `ARTIFACT_LINEAR_HINT` when set and first look for an existing cache directory whose frontmatter `linear.project` matches that issue's Project. Reuse its slug.
3. Otherwise use lowercased `ARTIFACT_LINEAR_HINT` when set.
4. Otherwise sanitize the branch name: lowercase it, replace `/` and every character outside `[a-z0-9._-]` with `-`, collapse repeated `-`, and trim leading/trailing separators.
5. If no valid slug remains, ask the user.

`get_feature_artifact_dir` accepts only `^[A-Za-z0-9][A-Za-z0-9._-]*$` and rejects `/` and any `..` path part. This is stricter than `get_feature_plan_dir`; see Local mode for legacy names.

## Frontmatter

In linear mode, merge identity keys into existing frontmatter as top-level keys; do not replace other keys or nest them under a namespace. Local mode writes no identity frontmatter.

| Field                 | Meaning                                                                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `linear.workspace`    | Linear workspace slug                                                                                                   |
| `linear.team`         | Team key, for example `YAN`                                                                                             |
| `linear.project`      | Feature Project id or slug                                                                                              |
| `linear.document`     | Document id, empty when there is no Document                                                                            |
| `linear.issue`        | Parent tracking issue, reviewed issue when present, otherwise empty                                                     |
| `linear.statusUpdate` | Report status-update id, empty for other kinds                                                                          |
| `tasks`               | Literal plan task id → `{issue, updatedAt}` from the last successful sync; only on `parallel-plan.md` and `prp-plan.md` |
| `syncedAt`            | Linear `updatedAt` observed at the last sync, stored verbatim in its ISO-8601 form                                      |
| `contentHash`         | Lowercase hex SHA-256 from the procedure below at the last successful push or pull                                      |
| `pending`             | `true` when a reachable Linear configuration is temporarily unreachable; otherwise omit or set `false`                  |

Compare `updatedAt` with `syncedAt` using **differs from**, not chronological ordering or timestamp normalization.

```yaml
---
title: Artifact store implementation plan
linear:
  workspace: yandy-r
  team: YAN
  project: artifact-store
  document: 7b1e3c0a-0000-4000-8000-000000000000
  issue: YAN-1152
  statusUpdate:
tasks:
  '1.1':
    issue: YAN-1153
    updatedAt: '2026-10-26T00:00:00.000Z'
  'Task 2':
    issue: YAN-1154
    updatedAt: '2026-10-26T00:00:00.000Z'
syncedAt: '2026-10-26T00:00:00.000Z'
contentHash: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
pending: false
---
```

### Body, hash, and push bytes

1. Frontmatter exists only as a block starting with `---` at byte 0 and closed by a later `---` line (ignoring CR on delimiter lines), whose first non-blank line is a top-level YAML key: it matches `^[A-Za-z_][A-Za-z0-9_.-]*:([[:space:]]|$)`. Blank-only blocks also qualify. Leading `#` comment lines may precede that key only when the block also has a top-level identity key (`linear`, `syncedAt`, `contentHash`, `pending`, or `tasks`), so a hand-annotated cache keeps its identity. Otherwise the whole file is body, including a local artifact that opens with a thematic break, a heading, or a URL; a `---` anywhere else is body content.
2. Start with the exact file bytes. Remove only the complete top-level `linear`, `syncedAt`, `contentHash`, `pending`, and `tasks` entries from that block. Do not re-serialize YAML or remove other keys.
3. Normalize CRLF and CR line endings to LF. Do not trim the result or add/remove its trailing newline; preserve every other byte, including non-identity frontmatter.
4. SHA-256 those bytes for `contentHash`. Push the same identity-stripped content to Linear: identity values are never pushed, while non-identity frontmatter remains in the pushed and hashed body. Apply the same procedure after pulls.

Validators that inspect headings or body structure must skip frontmatter (`artifact_body`).

## Sync

Skills run these procedures; the helper never calls Linear.

### Linear call gate

- Before any local write in linear mode, ensure a Linear MCP is installed and authorized. If not, stop and suggest `store: local`; do not create a cache, `.gitignore`, or perpetual pending file.
- Before any Linear call, confirm the connected workspace equals `ARTIFACT_LINEAR_WORKSPACE` when set. On mismatch, ask the user before proceeding.
- Only temporary unreachability of an installed, authorized MCP follows Offline.

### Save

1. Resolve the store and check `${ARTIFACT_STORE:-}`.
2. In linear mode, apply the Linear call gate. Offer pointer setup when the repo-root `.plans-config` lacks an effective `store: linear` or is not tracked (`git --literal-pathspecs -C <repo-root> ls-files --error-unmatch -- .plans-config` fails), independently of `ARTIFACT_STORE_SOURCE`. Gather missing values like init: workspace slug from Linear MCP `get_workspace`, team from `ARTIFACT_LINEAR_TEAM` or `list_teams` (use the sole team, ask when several), and label `repo:<repo-name>` from the `origin` remote basename minus `.git`, falling back to the main checkout directory name (parent of `git rev-parse --path-format=absolute --git-common-dir`). Existing `ARTIFACT_LINEAR_*` hints override these defaults; a project hint replaces the label. Ask once whether to run `artifact-store-pointer.sh --store=linear --linear-workspace=… --linear-team=… (--linear-label=… | --linear-project=…)` so fresh clones find Linear. Headless runs, no answer, unresolved values, declining, or helper failure produce a one-line notice and continue; this offer never blocks Save. Then call `ensure_artifact_dir` before any cache write or pull and read cached identity. If the cache file is absent or has no `linear.document` identity (or, for a plan, no `tasks` identity), run Lookup steps 3–6 before creating anything; reuse its Document, status update, parent, and task issues rather than duplicating them. On a hit, Load first, preserve that identity, and use Conflict before replacing its body when no prior sync state exists.
3. Write the new body. Local mode writes today's path without identity frontmatter and **stops here**. Linear mode preserves existing identity frontmatter, including `tasks`, and all non-identity keys; only the body is replaced.
4. Before updating an existing object, fetch its `updatedAt`. If it differs from `syncedAt`, run Conflict and do not push until answered. A discovered existing object with no prior `syncedAt` also requires Conflict before its body is replaced.
5. Create or update the object using the Kinds and naming rules. For plans, apply the task-issue rules above. On creation, record all returned ids.
6. After a successful push, stamp `syncedAt` verbatim from the response, recompute `contentHash`, update `tasks` and `linear.statusUpdate` as applicable, and clear `pending`.

If the MCP fails during step 4 or 5, keep the local file and follow Offline.

### Load

1. An explicit path, URL, or id selects that object; otherwise run Lookup.
   Before any pull writes the cache, apply the Linear call gate and resolve the selected kind and slug with `--ensure` (forwarding `STORE_FLAG`) to establish the safe cache and gitignore rule.
2. If the local file is missing, pull it, write the cache file, and stamp identity frontmatter.
3. If it exists, compute its current body hash and read `syncedAt`.
4. If Linear `updatedAt` differs from `syncedAt` and the current hash equals `contentHash` with no pending edit, pull; Linear wins because the cache is clean.
5. If Linear differs and the local hash differs from `contentHash` or `pending` is true, run Conflict; both sides changed.
6. If Linear matches `syncedAt`, keep the local file. When `pending` is true or its hash differs from `contentHash`, run Save from step 4 to push it.

### Conflict

1. Ask the user to keep local, take Linear, or show a diff. After showing a diff, ask again until the user chooses keep or take.
2. Keep local: push immediately against the acknowledged remote state, then stamp `syncedAt` and `contentHash` from the successful push. Do not prompt again for the same remote change.
3. Take Linear: replace the body, then stamp `syncedAt` and `contentHash` from the successful pull.
4. Never silently discard either side.

### Offline

1. For temporary MCP unreachability, warn once and continue locally.
2. Keep the local body and set `pending: true`.
3. On the next Load or Save that reaches Linear, push through the normal remote checks and clear `pending` after success.
4. If the remote also differs, run Conflict; never push or pull over the other side silently.

## Store resolution

`resolve_artifact_store` exports variables without writing files. First hit wins; accepted values are only `linear` and `local`.

1. `--store=` argument → `ARTIFACT_STORE_SOURCE=flag`.
2. Nearest in-scope `.plans-config` defining `store:` → `config`.
3. `${XDG_CONFIG_HOME:-$HOME/.config}/ycc/config`, key `store:` → `global`.
4. Default `local` → `default`.

An invalid value at the selected level does not fall through. An explicit resolve prints an error and returns 1; sourced auto-resolution prints `WARNING`, unsets every `ARTIFACT_*` variable, and does not fail the caller.

The default is intentionally `local` until the default-store flip (YAN-1618). Linear mode is opt-in through `--store=linear` or `store: linear`; set it up for a repo with `init --artifact-store=linear` or `artifact-store-pointer.sh` (see Fresh clone). Local resolution and writes do not require a Linear MCP.

The `store` and `linear_*` walks compare physical paths (`pwd -P`) and stop after the start directory's physical Git toplevel, or physical `PLANS_ROOT` without Git. Symlinked paths therefore cannot escape the boundary. The original start directory still goes to `resolve_plans_dir`, whose upward/topmost `PLANS_*` behavior is unchanged.

Each hint inherits independently from the nearest in-scope `.plans-config` defining that key. Unset keys export as empty strings. The global file is consulted only for `store:`.

| Config key         | Exported variable           |
| ------------------ | --------------------------- |
| `linear_workspace` | `ARTIFACT_LINEAR_WORKSPACE` |
| `linear_team`      | `ARTIFACT_LINEAR_TEAM`      |
| `linear_project`   | `ARTIFACT_LINEAR_PROJECT`   |
| `linear_label`     | `ARTIFACT_LINEAR_LABEL`     |

In linear mode, `ARTIFACT_ROOT` is `PLANS_ROOT` unless `PLANS_ROOT` lies outside the start directory's Git toplevel; then it is that Git toplevel, keeping a nested worktree's cache in the worktree. In local mode, `ARTIFACT_ROOT` is `PLANS_ROOT` and `ARTIFACT_DIR` is `$PLANS_DIR`. In linear mode, `ARTIFACT_DIR` is `${ARTIFACT_ROOT}/.ycc/artifacts`.

### Branch hint

`ARTIFACT_LINEAR_HINT` comes from `git -C <start-dir> branch --show-current`; it is empty for detached HEAD or no Git checkout. Skills verify the hint in Linear before using it.

- With a team, match only `<TEAM>-<digits>` case-insensitively. After uppercasing, the team must match `^[A-Z0-9]+$`; otherwise return an empty hint.
- Without a team, use prefix `[A-Za-z][A-Za-z0-9]{1,9}` with the same rules.
- The key must begin at the branch start or after `/`, `_`, or `-`, and end at `-`, `_`, `/`, or end of string.
- Use the first match and export it uppercased. `yramirez/yan-1152-foo` and `feat/YAN-1152-x` both yield `YAN-1152`.

## Helper API

| Variable                    | Value                                                                                                              |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `ARTIFACT_STORE`            | `linear` or `local`                                                                                                |
| `ARTIFACT_STORE_SOURCE`     | `flag`, `config`, `global`, or `default`                                                                           |
| `ARTIFACT_ROOT`             | Linear: `PLANS_ROOT`, or the start directory's Git toplevel when `PLANS_ROOT` lies outside it. Local: `PLANS_ROOT` |
| `ARTIFACT_DIR`              | Linear: `${ARTIFACT_ROOT}/.ycc/artifacts`. Local: `$PLANS_DIR`                                                     |
| `ARTIFACT_LINEAR_WORKSPACE` | `linear_workspace`, or empty                                                                                       |
| `ARTIFACT_LINEAR_TEAM`      | `linear_team`, or empty                                                                                            |
| `ARTIFACT_LINEAR_PROJECT`   | `linear_project`, or empty                                                                                         |
| `ARTIFACT_LINEAR_LABEL`     | `linear_label`, or empty                                                                                           |
| `ARTIFACT_LINEAR_HINT`      | Branch issue-key hint, or empty; skills must verify it in Linear                                                   |

| Function                                                     | Behavior                                                                                                                                                             |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `resolve_artifact_store [--store=linear\|local] [start-dir]` | Export all variables and re-export `PLANS_DIR`/`PLANS_ROOT` for the start directory. No writes. Return 1 on invalid input.                                           |
| `get_feature_artifact_dir <slug>`                            | Print `${ARTIFACT_DIR}/<slug>` after the slug validation above. Do not create it.                                                                                    |
| `get_artifact_path <kind> <slug>`                            | Print the kind's absolute local or linear path. Validate the slug like `get_feature_artifact_dir` and research/analysis suffixes as lowercase kebab-case. No writes. |
| `ensure_artifact_gitignore`                                  | Linear: ensure the repository gitignore rule. Local: return 0 without writing.                                                                                       |
| `ensure_artifact_dir`                                        | Create `ARTIFACT_DIR`, then run `ensure_artifact_gitignore`; the write-on-first-save entry point.                                                                    |

Pointer writer (not sourced; see Fresh clone for the CLI and exit codes):

```bash
bash ".claude/skills/_shared/scripts/artifact-store-pointer.sh" --store=linear|local [--linear-workspace=<slug>] [--linear-team=<KEY>] [--linear-label=<label> | --linear-project=<id-or-slug>] [--dry-run] [--no-commit] [--force] [<dir>]
```

It sources `resolve-artifact-store.sh` for `ensure_artifact_gitignore` and writes only `.plans-config`, `.gitignore`, and a pathspec commit of those paths.

Executed form; default output is `ARTIFACT_DIR` only:

```bash
bash ".claude/skills/_shared/scripts/resolve-artifact-store.sh" [--store=linear|local] [--debug] [--shell] [--kind=<kind> --slug=<slug>] [--ensure] [start-dir]
```

- `--shell` prints every existing `ARTIFACT_*` mode/config variable using Bash `printf %q`; planning skills print it and read `ARTIFACT_STORE` from stdout rather than silently evaluating it. `ARTIFACT_REPO_ROOT` is used by sourced local path resolution but is not added to `--shell` output.
- `--help` prints usage. `--debug` or `PLANS_DEBUG=1` enables debug output.
- `--kind` and `--slug` together print `get_artifact_path`; without them, default output remains `ARTIFACT_DIR`. With both options, local `--ensure` creates only that file's parent or that directory itself; linear `--ensure` calls `ensure_artifact_dir` for the cache base and gitignore rule. Without kind/slug, `--ensure` calls `ensure_artifact_dir` unchanged.
- Report kinds: `report` resolves locally to `${PLANS_DIR}/<slug>/report.md`; `prp-report` resolves to `<repo>/docs/prps/reports/<slug>-report.md` under the current checkout. Both resolve to `.ycc/artifacts/<slug>/report.md` in linear mode. Local `--ensure` creates only the parent directory.
- Sourcing auto-resolves without `exit` or enabling `set -e`; check `${ARTIFACT_STORE:-}` before use. Call `resolve_artifact_store` explicitly to pass a flag/start directory or receive invalid-input status.

```bash
# shellcheck source=/dev/null
source ".claude/skills/_shared/scripts/resolve-artifact-store.sh"
```

For body-only validation, source or execute `artifact-body.sh`. It removes only a leading block that qualifies as frontmatter under rule 1 of Body, hash, and push bytes; CRLF delimiters are accepted. A leading thematic break, an unterminated block, and every other file are printed unchanged. Body bytes after the block are not normalized.

```bash
# shellcheck source=/dev/null
source ".claude/skills/_shared/scripts/artifact-body.sh"
artifact_body path/to/artifact.md
bash ".claude/skills/_shared/scripts/artifact-body.sh" path/to/artifact.md
```

For PR-linking candidates, run the read-only collector (it never calls Linear):

```bash
bash ".claude/skills/_shared/scripts/collect-linear-keys.sh" [--base <ref>] [--team <TEAM>] [start-dir]
```

It prints deduplicated `<KEY><TAB><source><TAB><closes|refs>` rows, uppercased, with the branch hint first (`branch`, `closes`) and commit keys next (`commit`) in Git log order. Commit candidates use case-insensitive `Closes`, `Fixes` or `Resolves` (→ `closes`) or `Refs` (→ `refs`) followed by a colon or space and a key with the branch-hint prefix shape `[A-Za-z][A-Za-z0-9]{1,9}-<digits>`; a key seen with both keywords reports `closes`. The team filter defaults to `ARTIFACT_LINEAR_TEAM`; `--team ''` collects all teams. A `--base` naming a branch that has a remote-tracking `origin/<base>` uses the remote ref. The default base is the merge base with `origin/HEAD`, falling back to `origin/main`, then `main`; if none resolves, commit scanning is skipped with a warning on stderr. No candidates is a successful empty result; bad arguments fail. Apply PR linking's verification rule to every candidate before emitting a link line.

## Repository mapping

Default: one Linear Project per feature, named exactly `<slug>`, inside `ARTIFACT_LINEAR_TEAM`, with `ARTIFACT_LINEAR_LABEL` applied. Reuse a matching Project or create it on first save.

Alternative: set `linear_project` to a fixed Project id/slug for repos using one Project with milestones. Lookup step 5 then uses that Project instead of label discovery.

A plan's parent tracking issue is the verified hint issue when present; otherwise create one in the feature Project. The helper only supplies hints; skills perform all verification and creation.

## Worktrees

- Linear mode does not move plan files between worktrees. Planning skills use Load to pull into a worktree cache instead of following the committed-docs move in `worktree-strategy.md`; local mode retains that existing behavior. Implementation skills hydrate the plan once into the invoking checkout's cache and pass its absolute path to worktree tasks (see Implementation skills). Pushing from the main checkout does not refresh another worktree's clean cache: Load's differing-`updatedAt` rule pulls the new remote body there.
- Push every pending artifact before removing a worktree. Unpushed pending files block routine cleanup.
- Lookup step 2 checks both the current worktree cache and the main checkout cache, so per-worktree roots do not hide existing work.

## Gitignore

Ignore `.ycc/artifacts/` only, never all of `.ycc/`; a committed pointer may live there later.

In linear mode, both `ensure_*` functions refuse when `ARTIFACT_ROOT` is outside a Git work tree. They also refuse symlinked `${ARTIFACT_ROOT}/.gitignore`, `${ARTIFACT_ROOT}/.ycc`, or `ARTIFACT_DIR` rather than writing through them. Local mode never edits Git metadata; CLI `--ensure --kind=... --slug=...` creates only the resolved parent/directory, while `ensure_artifact_dir` (or bare `--ensure`) creates `ARTIFACT_DIR`.

The linear gitignore check returns without editing when either condition holds:

- `git check-ignore -v --no-index` ignores `.ycc/artifacts/probe` and the reported source is a `.gitignore` inside this repository. Global excludes and `.git/info/exclude` do not count.
- `${ARTIFACT_ROOT}/.gitignore` already contains `.ycc/artifacts/`, `/.ycc/artifacts/`, `.ycc/artifacts`, or `/.ycc/artifacts`.

Otherwise append `.ycc/artifacts/`, creating `.gitignore` if needed and adding a missing trailing newline before the entry. Skills call `--ensure` after the Linear gate and before the first cache write or pull; `artifact-store-pointer.sh` (run by `init --artifact-store=linear`) applies the same rule when it writes the committed pointer.

## Fresh clone

A fresh clone finds Linear from committed `.plans-config`. Only the `store`/`linear_*` lookup is bounded to the repository; `resolve-plans-dir.sh` retains its existing unbounded upward/topmost config behavior for `PLANS_*`.

```text
plans_dir: docs/plans
store: linear
linear_workspace: yandy-r
linear_team: YAN
linear_label: repo:ystack
```

A fixed-Project repo uses `linear_project: <id-or-slug>` instead of `linear_label`. No `scope: local` is needed: a nested worktree uses its own Git toplevel for the linear cache even when a higher config determines `PLANS_ROOT`.

The generated `AGENTS.md` carries this section, rendered by `init` from the `IF_ARTIFACT_STORE` block of its `AGENTS.md.tmpl` when Phase 5.6 succeeds with a linear pointer (or the root pointer is already linear and tracked):

```text
## Planning artifacts

Planning artifacts live in Linear (see `.plans-config`); `.ycc/artifacts/` is a gitignored local cache. If it's missing, pull from Linear.
```

The pointer is written and committed by `artifact-store-pointer.sh`, which `init --artifact-store=linear` runs once and Save offers when no committed pointer exists:

```bash
bash ".claude/skills/_shared/scripts/artifact-store-pointer.sh" --store=linear|local [--linear-workspace=<slug>] [--linear-team=<KEY>] [--linear-label=<label> | --linear-project=<id-or-slug>] [--dry-run] [--no-commit] [--force] [<dir>]
```

- `<dir>` defaults to `.`; the target is its physical git toplevel. `--store=linear` requires `--linear-workspace`, `--linear-team`, and one of `--linear-label` or `--linear-project`. Workspace, label, and project values must match `^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$`; team keys must match `^[A-Z][A-Z0-9]{0,9}$`.
- Sets `store:` and each provided `linear_*` key in the root `.plans-config` in place, preserving every other line, comment, and key order; new keys are appended. A differing existing value, or switching from an existing `linear_project` to `--linear-label` (or vice versa), exits `3` unless `--force`; forcing a mapping switch removes the other key's line. Identical values without a final newline remain `pointer_change=unchanged`, without a rewrite or commit.
- All refusal checks happen before any write: invalid or unsafe values/paths, symlinks, conflicts, CRLF pointer files, an ignored `.plans-config` (for either store), and linear gitignore writability. CRLF exits `2` with `convert .plans-config to LF line endings`. When the root pointer is new, tracked or untracked nested `*/.plans-config` files without `scope: local` also refuse with exit `3`, naming each path, because a root pointer would move their plans directory; `--force` overrides this refusal. For linear the helper ensures the `.ycc/artifacts/` gitignore rule (`ensure_artifact_gitignore`) before writing the pointer; dry runs perform the same refusal checks without writing.
- Commits only the pointer paths with `chore(ycc): configure Linear artifact store` (`configure local artifact store` for local); `.gitignore` joins the commit only when its sole diff is the added rule. Other staged changes stay staged. Pre-existing unstaged or staged `.plans-config` changes versus HEAD cause a warning and `commit=skipped`. An untracked existing pointer is also treated as dirty when it contains keys other than `store` and `linear_*` (comments and blank lines are ignored). In either case files are still written and the helper exits `0`, preserving the user's commit boundary. `--no-commit` also skips the commit; `--dry-run` writes nothing and prints the planned `.plans-config`.
- Exit codes: `2` usage, invalid/unsafe value, not a git work tree, symlinked target, CRLF pointer, or gitignore writability failure; `3` existing value, label/project mapping, or new-root/nested-config conflicts (use `--force`); `4` `.plans-config` is gitignored; `5` commit failed (files stay written, but the paths staged by the helper are unstaged, including on an unborn branch).
- Stdout ends with `pointer=<abs path>`, `pointer_change=created|updated|unchanged`, `gitignore=added|present|skipped`, `commit=<short sha>|skipped|none`.

A user-level store override uses the same key format:

```text
# ${XDG_CONFIG_HOME:-$HOME/.config}/ycc/config
store: local
```

## Lookup

In linear mode, stop at the first hit:

1. Explicit path, URL, or Linear id argument.
2. Current cache `.ycc/artifacts/<slug>/`, then the main checkout cache. Find the main root as the parent of `git rev-parse --git-common-dir` and check the same relative artifact path there.
3. `ARTIFACT_LINEAR_HINT`: verify the issue in Linear, then open its Project, Documents, and any report status update.
4. Open PR for the current branch (`gh pr view --json body` or GitHub MCP). Parse `(Closes|Fixes|Resolves) <TEAM>-<n>`, verify the issue in Linear, then open its Project and artifacts. Match keywords and keys case-insensitively. Without a configured team, accept the generic key prefix `[A-Za-z][A-Za-z0-9]{1,9}`.
5. `ARTIFACT_LINEAR_PROJECT` when set; otherwise Projects in `ARTIFACT_LINEAR_TEAM` carrying `ARTIFACT_LINEAR_LABEL`, filtered by slug.
6. Linear Document search by slug and title (`<slug> · <kind>`), plus the Project's report status updates.
7. Ask the user for a path, URL, or id, listing everything tried and any step skipped for missing configuration.

Never conclude "no plan" without querying Linear and asking the user. If Linear is unreachable, say the lookup is unverified and ask; never report not-found. Local mode uses today's local discovery only and makes no Linear calls.

## Local mode

`store: local` preserves today's paths and filenames, adds no identity frontmatter, and makes no Linear calls. Save stops after step 3 and Lookup uses today's local discovery only.

- `ARTIFACT_ROOT=PLANS_ROOT`, `ARTIFACT_DIR=PLANS_DIR`, and `get_feature_artifact_dir <slug>` prints `${PLANS_DIR}/<slug>`.
- Existing `docs/prps/` files stay there rather than moving under `docs/plans/`. Repo-level kinds resolve under `ARTIFACT_REPO_ROOT` (the physical current checkout git toplevel, or physical start directory outside git), independent of `PLANS_ROOT`.
- `ensure_artifact_gitignore` is a no-op; `ensure_artifact_dir` only creates `ARTIFACT_DIR`. CLI `--ensure --kind=... --slug=...` instead creates only the resolved file's parent or the resolved directory, never an unrelated plans directory.
- The new slug helper is stricter than `get_feature_plan_dir`. Existing names it rejects keep their existing paths rather than being silently renamed.

## Size and diagrams

- Keep Mermaid in fenced code blocks; Linear renders Mermaid.
- Split a document over about 100 KB into `<basename>-part-N.md`. Each part is its own Linear Document with its own title and identity frontmatter. When content shrinks, delete stale local part files and their Linear Documents.
- Visual-plan writes `plan.mdx` (and any additional visual surfaces) under `.ycc/artifacts/<slug>/visual/` in linear mode. These files stay local-only and are not uploaded; visual-plan does not edit the source plan to reference them. Local mode keeps the existing `<plan-dir>/visual/` behavior.

Migration of existing `docs/plans` and `docs/prps` trees is YAN-1158. Leave them in place.
