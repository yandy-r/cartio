#!/usr/bin/env bash
# release-state.sh — read the ycc release state from a repo-root RELEASING.md.
#
# Usage:
#   release-state.sh [--repo DIR] [get]
#   release-state.sh [--repo DIR] base [--head REF]
#   release-state.sh [--repo DIR] check
#
# Subcommands:
#   get    (default) print key=value lines:
#            present file model trunk maintenance latest_maintenance frozen
#            support backport_label tracker tracker_ref
#          With no RELEASING.md, prints only "present=0".
#   base   print the PR base branch for REF (default HEAD): the maintenance
#          branch whose own commits REF contains, else the trunk. With no
#          RELEASING.md, prints origin/HEAD's branch, falling back to "main".
#   check  compare the rendered table with the block and verify the trunk and
#          maintenance branches exist on origin. Prints "finding: ..." lines.
#
# Exit codes:
#   0  success (get/base), or no findings (check)
#   1  usage error, or check found problems
#   2  RELEASING.md exists but its state block is malformed
#
# Format reference:
#   .claude/skills/_shared/references/branching-model.md#state-block

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/release-state-lib.sh
source "${SCRIPT_DIR}/lib/release-state-lib.sh"

usage() {
  sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
}

_error() {
  echo "release-state: $*" >&2
}

REPO_DIR="."
SUBCOMMAND="get"
HEAD_REF="HEAD"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -lt 2 ]] && { _error "--repo requires a directory"; exit 1; }
      REPO_DIR="$2"
      shift 2
      ;;
    --head)
      [[ $# -lt 2 ]] && { _error "--head requires a ref"; exit 1; }
      HEAD_REF="$2"
      shift 2
      ;;
    get | base | check)
      SUBCOMMAND="$1"
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      _error "unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ ! -d "$REPO_DIR" ]]; then
  _error "no such directory: $REPO_DIR"
  exit 1
fi

ROOT="$(rs_repo_root "$REPO_DIR")"
FILE="${ROOT}/RELEASING.md"
PRESENT=0

if [[ -f "$FILE" ]]; then
  PRESENT=1
  if ! rs_parse_file "$FILE"; then
    _error "${RS_ERROR} (run ${RS_HINT_AUDIT})"
    exit 2
  fi
fi

git_in_root() {
  git -C "$ROOT" "$@"
}

# ref_exists <ref> — true when the ref resolves in the repo.
ref_exists() {
  git_in_root rev-parse --verify --quiet "$1^{commit}" >/dev/null 2>&1
}

cmd_get() {
  if [[ "$PRESENT" -eq 0 ]]; then
    echo "present=0"
    return 0
  fi
  echo "present=1"
  echo "file=${FILE}"
  echo "model=${RS_MODEL}"
  echo "trunk=${RS_TRUNK}"
  echo "maintenance=${RS_MAINTENANCE}"
  echo "latest_maintenance=$(rs_first "$RS_MAINTENANCE")"
  echo "frozen=${RS_FROZEN}"
  echo "support=${RS_SUPPORT}"
  echo "backport_label=${RS_BACKPORT_LABEL}"
  echo "tracker=${RS_TRACKER}"
  echo "tracker_ref=${RS_TRACKER_REF}"
}

cmd_base() {
  local branch n m
  if [[ "$PRESENT" -eq 0 ]]; then
    branch="$(git_in_root symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || true)"
    branch="${branch#origin/}"
    echo "${branch:-main}"
    return 0
  fi
  if ref_exists "$HEAD_REF" && ref_exists "origin/${RS_TRUNK}"; then
    local IFS=','
    for branch in $RS_MAINTENANCE; do
      ref_exists "origin/${branch}" || continue
      n="$(git_in_root rev-list --count "origin/${branch}" "^origin/${RS_TRUNK}")"
      m="$(git_in_root rev-list --count "origin/${branch}" "^origin/${RS_TRUNK}" "^${HEAD_REF}")"
      if [[ "$n" -gt 0 && "$m" -lt "$n" ]]; then
        echo "$branch"
        return 0
      fi
    done
  fi
  echo "$RS_TRUNK"
}

cmd_check() {
  local findings=0 expected actual heads branch
  if [[ "$PRESENT" -eq 0 ]]; then
    echo "finding: no RELEASING.md at ${ROOT} (run ${RS_HINT_CREATE})"
    return 1
  fi
  expected="$(rs_render_table)"
  if actual="$(rs_extract_table "$FILE")"; then
    if [[ "$expected" != "$actual" ]]; then
      echo "finding: the Current state table does not match the state block (run release-state-update.sh --render)"
      findings=$((findings + 1))
    fi
  else
    echo "finding: the Current state table markers are missing (run release-state-update.sh --render)"
    findings=$((findings + 1))
  fi
  if git_in_root remote get-url origin >/dev/null 2>&1; then
    if heads="$(git_in_root ls-remote --heads origin 2>/dev/null)"; then
      for branch in "$RS_TRUNK" ${RS_MAINTENANCE//,/ }; do
        if ! grep -q "refs/heads/${branch}\$" <<<"$heads"; then
          echo "finding: branch '${branch}' named in the state does not exist on origin"
          findings=$((findings + 1))
        fi
      done
    else
      _error "could not list origin branches; skipped the branch existence check"
    fi
  else
    _error "no origin remote; skipped the branch existence check"
  fi
  [[ "$findings" -eq 0 ]]
}

case "$SUBCOMMAND" in
  get) cmd_get ;;
  base) cmd_base ;;
  check) cmd_check ;;
esac
