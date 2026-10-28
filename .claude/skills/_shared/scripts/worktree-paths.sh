#!/usr/bin/env bash
# Shared path helpers for ycc feature worktrees.
#
# Source this file from other scripts. It intentionally does not enable
# set -euo pipefail so callers keep control of shell options.

ycc_repo_root() {
  local root
  if root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    printf '%s\n' "$root"
    return 0
  fi
  pwd -P
}

ycc_expand_worktree_root() {
  local repo_root="$1"
  local raw_root="$2"

  case "$raw_root" in
    "")
      # Default for this runtime bundle's build. Portable `agents` builds
      # replace only this marker line with ycc_runtime_worktree_root below.
      printf '%s/.claude/worktrees\n' "$repo_root" # @YCC_DEFAULT_WORKTREE_ROOT@
      ;;
    "~")
      printf '%s\n' "$HOME"
      ;;
    "~"/*)
      printf '%s/%s\n' "$HOME" "${raw_root#"~/"}"
      ;;
    /*)
      printf '%s\n' "$raw_root"
      ;;
    *)
      printf '%s/%s\n' "$repo_root" "$raw_root"
      ;;
  esac
}

ycc_worktree_root() {
  local repo_root="${1:-}"
  if [[ -z "$repo_root" ]]; then
    repo_root="$(ycc_repo_root)"
  fi

  ycc_expand_worktree_root "$repo_root" "${YCC_WORKTREE_ROOT:-}"
}

ycc_feature_worktree_path() {
  local repo_root="$1"
  local repo_name="$2"
  local feature_slug="$3"
  local root

  root="$(ycc_worktree_root "$repo_root")"
  printf '%s/%s-%s\n' "$root" "$repo_name" "$feature_slug"
}

# Used only by portable (agents) builds, which swap the default marker line in
# ycc_expand_worktree_root for this resolver. Evidence-based runtime markers;
# missing or conflicting markers (nested agents) fall back to the portable
# .agents root. OMP bash sets both OMPCODE=1 and CLAUDECODE=1, so OMPCODE
# wins and consumes the CLAUDECODE marker. See worktree-strategy.md §1 for the
# evidence list.
ycc_runtime_worktree_root() {
  local repo_root="$1"
  local runtime=""
  local count=0

  if [[ "${OPENCODE:-}" == "1" ]]; then
    runtime="opencode"
    count=$((count + 1))
  fi
  if [[ "${OMPCODE:-}" == "1" ]]; then
    runtime="omp"
    count=$((count + 1))
  elif [[ "${CLAUDECODE:-}" == "1" ]]; then
    runtime="claude"
    count=$((count + 1))
  fi
  if [[ -n "${CODEX_THREAD_ID:-}" ]]; then
    runtime="codex"
    count=$((count + 1))
  fi

  if [[ "$count" -ne 1 ]]; then
    runtime="agents"
  fi
  printf '%s/.%s/worktrees\n' "$repo_root" "$runtime"
}
