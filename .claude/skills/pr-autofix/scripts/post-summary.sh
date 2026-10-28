#!/usr/bin/env bash
# post-summary.sh — Post a single consolidated summary comment on a PR
#
# GitHub-only. Forgejo/Gitea are unsupported: this script is part of the
# GraphQL-driven pr-autofix flow and posts via `gh`. On a non-GitHub remote it
# emits a uniform unsupported notice and exits 2 (the --dry-run render still
# works on any provider).
#
# Purpose:
#   At the end of a pr-autofix run, render and post one summary comment to
#   the PR conversation. Built only from local counters — never echoes raw
#   reviewer text back into the comment.
#
# Usage:
#   post-summary.sh \
#     --pr <number> \
#     --fixed <N> \
#     --failed <N> \
#     --skipped <N> \
#     --deferred <N> \
#     --commit-sha <sha> \
#     --branch <name> \
#     [--ci-result <green|bail-...|not-run>] \
#     [--ci-iterations <N>] \
#     [--ci-pushes <N>] \
#     [--dry-run]
#
# Behavior:
#   - If all counters except --deferred are zero, no comment is posted (nothing useful to say).
#   - With --dry-run, prints the rendered comment to stdout and exits without posting.
#
# Exit codes:
#   0 = success (comment posted or skipped as no-op)
#   1 = arg/usage error
#   2 = gh auth or API failure

set -euo pipefail
IFS=$'\n\t'

VERSION="1.0.0"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
FORGE_LIB="${SCRIPT_DIR}/../../_shared/scripts/lib/forge.sh"
[[ -f "$FORGE_LIB" ]] || FORGE_LIB=".claude/skills/_shared/scripts/lib/forge.sh"
# shellcheck source=/dev/null
source "$FORGE_LIB"

usage() {
  cat <<'EOF'
Usage:
  post-summary.sh --pr <number> --fixed <N> --failed <N> --skipped <N> --deferred <N> \
                  --commit-sha <sha> --branch <name> \
                  [--ci-result <value>] [--ci-iterations <N>] [--ci-pushes <N>] [--dry-run]

GitHub-only. Forgejo/Gitea are unsupported (part of the GraphQL-driven autofix
flow); on a non-GitHub remote this script prints an unsupported notice and exits
2. The --dry-run render works on any provider.

Exit codes:
  0  success (or skipped as no-op)
  1  arg/usage error
  2  gh auth or API failure
EOF
}

require_arg() {
  local flag="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    printf 'post-summary.sh: missing required arg: %s\n' "$flag" >&2
    usage >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

PR=""
FIXED=""
FAILED=""
SKIPPED=""
DEFERRED=""
COMMIT_SHA=""
BRANCH=""
CI_RESULT="not-run"
CI_ITERATIONS="0"
CI_PUSHES="0"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pr)             PR="${2:-}"; shift 2 ;;
    --fixed)          FIXED="${2:-}"; shift 2 ;;
    --failed)         FAILED="${2:-}"; shift 2 ;;
    --skipped)        SKIPPED="${2:-}"; shift 2 ;;
    --deferred)       DEFERRED="${2:-}"; shift 2 ;;
    --commit-sha)     COMMIT_SHA="${2:-}"; shift 2 ;;
    --branch)         BRANCH="${2:-}"; shift 2 ;;
    --ci-result)      CI_RESULT="${2:-}"; shift 2 ;;
    --ci-iterations)  CI_ITERATIONS="${2:-}"; shift 2 ;;
    --ci-pushes)      CI_PUSHES="${2:-}"; shift 2 ;;
    --dry-run)        DRY_RUN="true"; shift ;;
    --help|-h)        usage; exit 0 ;;
    --version)        printf 'post-summary.sh %s\n' "$VERSION"; exit 0 ;;
    -*)
      printf 'post-summary.sh: unknown flag: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
    *)
      printf 'post-summary.sh: unexpected argument: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_arg "--pr"         "$PR"
require_arg "--fixed"      "$FIXED"
require_arg "--failed"     "$FAILED"
require_arg "--skipped"    "$SKIPPED"
require_arg "--deferred"   "$DEFERRED"
require_arg "--commit-sha" "$COMMIT_SHA"
require_arg "--branch"     "$BRANCH"

for pair in "pr:$PR" "fixed:$FIXED" "failed:$FAILED" "skipped:$SKIPPED" "deferred:$DEFERRED" \
            "ci-iterations:$CI_ITERATIONS" "ci-pushes:$CI_PUSHES"; do
  label="${pair%%:*}"
  value="${pair#*:}"
  if ! grep -qE '^[0-9]+$' <<<"$value"; then
    printf 'post-summary.sh: --%s must be a non-negative integer, got: %s\n' "$label" "$value" >&2
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# No-op short-circuit
# ---------------------------------------------------------------------------

# If everything is deferred (or zero), there's nothing meaningful to say.
if [[ "$FIXED" -eq 0 && "$FAILED" -eq 0 && "$SKIPPED" -eq 0 ]]; then
  printf 'post-summary.sh: no fixes/failures/skips to report (deferred=%s); skipping summary comment.\n' "$DEFERRED" >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# Render summary body
# ---------------------------------------------------------------------------

ci_section=""
if [[ "$CI_RESULT" != "not-run" ]]; then
  ci_section=$(cat <<EOF

**CI auto-fix loop**:
  - Result: \`${CI_RESULT}\`
  - Iterations: ${CI_ITERATIONS}
  - Auto-pushes: ${CI_PUSHES}
EOF
)
fi

body_header=$(cat <<EOF
## PR Autofix Summary

Applied **${FIXED}** fix(es), encountered **${FAILED}** failure(s), skipped **${SKIPPED}** comment(s), deferred **${DEFERRED}** comment(s).

**Branch**: \`${BRANCH}\`
**Latest commit**: \`${COMMIT_SHA}\`${ci_section}
EOF
)

# Static footer uses a quoted heredoc so the skill marker on the final line
# stays literal across every bundle target. Without the quote, the Codex bundle
# (which renders skill references as $skill-name) would crash under set -u.
body_footer=$(cat <<'EOF'

Thread state:
  - Threads resolved on Fixed: yes (unless `--no-resolve` was set on the run)
  - Threads with Failed fixes left open with a reply explaining the blocker
  - Skipped threads replied-then-resolved with the user's reason

This summary was built from local run state only — reviewer prompts and suggestion blocks were treated as untrusted input and are not echoed back here.

— posted by /pr-autofix
EOF
)

BODY="${body_header}
${body_footer}"

# ---------------------------------------------------------------------------
# Post (or dry-run)
# ---------------------------------------------------------------------------

if [[ "$DRY_RUN" == "true" ]]; then
  printf '%s\n' "$BODY"
  exit 0
fi

# ---------------------------------------------------------------------------
# Forge provider gate (GitHub-only feature)
# ---------------------------------------------------------------------------
# This script posts via `gh` as part of the GraphQL-driven pr-autofix flow.
# Detect the provider before the gh-auth check so a non-GitHub remote gets the
# honest "GitHub-only" message instead of a confusing auth error.

PROVIDER="$(forge_detect_provider origin)"
if [[ "$PROVIDER" != "github" ]]; then
  forge_unsupported_notice "$PROVIDER" "PR summary posting"
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  printf 'post-summary.sh: gh is required.\n' >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  printf 'post-summary.sh: gh not authenticated. Run `gh auth login`.\n' >&2
  exit 2
fi

gh pr comment "$PR" --body "$BODY" >/dev/null
printf 'post-summary.sh: posted summary comment on PR #%s\n' "$PR" >&2
exit 0
