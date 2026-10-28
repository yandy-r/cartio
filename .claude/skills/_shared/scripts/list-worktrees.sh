#!/usr/bin/env bash
# List surviving worktrees for a given repo/feature combination
# Usage: list-worktrees.sh <repo-name> <feature-slug>
#
# Runs `git worktree list --porcelain` and filters results to worktrees whose
# path contains <repo-root>/.claude/worktrees/<repo-name>-<feature-slug>
# (generated bundles rewrite the root for their runtime).
#
# Output:
#   A markdown table of matching worktrees (path, branch, status) followed by
#   a "Cleanup commands" block with `git worktree remove <path>` for each row.
#   If no matching worktrees are found, prints a short message and exits 0.
#
# Exit codes:
#   0 - Success (including zero results)
#   1 - Input validation error

set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: list-worktrees.sh <repo-name> <feature-slug>

Arguments:
  repo-name     Name of the repository (e.g., my-repo)
  feature-slug  Feature branch slug (e.g., add-jwt-refresh)

Examples:
  list-worktrees.sh my-repo add-jwt-refresh
  list-worktrees.sh ystack worktree-flag

Output:
  Markdown table of matching worktrees under <repo-root>/.claude/worktrees/<repo>-<feature>/
  followed by cleanup commands. Legacy roots are not migrated or deleted.
EOF
  exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=worktree-paths.sh
source "${SCRIPT_DIR}/worktree-paths.sh"

# Collapse $HOME prefix back to ~ for human-readable display
_collapse_home() {
  local path="$1"
  echo "${path/#$HOME/\~}"
}

main() {
  # ── Input validation ──────────────────────────────────────────────────────
  if [[ $# -lt 2 ]]; then
    echo "ERROR: two positional arguments required." >&2
    usage
  fi

  local repo_name="$1"
  local feature_slug="$2"

  if [[ -z "$repo_name" ]]; then
    echo "ERROR: repo-name must not be empty." >&2
    usage
  fi

  if [[ -z "$feature_slug" ]]; then
    echo "ERROR: feature-slug must not be empty." >&2
    usage
  fi

  # ── Build the prefix we are looking for ───────────────────────────────────
  # Absolute path prefix (for matching against git output)
  local repo_root
  repo_root="$(ycc_repo_root)"

  local abs_prefix
  abs_prefix="$(ycc_feature_worktree_path "$repo_root" "$repo_name" "$feature_slug")"

  # ── Parse `git worktree list --porcelain` ─────────────────────────────────
  # Each stanza looks like:
  #   worktree /absolute/path
  #   HEAD <sha>
  #   branch refs/heads/<branch>          ← may be "detached" instead
  #
  # We collect matching entries into parallel arrays.
  local -a wt_paths=()
  local -a wt_branches=()

  local current_path=""
  local current_branch=""

  while IFS= read -r line; do
    if [[ "$line" == worktree\ * ]]; then
      # Start of a new stanza — save previous if it matched
      if [[ -n "$current_path" && "$current_path" == "${abs_prefix}"* ]]; then
        wt_paths+=("$current_path")
        wt_branches+=("${current_branch:-detached}")
      fi
      current_path="${line#worktree }"
      current_branch=""
    elif [[ "$line" == branch\ * ]]; then
      # refs/heads/<name> → strip the prefix
      current_branch="${line#branch refs/heads/}"
    fi
  done < <(git worktree list --porcelain 2>/dev/null)

  # Flush the last stanza
  if [[ -n "$current_path" && "$current_path" == "${abs_prefix}"* ]]; then
    wt_paths+=("$current_path")
    wt_branches+=("${current_branch:-detached}")
  fi

  # ── No results ────────────────────────────────────────────────────────────
  if [[ ${#wt_paths[@]} -eq 0 ]]; then
    echo "No active worktrees for ${repo_name}-${feature_slug}."
    exit 0
  fi

  # ── Determine display widths for the table ────────────────────────────────
  local parent_abs="${abs_prefix}/"
  # Minimum column widths matching header text
  local max_path=4       # "Path"
  local max_branch=6     # "Branch"
  local max_status=6     # "Status"

  local -a display_paths=()
  local -a display_statuses=()

  for i in "${!wt_paths[@]}"; do
    local dp
    dp="$(_collapse_home "${wt_paths[$i]}")"
    display_paths+=("$dp")

    local status
    if [[ "${wt_paths[$i]}" == "$parent_abs" || "${wt_paths[$i]}" == "${abs_prefix}" ]]; then
      status="parent"
    else
      status="child"
    fi
    display_statuses+=("$status")

    local plen="${#dp}"
    local blen="${#wt_branches[$i]}"
    local slen="${#status}"
    (( plen > max_path   )) && max_path=$plen
    (( blen > max_branch )) && max_branch=$blen
    (( slen > max_status )) && max_status=$slen
  done

  # ── Emit the markdown table ───────────────────────────────────────────────
  # Header row
  printf "| %-${max_path}s | %-${max_branch}s | %-${max_status}s |\n" \
    "Path" "Branch" "Status"

  # Separator row
  printf "| %s | %s | %s |\n" \
    "$(printf '%0.s-' $(seq 1 "$max_path"))" \
    "$(printf '%0.s-' $(seq 1 "$max_branch"))" \
    "$(printf '%0.s-' $(seq 1 "$max_status"))"

  # Data rows
  for i in "${!wt_paths[@]}"; do
    printf "| %-${max_path}s | %-${max_branch}s | %-${max_status}s |\n" \
      "${display_paths[$i]}" \
      "${wt_branches[$i]}" \
      "${display_statuses[$i]}"
  done

  # ── Emit cleanup commands ────────────────────────────────────────────────
  echo ""
  echo "Cleanup commands:"
  echo ""
  echo '```bash'
  for i in "${!wt_paths[@]}"; do
    printf 'git worktree remove %q\n' "${wt_paths[$i]}"
  done
  echo '```'
}

main "$@"
