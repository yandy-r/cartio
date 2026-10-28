#!/usr/bin/env bash
# resolve-thread.sh — Mutate PR review threads and conversation comments
#
# GitHub-only. Forgejo/Gitea are unsupported: review-thread resolution and emoji
# reactions require the GitHub GraphQL API (resolveReviewThread, addReaction),
# which has no Forgejo/Gitea equivalent. On a non-GitHub remote this script emits
# a uniform unsupported notice and exits 2.
#
# Purpose:
#   Wraps the four mutations pr-autofix needs to close out comments:
#     resolve      — resolveReviewThread(threadId) via GraphQL
#     reply        — POST /pulls/{N}/comments/{comment_id}/replies via REST
#     issue-reply  — POST /issues/{N}/comments via REST (top-level conversation)
#     react        — POST reactions to either a pull-comment or issue-comment
#
# Subcommands:
#
#   resolve-thread.sh resolve <thread_node_id>
#       Resolves the given GraphQL review-thread node.
#
#   resolve-thread.sh reply <pr_number> <root_comment_id> <body>
#       Posts a reply under the thread root comment.
#       <root_comment_id> is the REST databaseId of the thread's root review comment.
#
#   resolve-thread.sh issue-reply <pr_number> <body>
#       Posts a new top-level PR conversation comment.
#
#   resolve-thread.sh react <comment_node_id> <reaction>
#       Adds an emoji reaction to a comment (review comment or issue comment).
#       <reaction> values: THUMBS_UP, THUMBS_DOWN, LAUGH, HOORAY, CONFUSED, HEART,
#                         ROCKET, EYES.
#
# Exit codes:
#   0 = success
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
  resolve-thread.sh resolve     <thread_node_id>
  resolve-thread.sh reply       <pr_number> <root_comment_id> <body>
  resolve-thread.sh issue-reply <pr_number> <body>
  resolve-thread.sh react       <comment_node_id> <reaction>

  resolve-thread.sh --help
  resolve-thread.sh --version

GitHub-only. Forgejo/Gitea are unsupported (no GraphQL API for review-thread
resolution or reactions); on a non-GitHub remote this script prints an
unsupported notice and exits 2.

Reaction values:
  THUMBS_UP THUMBS_DOWN LAUGH HOORAY CONFUSED HEART ROCKET EYES

Exit codes:
  0  success
  1  arg/usage error
  2  gh auth or API failure
EOF
}

require_dep() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'resolve-thread.sh: required dependency missing: %s\n' "$1" >&2
    exit 1
  fi
}

require_arg() {
  local label="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    printf 'resolve-thread.sh: missing required arg: %s\n' "$label" >&2
    usage >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

cmd_resolve() {
  local thread_id="${1:-}"
  require_arg "thread_node_id" "$thread_id"

  gh api graphql -F thread="$thread_id" -f query='
    mutation($thread:ID!) {
      resolveReviewThread(input:{threadId:$thread}) {
        thread { id isResolved }
      }
    }' >/dev/null
  printf 'resolve-thread.sh: resolved thread %s\n' "$thread_id" >&2
}

cmd_reply() {
  local pr="${1:-}"
  local root_id="${2:-}"
  local body="${3:-}"

  require_arg "pr_number"       "$pr"
  require_arg "root_comment_id" "$root_id"
  require_arg "body"            "$body"

  if ! grep -qE '^[0-9]+$' <<<"$pr"; then
    printf 'resolve-thread.sh: pr_number must be a positive integer, got: %s\n' "$pr" >&2
    exit 1
  fi
  if ! grep -qE '^[0-9]+$' <<<"$root_id"; then
    printf 'resolve-thread.sh: root_comment_id must be a positive integer, got: %s\n' "$root_id" >&2
    exit 1
  fi

  local owner repo
  owner=$(gh repo view --json owner --jq '.owner.login')
  repo=$(gh repo view --json name --jq '.name')

  gh api -X POST \
    "/repos/${owner}/${repo}/pulls/${pr}/comments/${root_id}/replies" \
    -f body="$body" >/dev/null
  printf 'resolve-thread.sh: posted reply to comment %s on PR #%s\n' "$root_id" "$pr" >&2
}

cmd_issue_reply() {
  local pr="${1:-}"
  local body="${2:-}"
  require_arg "pr_number" "$pr"
  require_arg "body"      "$body"

  if ! grep -qE '^[0-9]+$' <<<"$pr"; then
    printf 'resolve-thread.sh: pr_number must be a positive integer, got: %s\n' "$pr" >&2
    exit 1
  fi

  local owner repo
  owner=$(gh repo view --json owner --jq '.owner.login')
  repo=$(gh repo view --json name --jq '.name')

  gh api -X POST \
    "/repos/${owner}/${repo}/issues/${pr}/comments" \
    -f body="$body" >/dev/null
  printf 'resolve-thread.sh: posted top-level comment on PR #%s\n' "$pr" >&2
}

cmd_react() {
  local node_id="${1:-}"
  local reaction="${2:-}"

  require_arg "comment_node_id" "$node_id"
  require_arg "reaction"        "$reaction"

  case "$reaction" in
    THUMBS_UP|THUMBS_DOWN|LAUGH|HOORAY|CONFUSED|HEART|ROCKET|EYES)
      ;;
    *)
      printf 'resolve-thread.sh: unknown reaction %s\n' "$reaction" >&2
      usage >&2
      exit 1
      ;;
  esac

  gh api graphql -F subject="$node_id" -F content="$reaction" -f query='
    mutation($subject:ID!, $content:ReactionContent!) {
      addReaction(input:{subjectId:$subject, content:$content}) {
        reaction { content }
      }
    }' >/dev/null
  printf 'resolve-thread.sh: added %s reaction to comment %s\n' "$reaction" "$node_id" >&2
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  require_dep gh

  if [[ $# -eq 0 ]]; then
    usage >&2
    exit 1
  fi

  # Handle help/version before any forge or auth checks so they work anywhere.
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --version)
      printf 'resolve-thread.sh %s\n' "$VERSION"
      exit 0
      ;;
  esac

  # -------------------------------------------------------------------------
  # Forge provider gate (GitHub-only feature)
  # -------------------------------------------------------------------------
  # resolveReviewThread/addReaction are GraphQL-only. Forgejo/Gitea have no
  # GraphQL API, so detect the provider and short-circuit before the gh-auth
  # check so a non-GitHub remote gets the honest "GitHub-only" message.

  local provider
  provider="$(forge_detect_provider origin)"
  if [[ "$provider" != "github" ]]; then
    forge_unsupported_notice "$provider" "PR review-thread resolution"
    exit 2
  fi

  if ! gh auth status >/dev/null 2>&1; then
    printf 'resolve-thread.sh: gh not authenticated. Run `gh auth login`.\n' >&2
    exit 2
  fi

  case "$1" in
    resolve)
      shift
      cmd_resolve "$@"
      ;;
    reply)
      shift
      cmd_reply "$@"
      ;;
    issue-reply)
      shift
      cmd_issue_reply "$@"
      ;;
    react)
      shift
      cmd_react "$@"
      ;;
    *)
      printf 'resolve-thread.sh: unknown subcommand: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
