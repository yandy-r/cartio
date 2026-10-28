#!/usr/bin/env bash
# pr-guard.sh — PR base selection, sync-merge guard and backport prompt for the
# ycc release model. Shared by git-workflow and prp-pr; runs in the current
# repository.
#
# Usage:
#   pr-guard.sh base [--base BRANCH] [--head REF]
#   pr-guard.sh sync-check [--base BRANCH] [--head REF] [--head-branch NAME]
#   pr-guard.sh backport-label [--base BRANCH] [--head REF] [--title TITLE]
#                              [--labels L1,L2,...]
#
# Subcommands:
#   base            print the PR base branch: --base when given, else
#                   `release-state.sh base --head REF` when RELEASING.md exists,
#                   else the forge default branch (origin/HEAD, then "main").
#   sync-check      refuse a PR that would sync one long-lived branch into
#                   another. Prints result=ok|skipped|refused plus reason= lines.
#                   Needs origin/<base> fetched. Skipped without RELEASING.md.
#   backport-label  decide whether to offer a backport. Prints backport=0 with
#                   reason=, or backport=1 with branch= and label=.
#
# Options:
#   --base BRANCH       PR base (default: the `base` subcommand's answer)
#   --head REF          the PR's head commit (default HEAD)
#   --head-branch NAME  the PR's head branch name (default: the current branch
#                       when --head is HEAD, else REF without "origin/")
#   --title TITLE       PR title, checked for fix: / fix(scope):
#   --labels LIST       comma-separated PR labels, checked for type:bug
#
# Exit codes:
#   0  success (sync-check: ok or skipped)
#   1  usage error, or a git ref needed for the check is missing
#   2  RELEASING.md exists but its state block is malformed
#   3  sync-check refused the PR
#
# Rules and definitions:
#   .claude/skills/_shared/references/pr-base-and-backport.md
#   .claude/skills/_shared/references/branching-model.md#rules

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_STATE_SH="${SCRIPT_DIR}/release-state.sh"
# shellcheck source=lib/release-state-lib.sh
source "${SCRIPT_DIR}/lib/release-state-lib.sh"
# shellcheck source=lib/forge.sh
source "${SCRIPT_DIR}/lib/forge.sh"
# shellcheck source=lib/backport-lib.sh
source "${SCRIPT_DIR}/lib/backport-lib.sh"

RULES_REF='_shared/references/branching-model.md#rules'

usage() {
  sed -n '2,37p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
}

_error() {
  echo "pr-guard: $*" >&2
}

SUBCOMMAND=""
BASE=""
HEAD_REF="HEAD"
HEAD_BRANCH=""
TITLE=""
LABELS=""

need_value() {
  [[ $# -ge 2 ]] || { _error "$1 requires a value"; exit 1; }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    base | sync-check | backport-label)
      [[ -n "$SUBCOMMAND" ]] && { _error "only one subcommand allowed"; exit 1; }
      SUBCOMMAND="$1"
      shift
      ;;
    --base) need_value "$@"; BASE="$2"; shift 2 ;;
    --head) need_value "$@"; HEAD_REF="$2"; shift 2 ;;
    --head-branch) need_value "$@"; HEAD_BRANCH="$2"; shift 2 ;;
    --title) need_value "$@"; TITLE="$2"; shift 2 ;;
    --labels) need_value "$@"; LABELS="$2"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) _error "unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$SUBCOMMAND" ]]; then
  _error "a subcommand is required"
  usage
  exit 1
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  _error "not inside a git repository"
  exit 1
fi

# ---------------------------------------------------------------------------
# Release state
# ---------------------------------------------------------------------------

STATE_PRESENT=0
STATE_MODEL=""
STATE_TRUNK=""
STATE_MAINTENANCE=""
STATE_LATEST=""
STATE_LABEL_TEMPLATE=""

# kv_get <key=value lines> <key> — print the value for key.
kv_get() {
  sed -n "s/^$2=//p" <<<"$1"
}

# load_state — read release-state.sh get; exit with its code on failure
# (2 = malformed block, message already names /release-model --audit).
load_state() {
  local out rc=0
  out="$(bash "$RELEASE_STATE_SH" get 2>&1)" || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    printf '%s\n' "$out" >&2
    exit "$rc"
  fi
  STATE_PRESENT="$(kv_get "$out" present)"
  [[ "$STATE_PRESENT" == "1" ]] || return 0
  STATE_MODEL="$(kv_get "$out" model)"
  STATE_TRUNK="$(kv_get "$out" trunk)"
  STATE_MAINTENANCE="$(kv_get "$out" maintenance)"
  STATE_LATEST="$(kv_get "$out" latest_maintenance)"
  STATE_LABEL_TEMPLATE="$(kv_get "$out" backport_label)"
}

ref_exists() {
  git rev-parse --verify --quiet "$1^{commit}" >/dev/null 2>&1
}

