#!/usr/bin/env bash
# merge-children.sh — DEPRECATED compatibility shim (issue #80, single worktree
# contract). Does NOT merge branches, remove worktrees, or delete branches.
#
# The historical behavior (fan-in merge from per-task child worktrees) was
# unsafe and is removed. Callers that still invoke this script get an explicit
# deprecation message on stderr, confirmation that the feature worktree exists
# (when it does), and exit 0 so orchestration does not break before skills are
# updated.
#
# Usage: merge-children.sh <repo-name> <feature-slug> <task-id>[,<task-id>,...]
#        (task-ids are accepted for argument compatibility; they are not used)
#
# Exit codes:
#   0  always (shim: no fan-in, no errors from missing children)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat >&2 <<'EOF'
Usage: merge-children.sh <repo-name> <feature-slug> <task-id>[,<task-id>,...]

DEPRECATED: This script is a no-op. ycc uses one feature worktree per run; there
is no per-task child merge. Remove calls to merge-children.sh in favor of working
entirely in the path printed by: setup-worktree.sh parent <repo> <feature-slug>
EOF
  printf '\nSee: %s\n' "${SCRIPT_DIR}/../references/worktree-strategy.md" >&2
  exit 1
}

feature_worktree_status() {
  local parent_path="$1"
  if [[ -d "${parent_path}" ]] && git -C "${parent_path}" rev-parse --git-dir >/dev/null 2>&1; then
    echo "OK: feature worktree present at ${parent_path}" >&2
    echo "STATUS:feature_worktree=present path=${parent_path}" >&2
    return 0
  fi

  echo "merge-children.sh: NOTICE: feature worktree not found or not a git checkout: ${parent_path}" >&2
  echo "STATUS:feature_worktree=missing path=${parent_path}" >&2
  return 1
}

# shellcheck source=worktree-paths.sh
source "${SCRIPT_DIR}/worktree-paths.sh"

main() {
  if [[ $# -ne 3 ]]; then
    echo "ERROR: expected 3 arguments, got $#" >&2
    usage
  fi

  local repo_name="$1"
  local feature_slug="$2"
  local _raw_task_ids="$3"

  if [[ -z "${repo_name}" || -z "${feature_slug}" || -z "${_raw_task_ids}" ]]; then
    echo "ERROR: repo-name, feature-slug, and task-id list must be non-empty" >&2
    usage
  fi

  local repo_root
  repo_root="$(ycc_repo_root)"

  local parent_path
  parent_path="$(ycc_feature_worktree_path "$repo_root" "$repo_name" "$feature_slug")"

  echo "merge-children.sh: DEPRECATED: no-op (single worktree contract). Task IDs ignored: ${_raw_task_ids}" >&2
  echo "merge-children.sh: Do not rely on child worktrees or fan-in merge. Use only: ${parent_path}" >&2

  if ! feature_worktree_status "${parent_path}"; then
    if [[ "${YCC_MERGE_CHILDREN_REQUIRE_FEATURE_WORKTREE:-0}" == "1" ]]; then
      echo "merge-children.sh: strict mode enabled; failing because feature worktree is unavailable" >&2
      exit 2
    fi
  fi

  exit 0
}

main "$@"
