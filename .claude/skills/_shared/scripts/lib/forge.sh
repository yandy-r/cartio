#!/usr/bin/env bash
# forge.sh — Git forge provider detection and tooling selection.
#
# Sourceable library. Sourcing it has NO side effects (no network, no writes,
# no `set` changes that leak to the caller). Every consumer that talks to a
# remote git host (GitHub / Forgejo / Gitea / GitLab) routes through these
# helpers so a single detection law governs the whole bundle.
#
# Provider values returned by forge_detect_provider:
#   github   — GitHub.com or GitHub Enterprise        → CLI: gh
#   forgejo  — Forgejo instance (Gitea fork)          → CLI: tea
#   gitea    — Gitea instance                          → CLI: tea
#   gitlab   — GitLab.com or self-hosted GitLab        → CLI: glab
#   unknown  — could not be determined                 → CLI: (none)
#
# `forgejo` and `gitea` are the same tooling family (both driven by `tea`);
# they are distinguished only for accurate messaging. Treat them identically
# for command selection — forge_cli maps both to `tea`.
#
# Contract: see _shared/references/forge-detection.md (single source of truth).

# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

# forge_strip_git_suffix <url> — strip a trailing ".git".
forge_strip_git_suffix() {
  printf '%s' "${1%.git}"
}

# forge_remote_url [remote] — print the fetch URL for a remote (default origin).
# Prints nothing and returns 1 if the remote does not exist.
forge_remote_url() {
  local remote="${1:-origin}"
  git remote get-url "$remote" 2>/dev/null
}

# forge_host_from_url <url> — extract the bare hostname from an ssh, https, or
# git:// remote URL. Handles:
#   git@host:owner/repo.git
#   ssh://git@host:22/owner/repo.git
#   https://host/owner/repo.git
#   git://host/owner/repo.git
forge_host_from_url() {
  local url="$1"
  url="$(forge_strip_git_suffix "$url")"

  case "$url" in
    *://*)
      # scheme://[user@]host[:port]/path
      local rest="${url#*://}"
      rest="${rest#*@}"      # drop optional user@
      rest="${rest%%/*}"     # keep host[:port]
      printf '%s' "${rest%%:*}"  # drop :port
      ;;
    *@*:*)
      # scp-like: [user@]host:path
      local rest="${url#*@}"
      printf '%s' "${rest%%:*}"
      ;;
    *:*)
      # host:path with no user
      printf '%s' "${url%%:*}"
      ;;
    *)
      printf '%s' "$url"
      ;;
  esac
}

# ---------------------------------------------------------------------------
# Configured-host probes (local only — no network)
# ---------------------------------------------------------------------------

# forge_host_is_github <host> — true if the host is GitHub.com, a configured
# GH_HOST/GITHUB_HOST enterprise host, or listed in ~/.config/gh/hosts.yml.
forge_host_is_github() {
  local host="$1"
  case "$host" in
    github.com|*.github.com) return 0 ;;
  esac
  if [[ -n "${GH_HOST:-}" && "$host" == "${GH_HOST}" ]]; then return 0; fi
  if [[ -n "${GITHUB_HOST:-}" && "$host" == "${GITHUB_HOST}" ]]; then return 0; fi
  local hosts_yml="${HOME}/.config/gh/hosts.yml"
  if [[ -f "$hosts_yml" ]] && grep -qE "^${host}:" "$hosts_yml" 2>/dev/null; then
    return 0
  fi
  return 1
}

# forge_host_is_gitlab <host> — true if GitLab.com or a configured glab host.
forge_host_is_gitlab() {
  local host="$1"
  case "$host" in
    gitlab.com|*.gitlab.com) return 0 ;;
  esac
  local glab_cfg="${HOME}/.config/glab-cli/config.yml"
  if [[ -f "$glab_cfg" ]] && grep -qE "^[[:space:]]*${host}:" "$glab_cfg" 2>/dev/null; then
    return 0
  fi
  return 1
}

# forge_host_in_tea <host> — true if the host matches a configured `tea` login
# (Gitea/Forgejo). Reads `tea login list` so no network call is made.
forge_host_in_tea() {
  local host="$1" logins
  [[ -n "$host" ]] || return 1
  command -v tea >/dev/null 2>&1 || return 1
  # `tea login list` prints a table containing the URL and SSH host columns.
  # Capture it and match with a here-string, not `tea login list | grep -q`:
  # under pipefail the early-exiting grep SIGPIPEs tea and a configured login
  # reads as absent (YAN-1607).
  logins="$(tea login list 2>/dev/null || true)"
  grep -qiF -- "$host" <<<"$logins"
}

# ---------------------------------------------------------------------------
# Optional network probe — distinguishes Forgejo from Gitea, and confirms a
# gitea-family host when no local config exists. Best-effort; never required.
# ---------------------------------------------------------------------------

