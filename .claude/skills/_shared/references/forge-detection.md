# Forge Provider Detection & Tooling Selection

## Contents

- Provider values
- Detection algorithm
- Auth probes (run once, cache the result)
- Operation equivalence map
- Capability matrix — GitHub-only features
- Skill integration pattern (Phase 0)

Single source of truth for how every git skill in the `ycc` bundle decides
**which forge it is talking to** and **which CLI to drive**. Any skill that
creates PRs, files issues, cuts releases, posts review comments, or watches CI
must run this detection first and route accordingly.

The programmatic counterpart is
[`_shared/scripts/lib/forge.sh`](../scripts/lib/forge.sh) — a sourceable bash
library that implements the detection law described here. Scripts source it;
SKILL prose references this document. The two must never diverge.

> **Why this exists.** The bundle was historically GitHub-only (`gh`
> everywhere). Self-hosted **Forgejo** and **Gitea** repositories need the same
> workflows driven through the `tea` CLI. Detection lets one skill serve both
> without the user passing a `--provider` flag.

---

## Provider values

| Provider  | Hosts                          | CLI    | Notes                                  |
| --------- | ------------------------------ | ------ | -------------------------------------- |
| `github`  | github.com, GitHub Enterprise  | `gh`   | Full feature support                   |
| `forgejo` | self-hosted Forgejo            | `tea`  | Gitea fork; same tooling as `gitea`    |
| `gitea`   | self-hosted Gitea              | `tea`  | Generic Gitea-family label             |
| `gitlab`  | gitlab.com, self-hosted GitLab | `glab` | Pre-existing partial support           |
| `unknown` | anything else                  | (none) | Skip host-API steps with a loud notice |

`forgejo` and `gitea` are the **same tooling family** — both driven by `tea`.
They are distinguished only for accurate user-facing messages. For command
selection, treat them identically (`forge_cli` maps both to `tea`).

---

## Detection algorithm

`forge_detect_provider [remote] [--probe]` (default remote: `origin`):

1. Read `git remote get-url <remote>`. No remote → `unknown`.
2. Extract the bare host from the URL (handles `git@host:owner/repo.git`,
   `ssh://git@host:port/...`, `https://host/...`, `git://host/...`; strips a
   trailing `.git`).
3. **GitHub** if the host is `github.com` / `*.github.com`, equals `$GH_HOST`
   or `$GITHUB_HOST`, or appears in `~/.config/gh/hosts.yml`.
4. **GitLab** if the host is `gitlab.com` / `*.gitlab.com`, or appears in
   `~/.config/glab-cli/config.yml`.
5. **Gitea family** if the host matches a configured `tea login list` entry.
   With `--probe`, a best-effort `GET https://<host>/api/v1/version` (plus the
   Server header) distinguishes `forgejo` from `gitea`; offline, default to
   `gitea`.
6. With `--probe` and no local match, the `/api/v1/version` probe can still
   classify an unconfigured Gitea/Forgejo host.
7. Otherwise → `unknown`.

Steps 3–4 and the `tea login list` read in step 5 are **local only** — no
network, no rate-limit cost. The `/api/v1/version` probe in steps 5–6 is
opt-in via `--probe` so default detection stays fast and offline-friendly.

---

## Auth probes (run once, cache the result)

Never call the host API to check auth — use the local status commands:

```bash
# github
command -v gh   >/dev/null && gh   auth status >/dev/null 2>&1   # → authed

# forgejo / gitea  (tea has no `auth status`; a configured login is the signal).
# Capture, then match: `tea login list | grep -q` SIGPIPEs tea under pipefail.
command -v tea  >/dev/null && ( logins="$(tea login list 2>/dev/null || true)"; grep -q '://' <<<"$logins" )

# gitlab
command -v glab >/dev/null && glab auth status >/dev/null 2>&1
```

