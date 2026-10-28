#!/usr/bin/env bash
# fetch-pr-comments.sh — Fetch all actionable PR comments via GitHub GraphQL
#
# GitHub-only. Forgejo/Gitea are unsupported: they expose no GraphQL API, so
# review-thread harvesting cannot be replicated. On a non-GitHub remote this
# script emits a uniform unsupported notice and exits 2.
#
# Purpose:
#   Single-source fetcher used by pr-autofix. Pulls every actionable
#   comment surface on a PR — review threads (file/line anchored) and
#   top-level issue conversation comments — and emits one JSONL record per
#   comment-of-interest.
#
# Output schema (one JSON object per line):
#   {
#     "source": "review_thread" | "issue_comment",
#     "thread_id": "PRRT_..."  | null,        # GraphQL Node ID (for resolveReviewThread mutation)
#     "is_resolved": bool,                    # always false for issue_comment
#     "is_outdated": bool,                    # always false for issue_comment
#     "comment_id": <int>,                    # REST databaseId
#     "comment_node_id": "PRRC_..." | "...",  # GraphQL Node ID (for reactions, replies)
#     "author_login": "string",
#     "is_bot": bool,
#     "path": "string" | null,
#     "line": <int> | null,
#     "start_line": <int> | null,
#     "original_line": <int> | null,
#     "body": "string",
#     "created_at": "ISO 8601"
#   }
#
# Exit codes:
#   0 = success (records written to --out file; may be empty if no comments)
#   1 = arg/usage error
#   2 = gh auth failure or PR not found
#   3 = GraphQL error

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
  fetch-pr-comments.sh --pr <number> --out <file> [--owner <owner>] [--repo <repo>]
  fetch-pr-comments.sh --help
  fetch-pr-comments.sh --version

GitHub-only. Forgejo/Gitea are unsupported (no GraphQL API); on a non-GitHub
remote this script prints an unsupported notice and exits 2.

Options:
  --pr <number>     PR number to fetch comments for (required)
  --out <file>      JSONL output path (required)
  --owner <owner>   Repo owner (default: resolved via `gh repo view`)
  --repo <repo>     Repo name  (default: resolved via `gh repo view`)

Exit codes:
  0  success
  1  arg/usage error
  2  gh auth failure or PR not found
  3  GraphQL error
EOF
}

require_arg() {
  local flag="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    printf 'fetch-pr-comments.sh: missing required arg: %s\n' "$flag" >&2
    usage >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

PR=""
OUT=""
OWNER=""
REPO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pr)
      PR="${2:-}"
      shift 2
      ;;
    --out)
      OUT="${2:-}"
      shift 2
      ;;
    --owner)
      OWNER="${2:-}"
      shift 2
      ;;
    --repo)
      REPO="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --version)
      printf 'fetch-pr-comments.sh %s\n' "$VERSION"
      exit 0
      ;;
    -*)
      printf 'fetch-pr-comments.sh: unknown flag: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
    *)
      printf 'fetch-pr-comments.sh: unexpected argument: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_arg "--pr"  "$PR"
require_arg "--out" "$OUT"

if ! grep -qE '^[0-9]+$' <<<"$PR"; then
  printf 'fetch-pr-comments.sh: --pr must be a positive integer, got: %s\n' "$PR" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

for dep in gh jq; do
  if ! command -v "$dep" >/dev/null 2>&1; then
    printf 'fetch-pr-comments.sh: required dependency missing: %s\n' "$dep" >&2
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# Forge provider gate (GitHub-only feature)
# ---------------------------------------------------------------------------
# Review-thread harvesting uses the GitHub GraphQL API. Forgejo/Gitea have no
# GraphQL API, so detect the provider before any gh call and degrade gracefully.

PROVIDER="$(forge_detect_provider origin)"
if [[ "$PROVIDER" != "github" ]]; then
  forge_unsupported_notice "$PROVIDER" "PR review-comment harvesting"
  exit 2
fi

if ! gh auth status >/dev/null 2>&1; then
  printf 'fetch-pr-comments.sh: gh not authenticated. Run `gh auth login`.\n' >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Resolve owner/repo if not provided
# ---------------------------------------------------------------------------

if [[ -z "$OWNER" ]]; then
  OWNER=$(gh repo view --json owner --jq '.owner.login' 2>/dev/null || true)
fi
if [[ -z "$REPO" ]]; then
  REPO=$(gh repo view --json name --jq '.name' 2>/dev/null || true)
fi

if [[ -z "$OWNER" || -z "$REPO" ]]; then
  printf 'fetch-pr-comments.sh: could not resolve owner/repo (not in a GitHub repo, or `gh repo view` failed).\n' >&2
  exit 2
fi

# Confirm PR exists
if ! gh pr view "$PR" --json number >/dev/null 2>&1; then
  printf 'fetch-pr-comments.sh: PR #%s not found in %s/%s\n' "$PR" "$OWNER" "$REPO" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Prepare output (truncate)
# ---------------------------------------------------------------------------

