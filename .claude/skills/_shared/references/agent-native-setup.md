# Agent-Native Runtime — Shared Setup Contract

## Contents

- Install (pinned)
- Reconnect / auth (hosted mode only)
- MCP connector (hosted or self-hosted mode)
- Local-only operation
- Localhost bridge
- Block vocabulary (resolve at USE TIME)
- Egress policy
- Hosted Plans app

Reference for ycc skills that drive the Agent-Native visual-planning runtime — currently
`.claude/skills/visual-plan/SKILL.md` and
`.claude/skills/visual-recap/SKILL.md`. Both skills cite this doc so the install
pin, the auth/reconnect flow, the connector names, the localhost bridge, and the egress
policy stay convergent. Fix them here, not per skill (DRY).

The pinned version below was resolved with `npm view @agent-native/core version` at authoring
time. Re-resolve and re-pin before a release if the runtime has advanced — never substitute a
floating `latest` dist-tag into committed content.

---

## Install (pinned)

Install a skill from the Agent-Native core CLI with an **explicit pinned version**:

```
npx -y @agent-native/core@0.59.1 skills add <skill>
```

- The pin (`0.59.1`) is the version resolved via `npm view @agent-native/core version`.
- Never commit a floating `latest` dist-tag — the block vocabulary and CLI surface drift
  between releases, so an unpinned install can silently change behavior. Pin, verify, then bump
  deliberately.

## Reconnect / auth (hosted mode only)

Authorize the runtime against the hosted Plans origin once per client. This is a one-time
step per machine/client, not per skill run. Local-files mode does not register an MCP server
and does not use this flow:

```
npx -y @agent-native/core@0.59.1 reconnect https://plan.agent-native.com --client [claude-code|codex|all]
```

- `--client all` reconnects every detected client at once.
- Re-run only when credentials are revoked or the client is reinstalled.

## MCP connector (hosted or self-hosted mode)

The runtime exposes an MCP connector named **`plan`**. The legacy alias **`agent-native-plans`**
still resolves to the same connector — accept either name when detecting an existing
connection, but prefer `plan` in new guidance. Do not register either connector for
local-files mode; the Agent-Native installer intentionally skips MCP registration in that
mode.

## Local-only operation

For local-only plan storage (no hosted writes and no auth required), prefix each
runtime command explicitly:

```
env AGENT_NATIVE_PLANS_MODE=local-files npx -y @agent-native/core@0.59.1 plan <subcommand>
```

In this mode plan content is read and written on the local filesystem only. The
schema-only block-catalog request and the browser UI shell used by the localhost bridge may
contact the configured Plan app, but neither writes plan content to its database. Use the
pinned CLI commands below instead of a startup MCP server.

## Localhost bridge

To preview a plan locally, use the pinned Agent-Native CLI against the plan directory:

```
env AGENT_NATIVE_PLANS_MODE=local-files npx -y @agent-native/core@0.59.1 plan local check --dir plans/<slug>
env AGENT_NATIVE_PLANS_MODE=local-files npx -y @agent-native/core@0.59.1 plan local serve --dir plans/<slug> --open
```

- `check` validates the plan files under `plans/<slug>` without serving.
- `serve` binds **127.0.0.1** on an **ephemeral, CLI-chosen port** and opens the preview with
  `--open`.
- The bridge performs **no hosted DB writes**. The browser may load the hosted Plan UI shell,
  but it reads plan MDX from 127.0.0.1 and does not upload that content to hosted storage.

## Block vocabulary (resolve at USE TIME)

> GOTCHA: The MDX block vocabulary drifts upstream between releases. Do **not** trust any
> hardcoded component/block list in skill bodies or in this doc.

At the runtime-author step (use time, not authoring time), fetch the live block catalog:

- Hosted/connected: call the **`get-plan-blocks`** MCP tool.
- `local-files`: run
  `env AGENT_NATIVE_PLANS_MODE=local-files npx -y @agent-native/core@0.59.1 plan blocks --out <path>`
  to dump the current catalog.

Always render plan MDX against the freshly-fetched block list, never a list memorized in a
skill.

## Egress policy

Hosted upload is **OFF by default** and **consent-gated**:

- No plan content leaves the machine unless the operator passes an explicit `--share` flag
  that names the destination host `plan.agent-native.com`.
- All hosted egress MUST be routed through
  `.claude/skills/_shared/scripts/visual-egress-guard.sh` so the consent check
  and destination allowlist are enforced in one place.
- Localhost bridge usage (above) is not egress: it stays on 127.0.0.1 and writes nothing
  remote.

## Hosted Plans app

- Hosted origin: `https://plan.agent-native.com`.
- Create tools return **absolute, shareable URLs** under that origin.
- Plans backed by a **private repo are org-gated** — only members of the owning org can open
  the shared URL.
