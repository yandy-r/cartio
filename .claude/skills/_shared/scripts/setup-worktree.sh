#!/usr/bin/env bash
# setup-worktree.sh — create a feature git worktree for a ycc run (single-worktree
# contract, GitHub #80).
#
# Usage:
#   setup-worktree.sh parent <repo-name> <feature-slug> [--base-ref <branch>]
#   setup-worktree.sh child  <repo-name> <feature-slug> <task-id>   [DEPRECATED]
#
# Modes:
#   parent  Creates <repo-root>/.claude/worktrees/<repo>-<feature>/.  This is the
#           only supported worktree-creation path for the shared contract.
#           Generated bundles retarget this root for their runtime.
#
#           Default base ref: HEAD of the current branch; new branch feat/<feature>.
#           With --base-ref: checks out the existing branch (e.g. PR head). If the
#           branch is missing locally, runs git fetch from origin once.
#
#   child   DEPRECATED. Does not create a separate per-task worktree. Prints a
#           warning to stderr and echoes the *feature* worktree path (same as
#           parent) if it already exists; otherwise instructs the caller to run
#           `parent` first. Non-destructive — no new git worktrees for “children”.
#
# Idempotency (parent): if the path exists and is on the expected branch, echo path.
#
# Exit codes:
#   0  success (path echoed to stdout; child deprecation may print to stderr)
#   1  error (message on stderr)

set -euo pipefail

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
  cat >&2 <<'EOF'
Usage:
  setup-worktree.sh parent <repo-name> <feature-slug> [--base-ref <branch>]
  setup-worktree.sh child  <repo-name> <feature-slug> <task-id>   (deprecated)

Arguments:
  repo-name         Short name of the repository (e.g. "ystack")
  feature-slug      Kebab-case feature identifier (e.g. "add-widget")
  task-id           (child only, ignored for path) legacy parallel-task id
  --base-ref <b>    (parent only) Check out the existing branch <b> into the
                    worktree instead of creating a new feat/<feature> branch.

Worktree (single contract):
  feature  <repo-root>/.claude/worktrees/<repo>-<feature>/
           (generated bundles rewrite the root for their runtime)

Set YCC_WORKTREE_ROOT to override the root. Relative overrides are resolved
against the current repository root. Legacy roots are not migrated or touched.