: > "$OUT"

# ---------------------------------------------------------------------------
# Helper — detect "bot" accounts.
# Returns "true" if the login matches a bot pattern, else "false".
# ---------------------------------------------------------------------------

is_bot_login() {
  local login="$1"
  if [[ "$login" == *"[bot]" ]]; then
    printf 'true'
    return
  fi
  # Known bot logins lacking the [bot] suffix
  case "$login" in
    coderabbitai|sonarcloud|codacy-production|deepsource-autofix|sonarqubecloud)
      printf 'true'
      ;;
    *)
      printf 'false'
      ;;
  esac
}

# ---------------------------------------------------------------------------
# Section 1 — review threads (with cursor pagination)
# ---------------------------------------------------------------------------

cursor=""
while :; do
  # Build args array
  args=(-F owner="$OWNER" -F repo="$REPO" -F pr="$PR")
  if [[ -n "$cursor" ]]; then
    args+=(-F cursor="$cursor")
  fi

  response=$(gh api graphql "${args[@]}" -f query='
    query($owner:String!, $repo:String!, $pr:Int!, $cursor:String) {
      repository(owner:$owner, name:$repo) {
        pullRequest(number:$pr) {
          reviewThreads(first:50, after:$cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              isResolved
              isOutdated
              comments(first:1) {
                nodes {
                  databaseId
                  id
                  body
                  path
                  line
                  startLine
                  originalLine
                  createdAt
                  author { login }
                }
              }
            }
          }
        }
      }
    }') || {
    printf 'fetch-pr-comments.sh: GraphQL error fetching review threads.\n' >&2
    exit 3
  }

  # Emit one record per thread root comment
  printf '%s\n' "$response" | jq -c --arg src "review_thread" '
    .data.repository.pullRequest.reviewThreads.nodes[]
    | . as $thread
    | $thread.comments.nodes[0]
    | select(. != null)
    | {
        source: $src,
        thread_id: $thread.id,
        is_resolved: $thread.isResolved,
        is_outdated: $thread.isOutdated,
        comment_id: (.databaseId // 0),
        comment_node_id: .id,
        author_login: (.author.login // ""),
        path: .path,
        line: .line,
        start_line: .startLine,
        original_line: .originalLine,
        body: (.body // ""),
        created_at: (.createdAt // "")
      }
  ' | while IFS= read -r record; do
    login=$(printf '%s' "$record" | jq -r '.author_login')
    is_bot=$(is_bot_login "$login")
    printf '%s' "$record" | jq -c --argjson is_bot "$is_bot" '. + {is_bot: $is_bot}' >> "$OUT"
  done

  has_next=$(printf '%s' "$response" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage')
  cursor=$(printf '%s' "$response" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor // empty')
  if [[ "$has_next" != "true" ]]; then
    break
  fi
done

# ---------------------------------------------------------------------------
# Section 2 — top-level issue comments (with cursor pagination)
# ---------------------------------------------------------------------------

cursor=""
while :; do
  args=(-F owner="$OWNER" -F repo="$REPO" -F pr="$PR")
  if [[ -n "$cursor" ]]; then
    args+=(-F cursor="$cursor")
  fi

  response=$(gh api graphql "${args[@]}" -f query='
    query($owner:String!, $repo:String!, $pr:Int!, $cursor:String) {
      repository(owner:$owner, name:$repo) {
        pullRequest(number:$pr) {
          comments(first:50, after:$cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              databaseId
              id
              body
              createdAt
              author { login }
            }
          }
        }
      }
    }') || {
    printf 'fetch-pr-comments.sh: GraphQL error fetching issue comments.\n' >&2
    exit 3
  }

  printf '%s\n' "$response" | jq -c --arg src "issue_comment" '
    .data.repository.pullRequest.comments.nodes[]
    | select(. != null)
    | {
        source: $src,
        thread_id: null,
        is_resolved: false,
        is_outdated: false,
        comment_id: (.databaseId // 0),
        comment_node_id: .id,
        author_login: (.author.login // ""),
        path: null,
        line: null,
        start_line: null,
        original_line: null,
        body: (.body // ""),
        created_at: (.createdAt // "")
      }
  ' | while IFS= read -r record; do
    login=$(printf '%s' "$record" | jq -r '.author_login')
    is_bot=$(is_bot_login "$login")
    printf '%s' "$record" | jq -c --argjson is_bot "$is_bot" '. + {is_bot: $is_bot}' >> "$OUT"
  done

  has_next=$(printf '%s' "$response" | jq -r '.data.repository.pullRequest.comments.pageInfo.hasNextPage')
  cursor=$(printf '%s' "$response" | jq -r '.data.repository.pullRequest.comments.pageInfo.endCursor // empty')
  if [[ "$has_next" != "true" ]]; then
    break
  fi
done

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

record_count=$(wc -l < "$OUT" | tr -d ' ')
printf 'fetch-pr-comments.sh: wrote %s records to %s\n' "$record_count" "$OUT" >&2
exit 0