# forge_probe_gitea_family <host> — echo "forgejo" or "gitea" if the host serves
# the Gitea REST API, else echo nothing and return 1. Hits /api/v1/version,
# which is public on virtually all instances.
forge_probe_gitea_family() {
  local host="$1"
  command -v curl >/dev/null 2>&1 || return 1
  local body
  body="$(curl -fsSL --max-time 5 "https://${host}/api/v1/version" 2>/dev/null)" || return 1
  # The version endpoint returns {"version":"..."}. Forgejo advertises itself in
  # the Server header; fall back to assuming gitea for the bare version payload.
  case "$body" in
    *version*)
      local server
      server="$(curl -fsSLI --max-time 5 "https://${host}/api/v1/version" 2>/dev/null | tr -d '\r')"
      if grep -qi 'forgejo' <<<"$server"; then
        printf 'forgejo'
      else
        printf 'gitea'
      fi
      return 0
      ;;
  esac
  return 1
}

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

# forge_detect_provider [remote] [--probe]
#   Detect the forge provider for a remote (default origin).
#   Pass --probe to allow the network /api/v1/version fallback (off by default
#   so detection stays fast and offline-friendly).
#   Echoes one of: github | forgejo | gitea | gitlab | unknown
forge_detect_provider() {
  local remote="origin" probe="false"
  local arg
  for arg in "$@"; do
    case "$arg" in
      --probe) probe="true" ;;
      *) remote="$arg" ;;
    esac
  done

  local url host
  url="$(forge_remote_url "$remote")" || { printf 'unknown'; return 0; }
  [[ -z "$url" ]] && { printf 'unknown'; return 0; }
  host="$(forge_host_from_url "$url")"
  [[ -z "$host" ]] && { printf 'unknown'; return 0; }

  if forge_host_is_github "$host"; then printf 'github'; return 0; fi
  if forge_host_is_gitlab "$host"; then printf 'gitlab'; return 0; fi
  if forge_host_in_tea "$host"; then
    if [[ "$probe" == "true" ]]; then
      local fam
      fam="$(forge_probe_gitea_family "$host")" && { printf '%s' "$fam"; return 0; }
    fi
    # tea is configured for this host but we can't tell Forgejo from Gitea
    # offline — default to the generic family label.
    printf 'gitea'
    return 0
  fi
  if [[ "$probe" == "true" ]]; then
    local fam
    fam="$(forge_probe_gitea_family "$host")" && { printf '%s' "$fam"; return 0; }
  fi
  printf 'unknown'
}

# ---------------------------------------------------------------------------
# Tooling selection
# ---------------------------------------------------------------------------

# forge_cli <provider> — echo the CLI binary name for a provider.
#   github → gh, forgejo|gitea → tea, gitlab → glab, unknown → (empty)
forge_cli() {
  case "$1" in
    github) printf 'gh' ;;
    forgejo|gitea) printf 'tea' ;;
    gitlab) printf 'glab' ;;
    *) printf '' ;;
  esac
}

# forge_is_gitea_family <provider> — true for forgejo or gitea.
forge_is_gitea_family() {
  case "$1" in
    forgejo|gitea) return 0 ;;
    *) return 1 ;;
  esac
}

# forge_cli_available <provider> — true if the provider's CLI is installed.
forge_cli_available() {
  local cli
  cli="$(forge_cli "$1")"
  [[ -n "$cli" ]] && command -v "$cli" >/dev/null 2>&1
}

# forge_auth_ok <provider> — true if the provider's CLI is authenticated.
# Uses only local auth-status checks (no API/rate-limit cost).
forge_auth_ok() {
  case "$1" in
    github) command -v gh   >/dev/null 2>&1 && gh   auth status >/dev/null 2>&1 ;;
    forgejo|gitea)
      # `tea` has no `auth status`; a configured login list is the local signal.
      # Captured, not piped into grep -q (SIGPIPE under pipefail; YAN-1607).
      command -v tea  >/dev/null 2>&1 || return 1
      local logins
      logins="$(tea login list 2>/dev/null || true)"
      grep -q '://' <<<"$logins"
      ;;
    gitlab) command -v glab >/dev/null 2>&1 && glab auth status >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

# forge_default_branch <provider> — echo the repository default branch, or
# fall back to "main". Provider-neutral.
forge_default_branch() {
  local provider="$1" b=""
  case "$provider" in
    github)
      b="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null)"
      ;;
    forgejo|gitea)
      # `tea repos search`/`tea` lacks a stable default-branch field across
      # versions; infer from the remote HEAD instead.
      b="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')"
      ;;
    gitlab)
      b="$(glab repo view --output json 2>/dev/null | grep -o '"default_branch":"[^"]*"' | head -n1 | sed 's/.*:"//;s/"//')"
      ;;
  esac
  if [[ -z "$b" ]]; then
    b="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')"
  fi
  printf '%s' "${b:-main}"
}

# forge_unsupported_notice <provider> <feature> — print a uniform, honest
# message to stderr when an advanced feature is GitHub-only. Returns 0 so the
# caller controls the exit code.
forge_unsupported_notice() {
  local provider="$1" feature="$2"
  printf '%s: %s is a GitHub-only feature; provider "%s" is not supported.\n' \
    "${0##*/}" "$feature" "$provider" >&2
  printf '  Reason: Forgejo/Gitea expose only the REST v1 API (no GraphQL) and\n' >&2
  printf '  the tea CLI does not surface CI-run logs/rerun controls. Run this\n' >&2
  printf '  feature on a GitHub remote, or do the action manually on %s.\n' "$provider" >&2
}