# resolve_base — BASE from --base, the state, or the forge default branch.
resolve_base() {
  [[ -n "$BASE" ]] && return 0
  if [[ "$STATE_PRESENT" == "1" ]]; then
    BASE="$(bash "$RELEASE_STATE_SH" base --head "$HEAD_REF")"
  else
    BASE="$(forge_default_branch "$(forge_detect_provider origin)")"
  fi
}

# resolve_head_branch — HEAD_BRANCH from --head-branch or the head ref.
resolve_head_branch() {
  [[ -n "$HEAD_BRANCH" ]] && return 0
  if [[ "$HEAD_REF" == "HEAD" ]]; then
    HEAD_BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  else
    HEAD_BRANCH="${HEAD_REF#origin/}"
  fi
}

# is_long_lived <branch> — the state trunk or any release/* branch.
is_long_lived() {
  [[ "$1" == "$STATE_TRUNK" || "$1" == release/* ]]
}

# long_lived_tips — origin/<trunk> and every origin/release/* ref.
long_lived_tips() {
  if ref_exists "origin/${STATE_TRUNK}"; then
    echo "origin/${STATE_TRUNK}"
  fi
  git for-each-ref --format='%(refname:short)' 'refs/remotes/origin/release/*'
}

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

cmd_base() {
  resolve_base
  echo "$BASE"
}

refuse() {
  echo "result=refused"
  echo "reason=$1"
  echo "rule=${RULES_REF} (rule 3: never merge one long-lived branch into another)"
  exit 3
}

cmd_sync_check() {
  local merge second tip base_ref
  if [[ "$STATE_PRESENT" != "1" ]]; then
    echo "result=skipped"
    echo "reason=no RELEASING.md"
    return 0
  fi
  resolve_base
  resolve_head_branch

  if [[ -n "$HEAD_BRANCH" && "$HEAD_BRANCH" != "$BASE" ]] &&
    is_long_lived "$HEAD_BRANCH" && is_long_lived "$BASE"; then
    # Single quotes around /backport: generated bundles rewrite it to a
    # $-prefixed name that bash would expand inside double quotes.
    refuse "head '${HEAD_BRANCH}' and base '${BASE}' are both long-lived branches; move fixes by cherry-pick"' (/backport), not by a sync PR'
  fi

  base_ref="origin/${BASE}"
  if ! ref_exists "$base_ref"; then
    _error "${base_ref} not found; run 'git fetch origin ${BASE}' first"
    exit 1
  fi
  if ! ref_exists "$HEAD_REF"; then
    _error "head ref '${HEAD_REF}' not found"
    exit 1
  fi

  while IFS= read -r merge; do
    [[ -z "$merge" ]] && continue
    second="$(git rev-parse "${merge}^2")"
    git merge-base --is-ancestor "$second" "$base_ref" && continue
    while IFS= read -r tip; do
      [[ -z "$tip" || "$tip" == "$base_ref" ]] && continue
      if git merge-base --is-ancestor "$second" "$tip"; then
        refuse "merge commit $(git rev-parse --short "$merge") brings ${tip} into a PR for '${BASE}'; rebase onto ${base_ref} and cherry-pick instead"
      fi
    done < <(long_lived_tips)
  done < <(git rev-list --merges "${base_ref}..${HEAD_REF}")

  echo "result=ok"
}

# is_fix — the title, a commit subject in origin/<base>..HEAD, or a
# type:bug label marks the change as a fix.
is_fix() {
  local label
  [[ "$TITLE" =~ $BP_FIX_TITLE_RE ]] && return 0
  while IFS= read -r label; do
    [[ "$(rs_trim "$label")" == "type:bug" ]] && return 0
  done < <(tr ',' '\n' <<<"$LABELS")
  if ref_exists "origin/${BASE}" && ref_exists "$HEAD_REF"; then
    git log --format=%s "origin/${BASE}..${HEAD_REF}" | grep -E "$BP_FIX_TITLE_RE" >/dev/null && return 0
  fi
  return 1
}

no_backport() {
  echo "backport=0"
  echo "reason=$1"
}

cmd_backport_label() {
  if [[ "$STATE_PRESENT" != "1" ]]; then
    no_backport "no RELEASING.md"
    return 0
  fi
  if [[ "$STATE_MODEL" != "release-branches" ]]; then
    no_backport "model is ${STATE_MODEL}"
    return 0
  fi
  if [[ -z "$STATE_MAINTENANCE" ]]; then
    no_backport "no maintenance branches"
    return 0
  fi
  resolve_base
  if [[ "$BASE" != "$STATE_TRUNK" ]]; then
    no_backport "PR targets '${BASE}', not the trunk '${STATE_TRUNK}'"
    return 0
  fi
  if ! is_fix; then
    no_backport "not a fix"
    return 0
  fi
  RS_BACKPORT_LABEL="$STATE_LABEL_TEMPLATE"
  export RS_BACKPORT_LABEL
  echo "backport=1"
  echo "branch=${STATE_LATEST}"
  echo "label=$(rs_label_for "$STATE_LATEST")"
}

load_state

case "$SUBCOMMAND" in
  base) cmd_base ;;
  sync-check) cmd_sync_check ;;
  backport-label) cmd_backport_label ;;
esac
