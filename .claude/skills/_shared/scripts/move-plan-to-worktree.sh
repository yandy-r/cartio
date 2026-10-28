#!/usr/bin/env bash
# move-plan-to-worktree.sh — relocate plan artifacts from the main checkout into a
# pre-created feature worktree (single-worktree contract, GitHub #80).
#
# Usage:
#   move-plan-to-worktree.sh <plan-path> <wt-parent-path> [extra-path ...]
#   move-plan-to-worktree.sh --help
#
# Arguments:
#   plan-path        Path to the primary plan file. May be absolute or relative
#                    to the current repo root. Its new path inside the worktree
#                    is echoed to stdout.
#   wt-parent-path   Absolute path to the feature worktree, as returned by
#                    setup-worktree.sh parent.
#   extra-path ...   Optional companion artifacts (e.g. shared.md, research
#                    notes) that should follow the plan into the worktree. Their
#                    new locations are reported on stderr only.
#
# Contract:
#   Plan artifacts are pre-commit and live in main when the implementor starts.
#   This helper MOVES them into the worktree exactly once. It NEVER copies and
#   NEVER syncs. After this script runs, the main checkout is clean (no
#   untracked plan files) and the worktree owns the canonical copy.
#
# Behavior:
#   - If the source path does not exist, the helper treats it as an idempotent
#     no-op and (for the primary plan) still echoes the expected destination
#     path. This makes re-runs safe.
#   - If the source already resolves to a path inside <wt-parent-path>, no move
#     occurs and the existing in-worktree path is echoed.
#   - Otherwise: mkdir -p the destination's parent inside the worktree, then mv
#     the file in. Plain mv is used (cross-worktree git mv is not supported).
#
# Exit codes:
#   0  success
#   1  invalid arguments or move failure (message on stderr)

set -euo pipefail

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
  cat >&2 <<'EOF'
Usage:
  move-plan-to-worktree.sh <plan-path> <wt-parent-path> [extra-path ...]

Arguments:
  plan-path        Primary plan file (absolute or relative to repo root).
                   Its destination inside the worktree is echoed to stdout.
  wt-parent-path   Feature worktree path, as returned by setup-worktree.sh.
  extra-path ...   Optional companion artifacts to relocate alongside the plan.
                   Their destinations are reported on stderr only.

Contract:
  Plan artifacts MOVE into the worktree exactly once. Never copy, never sync.
EOF
}

_error() {
  echo "move-plan-to-worktree.sh: error: $*" >&2
}

_debug() {
  echo "move-plan-to-worktree.sh: $*" >&2
}

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Resolve to an absolute path. Tolerates non-existent paths (for idempotency).
_abs_path() {
  local p="$1"
  if [[ "$p" = /* ]]; then
    printf '%s\n' "$p"
    return 0
  fi
  printf '%s/%s\n' "$(pwd -P)" "$p"
}

# Locate the repo root for a path. Falls back to the current working directory
# if `git rev-parse` is unavailable for the given path.
_repo_root_for() {
  local p="$1"
  local search_dir
  if [[ -d "$p" ]]; then
    search_dir="$p"
  else
    search_dir="$(dirname -- "$p")"
  fi
  while [[ ! -d "$search_dir" && "$search_dir" != "/" ]]; do
    search_dir="$(dirname -- "$search_dir")"
  done
  (cd -- "$search_dir" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null) \
    || pwd -P
}

# Strip a leading prefix from a path, if it matches. Echoes the remainder
# (without a leading '/') on success.
_strip_prefix() {
  local prefix="${1%/}"
  local path="$2"
  if [[ "$path" == "$prefix" ]]; then
    printf '\n'
    return 0
  fi
  if [[ "$path" == "$prefix"/* ]]; then
    printf '%s\n' "${path#"$prefix"/}"
    return 0
  fi
  return 1
}

# Move one artifact. Echoes the destination on stdout when echo_dest=1.
# Returns 0 on success or skip; non-zero only on a real failure.
_move_one() {
  local src_input="$1"
  local wt_parent="$2"
  local echo_dest="$3"

  local src_abs
  src_abs="$(_abs_path "$src_input")"

  # If the input is already inside the worktree, no work to do.
  if rel="$(_strip_prefix "$wt_parent" "$src_abs")"; then
    _debug "source already inside worktree: $src_abs"
    if [[ "$echo_dest" = "1" ]]; then
      printf '%s\n' "$src_abs"
    fi
    return 0
  fi

  local repo_root
  repo_root="$(_repo_root_for "$src_abs")"

  local rel
  if ! rel="$(_strip_prefix "$repo_root" "$src_abs")"; then
    _error "source path '$src_input' is not under repo root '$repo_root'"
    return 1
  fi
  if [[ -z "$rel" ]]; then
    _error "source path resolves to repo root itself: '$src_input'"
    return 1
  fi

  local dst="${wt_parent%/}/$rel"

  # Idempotent: destination already populated.
  if [[ -e "$dst" ]]; then
    _debug "destination already exists, skipping move: $dst"
    if [[ ! -e "$src_abs" ]]; then
      :
    elif [[ "$src_abs" -ef "$dst" ]]; then
      :
    else
      _debug "  note: source still present at '$src_abs' (manual cleanup may be needed)"
    fi
    if [[ "$echo_dest" = "1" ]]; then
      printf '%s\n' "$dst"
    fi
    return 0
  fi

  # Idempotent: source missing → previously moved or never existed.
  if [[ ! -e "$src_abs" ]]; then
    _debug "source not found, treating as already moved: $src_abs"
    if [[ "$echo_dest" = "1" ]]; then
      printf '%s\n' "$dst"
    fi
    return 0
  fi

  mkdir -p -- "$(dirname -- "$dst")"

  if ! mv -- "$src_abs" "$dst"; then
    _error "failed to move '$src_abs' → '$dst'"
    return 1
  fi

  _debug "moved: $rel → $dst"
  if [[ "$echo_dest" = "1" ]]; then
    printf '%s\n' "$dst"
  fi
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

main() {
  if [[ $# -ge 1 && ( "$1" = "--help" || "$1" = "-h" ) ]]; then
    usage
    exit 0
  fi

  if [[ $# -lt 2 ]]; then
    _error "expected at least 2 arguments: <plan-path> <wt-parent-path> [extra-path ...]"
    usage
    exit 1
  fi

  local plan_path="$1"
  local wt_parent="$2"
  shift 2

  if [[ -z "$plan_path" || -z "$wt_parent" ]]; then
    _error "plan-path and wt-parent-path must not be empty"
    exit 1
  fi

  if [[ ! -d "$wt_parent" ]]; then
    _error "wt-parent-path '$wt_parent' does not exist or is not a directory"
    exit 1
  fi

  # Normalise the worktree path to its absolute form so prefix checks work.
  local wt_parent_abs
  wt_parent_abs="$(cd -- "$wt_parent" && pwd -P)"

  _move_one "$plan_path" "$wt_parent_abs" 1

  local extra
  for extra in "$@"; do
    [[ -z "$extra" ]] && continue
    _move_one "$extra" "$wt_parent_abs" 0
  done
}

main "$@"
