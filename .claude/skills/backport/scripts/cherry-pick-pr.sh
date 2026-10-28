#!/usr/bin/env bash
# cherry-pick-pr.sh — cherry-pick one merged trunk PR onto a maintenance branch
# in a fresh worktree. Never pushes.
#
# Usage:
#   cherry-pick-pr.sh <pr_number> <release/X.Y> [--sha SHA] [--repo DIR]
#
# Steps:
#   1. Check that <release/X.Y> is an active maintenance branch in RELEASING.md.
#   2. Resolve the merge/squash commit: --sha, else
#      `gh pr view <pr> --json state,mergeCommit` (the PR must be merged).
#   3. Fetch origin/<release/X.Y>, create branch backport/X.Y/<pr>-<slug> from it
#      (slug from the commit subject), and add a worktree for that branch via
#      _shared/scripts/setup-worktree.sh (honors YCC_WORKTREE_ROOT).
#   4. `git cherry-pick -x <sha>` (with `-m 1` for a true merge commit).
#
# Output (stdout, key=value):
#   worktree=<path>
#   branch=<name>
#   conflicts=<comma-separated paths>   (exit 3 only)
#
# Exit codes:
#   0  cherry-pick committed cleanly in the new worktree
#   1  usage error, target is not an active maintenance branch, no
#      RELEASING.md, PR not merged, commit or branch missing, branch already
#      exists, or the change is already on the target (empty cherry-pick)
#   2  RELEASING.md exists but its state block is malformed
#   3  conflict — the worktree is left mid-cherry-pick for resolution
#
# Rules: .claude/skills/_shared/references/branching-model.md#backporting

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_DIR="${SCRIPT_DIR}/../../_shared/scripts"
STATE_SCRIPT="${SHARED_DIR}/release-state.sh"
SETUP_WORKTREE="${SHARED_DIR}/setup-worktree.sh"
SLUG_MAX=40

_error() {
  echo "cherry-pick-pr: $*" >&2
}

usage() {
  sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
}