The lib exposes these as `forge_auth_ok <provider>`. If the matched provider's
CLI is missing or unauthenticated, surface a precise remediation (`gh auth
login`, `tea login add`, `glab auth login`) and fall back to local-only mode
rather than guessing.

---

## Operation equivalence map

Core operations are supported on **both** families. Drive them through the CLI
that `forge_cli <provider>` returns.

| Operation             | GitHub (`gh`)                            | Forgejo / Gitea (`tea`)                          |
| --------------------- | ---------------------------------------- | ------------------------------------------------ |
| Default branch        | `gh repo view --json defaultBranchRef`   | `git symbolic-ref refs/remotes/origin/HEAD`      |
| Create PR             | `gh pr create --title --body [--draft]`  | `tea pull create --title --description [--head]` |
| PR for current branch | `gh pr list --head <branch>`             | `tea pull list` (filter by head branch)          |
| View PR state         | `gh pr view <n> --json state`            | `tea pull <n>`                                   |
| List open issues      | `gh issue list`                          | `tea issues list`                                |
| Create issue          | `gh issue create --title --body`         | `tea issues create --title --description`        |
| Close issue           | `gh issue close <n>`                     | `tea issues close <n>`                           |
| Create release        | `gh release create <tag> [--notes-file]` | `tea release create --tag <tag> [--note-file]`   |
| Delete remote branch  | `git push <remote> --delete <branch>`    | `git push <remote> --delete <branch>` (same)     |

`tea` operates on the repo bound to the current directory's `origin` remote (it
reads `.git/config`); pass `--repo owner/name` only when overriding.

---

## Capability matrix — GitHub-only features

Some advanced features depend on APIs that **do not exist** on Forgejo/Gitea.
These degrade gracefully: detect the provider, and on a non-GitHub remote emit
`forge_unsupported_notice` and exit cleanly rather than faking parity.

| Feature                              | GitHub | Forgejo / Gitea | Why                                                         |
| ------------------------------------ | ------ | --------------- | ----------------------------------------------------------- |
| PR / issue / release create + list   | ✅     | ✅              | Covered by `gh` and `tea`                                   |
| Review-thread resolution (`resolve`) | ✅     | ❌              | Requires GraphQL `resolveReviewThread`; no GraphQL on Gitea |
| Per-comment PR autofix harvesting    | ✅     | ❌              | `fetch-pr-comments.sh` uses the GitHub GraphQL API          |
| CI-autofix loop (`--ci`)             | ✅     | ❌              | Depends on `gh run view --log-failed` / `gh run rerun`      |
| Emoji reactions on comments          | ✅     | ❌              | GraphQL `addReaction`                                       |

For ❌ features on a non-GitHub remote, the skill must:

1. Detect the provider in its Phase 0.
2. Tell the user the feature is GitHub-only and **why** (one line).
3. Offer the manual alternative (e.g. "resolve the thread in the Forgejo UI").
4. Continue with whatever **is** supported (e.g. still create the PR via `tea`),
   skipping only the unsupported step.

Never silently no-op and never pretend an unsupported step succeeded.

The CI-autofix loop policy (caps, classification, termination, audit log) lives in
`ci-monitoring.md` in this directory; read it before entering the loop. The older
GitHub/GitLab host-operation notes this document generalizes live in
`ycc/skills/git-cleanup/references/host-detection.md`, owned by `git-cleanup`.

---

## Skill integration pattern (Phase 0)

Every git-operation skill adds a detection step before any host call:

```bash
# shellcheck source=/dev/null
source ".claude/skills/_shared/scripts/lib/forge.sh"
provider="$(forge_detect_provider origin)"
cli="$(forge_cli "$provider")"
forge_auth_ok "$provider" || { echo "Auth needed for $provider"; exit 2; }
```

Then branch on `$provider` for the operation, or call a script that does the
branching internally. Log the resolved provider + CLI so the user sees which
forge the workflow targeted.

---