'child' mode is deprecated: it echoes the feature worktree path if present; it does
not create separate child worktrees. See worktree-strategy.md.
EOF
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_error() {
  echo "setup-worktree.sh: error: $*" >&2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=worktree-paths.sh
source "${SCRIPT_DIR}/worktree-paths.sh"

# Replace every '.' with '-' in a task-id string.
normalize_task_id() {
  local raw="$1"
  echo "${raw//./-}"
}

# Return the branch currently checked out in a worktree path.
# Uses `git worktree list --porcelain` output.
_worktree_branch() {
  local path="$1"
  git worktree list --porcelain 2>/dev/null | awk -v p="$path" '
    /^worktree / { cur = substr($0, 10) }
    /^branch /   { if (cur == p) { print substr($0, 8); exit } }
  '
}

# ---------------------------------------------------------------------------
# Mode: parent (feature worktree)
# ---------------------------------------------------------------------------

setup_parent() {
  local repo_name="$1"
  local feature_slug="$2"
  local base_ref="${3:-}"

  if [[ -z "$repo_name" || -z "$feature_slug" ]]; then
    _error "repo-name and feature-slug must not be empty"
    usage
    exit 1
  fi

  local repo_root
  repo_root="$(ycc_repo_root)"

  local worktrees_dir
  worktrees_dir="$(ycc_worktree_root "$repo_root")"

  local parent_path
  parent_path="$(ycc_feature_worktree_path "$repo_root" "$repo_name" "$feature_slug")"

  local branch
  if [[ -n "$base_ref" ]]; then
    branch="$base_ref"
  else
    branch="feat/${feature_slug}"
  fi

  mkdir -p "$worktrees_dir"

  if [[ -d "$parent_path" ]]; then
    local actual_branch
    actual_branch="$(_worktree_branch "$parent_path")"

    local expected_ref="refs/heads/${branch}"
    if [[ "$actual_branch" == "$expected_ref" || "$actual_branch" == "$branch" ]]; then
      echo "$parent_path"
      return 0
    else
      _error "worktree already exists at '$parent_path' but is on branch '${actual_branch}' (expected '${expected_ref}')"
      exit 1
    fi
  fi

  if [[ -n "$base_ref" ]]; then
    if ! git rev-parse --verify "refs/heads/${base_ref}" >/dev/null 2>&1; then
      if ! git fetch origin "${base_ref}:${base_ref}" >/dev/null 2>&1; then
        _error "branch '${base_ref}' does not exist locally and could not be fetched from origin"
        exit 1
      fi
    fi

    # Redirect git's stdout to stderr so callers capturing the script's stdout
    # via $(...) only receive the worktree path on the final echo below.
    git worktree add "$parent_path" "$base_ref" >&2
  else
    git worktree add -B "$branch" "$parent_path" HEAD >&2
  fi

  echo "$parent_path"
}

# ---------------------------------------------------------------------------
# Mode: child (deprecated — no separate worktree; echo feature path if exists)
# ---------------------------------------------------------------------------

setup_child() {
  local repo_name="$1"
  local feature_slug="$2"
  local raw_task_id="$3"

  if [[ -z "$repo_name" || -z "$feature_slug" || -z "$raw_task_id" ]]; then
    _error "repo-name, feature-slug, and task-id must not be empty"
    usage
    exit 1
  fi

  local _task_id
  _task_id="$(normalize_task_id "$raw_task_id")"

  local repo_root
  repo_root="$(ycc_repo_root)"

  local feature_path
  feature_path="$(ycc_feature_worktree_path "$repo_root" "$repo_name" "$feature_slug")"

  echo "setup-worktree.sh: DEPRECATED: 'child' does not create a per-task worktree (task-id=${_task_id}). Use a single feature worktree; all agents share: ${feature_path}" >&2

  if [[ -d "$feature_path" ]]; then
    if git -C "$feature_path" rev-parse --git-dir >/dev/null 2>&1; then
      echo "$feature_path"
      return 0
    fi
  fi

  _error "feature worktree not found at '${feature_path}'. Run: setup-worktree.sh parent ${repo_name} ${feature_slug}"
  exit 1
}

parse_parent_args() {
  local -n out_repo="$1"
  local -n out_slug="$2"
  local -n out_base_ref="$3"
  shift 3

  out_repo=""
  out_slug=""
  out_base_ref=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --base-ref)
        if [[ $# -lt 2 ]]; then
          _error "'--base-ref' requires a branch argument"
          usage
          exit 1
        fi
        out_base_ref="$2"
        shift 2
        ;;
      --base-ref=*)
        out_base_ref="${1#--base-ref=}"
        shift
        ;;
      --)
        shift
        break
        ;;
      -*)
        _error "unknown flag in 'parent' mode: $1"
        usage
        exit 1
        ;;
      *)
        if [[ -z "$out_repo" ]]; then
          out_repo="$1"
        elif [[ -z "$out_slug" ]]; then
          out_slug="$1"
        else
          _error "'parent' mode takes at most 2 positional arguments: <repo-name> <feature-slug>"
          usage
          exit 1
        fi
        shift
        ;;
    esac
  done
  : "$out_base_ref"
}

run_parent_mode() {
  local p_repo p_slug p_base_ref
  parse_parent_args p_repo p_slug p_base_ref "$@"
  if [[ -z "$p_repo" || -z "$p_slug" ]]; then
    _error "'parent' mode requires <repo-name> and <feature-slug>"
    usage
    exit 1
  fi
  setup_parent "$p_repo" "$p_slug" "$p_base_ref"
}

run_child_mode() {
  if [[ $# -ne 3 ]]; then
    _error "'child' mode requires exactly 3 arguments: <repo-name> <feature-slug> <task-id>"
    usage
    exit 1
  fi
  setup_child "$1" "$2" "$3"
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

main() {
  local mode="${1:-}"
  case "$mode" in
    parent)
      shift
      run_parent_mode "$@"
      ;;
    child)
      shift
      run_child_mode "$@"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      _error "first argument must be 'parent' or 'child'"
      usage
      exit 1
      ;;
  esac
}

main "$@"