PR_NUMBER=""
TARGET=""
SHA=""
REPO_DIR="."

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha)
      [[ $# -lt 2 || -z "${2:-}" ]] && { _error "--sha requires a commit"; exit 1; }
      SHA="$2"
      shift 2
      ;;
    --repo)
      [[ $# -lt 2 ]] && { _error "--repo requires a directory"; exit 1; }
      REPO_DIR="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      _error "unknown flag: $1"
      usage
      exit 1
      ;;
    *)
      if [[ -z "$PR_NUMBER" ]]; then
        PR_NUMBER="$1"
      elif [[ -z "$TARGET" ]]; then
        TARGET="$1"
      else
        _error "unexpected argument: $1"
        usage
        exit 1
      fi
      shift
      ;;
  esac
done

if [[ ! "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  _error "<pr_number> must be a number (got '${PR_NUMBER}')"
  usage
  exit 1
fi
if [[ ! "$TARGET" =~ ^release/([0-9]+\.[0-9]+)$ ]]; then
  _error "<target> must be release/X.Y (got '${TARGET}')"
  usage
  exit 1
fi
VERSION="${BASH_REMATCH[1]}"
if [[ ! -d "$REPO_DIR" ]]; then
  _error "no such directory: $REPO_DIR"
  exit 1
fi

ROOT="$(git -C "$REPO_DIR" rev-parse --show-toplevel 2>/dev/null)" || {
  _error "${REPO_DIR} is not inside a git repository"
  exit 1
}

git_root() {
  git -C "$ROOT" "$@"
}

# ---------------------------------------------------------------------------
# Release state: the target must be an active maintenance branch
# ---------------------------------------------------------------------------

rc=0
STATE_OUT="$(bash "$STATE_SCRIPT" --repo "$ROOT" get)" || rc=$?
if [[ "$rc" -eq 2 ]]; then
  _error "RELEASING.md has a malformed state block (see above); run /release-model --audit"
  exit 2
elif [[ "$rc" -ne 0 ]]; then
  _error "could not read the release state"
  exit 1
fi
if [[ "$(sed -n 's/^present=//p' <<<"$STATE_OUT")" != "1" ]]; then
  _error "no RELEASING.md in ${ROOT}; backports need a release-branches model (run /release-model)"
  exit 1
fi
MAINTENANCE="$(sed -n 's/^maintenance=//p' <<<"$STATE_OUT")"
if [[ ",${MAINTENANCE}," != *",${TARGET},"* ]]; then
  _error "${TARGET} is not an active maintenance branch (maintenance: ${MAINTENANCE:-none})"
  exit 1
fi

# ---------------------------------------------------------------------------
# Resolve the commit to pick
# ---------------------------------------------------------------------------

if [[ -z "$SHA" ]]; then
  if ! command -v gh >/dev/null 2>&1; then
    _error "gh is not installed; pass --sha <merge-commit> or install the GitHub CLI"
    exit 1
  fi
  if ! pr_line="$(cd "$ROOT" && gh pr view "$PR_NUMBER" --json state,mergeCommit \
    --jq '[.state, (.mergeCommit.oid // "")] | @tsv' 2>&1)"; then
    _error "gh pr view ${PR_NUMBER} failed: ${pr_line}"
    exit 1
  fi
  IFS=$'\t' read -r pr_state SHA <<<"$pr_line"
  if [[ "$pr_state" != "MERGED" || -z "$SHA" ]]; then
    _error "PR #${PR_NUMBER} is ${pr_state:-unknown}, not merged; only merged PRs are backported"
    exit 1
  fi
fi

if ! git_root fetch -q origin "refs/heads/${TARGET}:refs/remotes/origin/${TARGET}" 2>/dev/null; then
  _error "origin has no branch ${TARGET}; create it from its tag first, e.g.: git push origin v${VERSION}.0^{commit}:refs/heads/${TARGET}"
  exit 1
fi
if ! git_root cat-file -e "${SHA}^{commit}" 2>/dev/null; then
  git_root fetch -q origin 2>/dev/null || true
fi
if ! SHA="$(git_root rev-parse --verify --quiet "${SHA}^{commit}")"; then
  _error "commit ${SHA:-?} not found locally or on origin"
  exit 1
fi

# ---------------------------------------------------------------------------
# Branch + worktree
# ---------------------------------------------------------------------------

slugify() {
  local s
  s="$(printf '%s' "$1" | sed -E 's/\(#[0-9]+\)//g' | tr '[:upper:]' '[:lower:]' |
    sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')"
  s="${s:0:$SLUG_MAX}"
  s="${s%-}"
  printf '%s' "${s:-change}"
}

SLUG="$(slugify "$(git_root log -1 --format=%s "$SHA")")"
BRANCH="backport/${VERSION}/${PR_NUMBER}-${SLUG}"
REPO_NAME="$(basename "$(git_root rev-parse --path-format=absolute --git-common-dir | sed 's#/\.git$##')")"
FEATURE_SLUG="backport-${VERSION//./-}-${PR_NUMBER}"

if git_root show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  _error "branch ${BRANCH} already exists; finish or delete it (and its worktree) first"
  exit 1
fi

git_root branch --no-track "$BRANCH" "origin/${TARGET}" >/dev/null
if ! WORKTREE="$(cd "$ROOT" && bash "$SETUP_WORKTREE" parent "$REPO_NAME" "$FEATURE_SLUG" --base-ref "$BRANCH")"; then
  git_root branch -D "$BRANCH" >/dev/null 2>&1 || true
  _error "could not create a worktree for ${BRANCH}"
  exit 1
fi

# ---------------------------------------------------------------------------
# Cherry-pick
# ---------------------------------------------------------------------------

PICK_ARGS=(-x)
parent_count="$(git_root rev-list --parents -n 1 "$SHA" | wc -w)"
if [[ "$parent_count" -gt 2 ]]; then
  PICK_ARGS+=(-m 1)
fi

pick_log=""
if pick_log="$(git -C "$WORKTREE" cherry-pick "${PICK_ARGS[@]}" "$SHA" 2>&1)"; then
  echo "worktree=${WORKTREE}"
  echo "branch=${BRANCH}"
  exit 0
fi

conflicts="$(git -C "$WORKTREE" diff --name-only --diff-filter=U | paste -sd, -)"
if [[ -n "$conflicts" ]]; then
  echo "worktree=${WORKTREE}"
  echo "branch=${BRANCH}"
  echo "conflicts=${conflicts}"
  _error "cherry-pick of ${SHA} onto ${TARGET} conflicted; resolve in ${WORKTREE}, then 'git cherry-pick --continue'"
  exit 3
fi

# Not a conflict: most often the change is already on the target (empty pick).
git -C "$WORKTREE" cherry-pick --abort >/dev/null 2>&1 || true
git_root worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
git_root branch -D "$BRANCH" >/dev/null 2>&1 || true
_error "cherry-pick of ${SHA} onto ${TARGET} failed without conflicts (already applied?):"
printf '%s\n' "$pick_log" >&2
exit 1
