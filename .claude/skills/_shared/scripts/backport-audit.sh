#!/usr/bin/env bash
# backport-audit.sh — before a patch vX.Y.Z is tagged, confirm that every fix
# meant for release/X.Y actually reached it.
#
# Usage:
#   backport-audit.sh <release/X.Y | X.Y> [--repo DIR] [--since REF]
#
# Two checks against origin (GitHub only):
#   labelled    Every PR merged into the trunk with the line's backport label
#               (backport_label with {X.Y} filled in, e.g. backport:0.5) has
#               a MERGED twin: a PR into release/X.Y whose body says
#               "Backport of #<N>", or a commit on origin/release/X.Y whose
#               message says "cherry picked from commit <merge sha>".
#               Label age does not matter; every labelled PR is checked.
#   unlabelled  Every fix PR (title fix:/fix(scope):/fix!:, or a type:bug
#               label) merged into the trunk since REF without the line's
#               label and without a twin is listed for a decision: label and
#               backport it, or confirm it only fixes trunk code.
#               Default REF: the newest tag reachable from origin/release/X.Y,
#               the line's previous release, so each fix is reviewed at the
#               first patch after it merged. Pass --since vX.Y.0 to sweep the
#               whole line (the first audit of a line that shipped without it).
#
# Output (stdout), one line per PR, blocking rows first, then by PR number:
#   <status>\t<pr>\t<merge_sha>\t<title>\t<detail>
#   missing     labelled, no merged twin ("no backport PR", or
#               "backport #M closed unmerged")
#   in-review   labelled (or a fix), twin PR open but not merged ("#M")
#   unlabelled  fix merged since REF with no label and no twin (names any
#               other line's backport label the PR carries)
#   ok          backported ("#M", or "cherry-pick <sha7>")
# A last line "since=<REF>" names the window used by the unlabelled check.
#
# Exit codes:
#   0  every labelled PR is backported and no fix is unaccounted for
#   1  usage error, missing/unauthenticated gh, missing jq, no RELEASING.md,
#      model is not release-branches, release/X.Y missing on origin, a
#      gh/git query failed, or a query hit the result limit
#   2  RELEASING.md exists but its state block is malformed
#   3  findings: at least one missing, in-review or unlabelled row
#
# Rules: .claude/skills/_shared/references/branching-model.md#backporting

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_SCRIPT="${SCRIPT_DIR}/release-state.sh"
# shellcheck source=lib/backport-lib.sh
source "${SCRIPT_DIR}/lib/backport-lib.sh"
PR_LIMIT=1000
NAME="backport-audit"
# Single-quoted: the Codex generator rewrites this to its own skill syntax, which
# must stay literal rather than expand as a shell variable.
RELEASE_MODEL_CMD='/release-model'

_error() {
  echo "${NAME}: $*" >&2
}

usage() {
  sed -n '2,42p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
}

REPO_DIR="."
SINCE=""
LINE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -lt 2 ]] && { _error "--repo requires a directory"; exit 1; }
      REPO_DIR="$2"
      shift 2
      ;;
    --since)
      [[ $# -lt 2 || -z "${2:-}" ]] && { _error "--since requires a ref"; exit 1; }
      SINCE="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      _error "unknown option: $1"
      usage
      exit 1
      ;;
    *)
      [[ -n "$LINE" ]] && { _error "unexpected argument: $1"; exit 1; }
      LINE="${1#release/}"
      shift
      ;;
  esac
done

if [[ ! "$LINE" =~ ^[0-9]+\.[0-9]+$ ]]; then
  _error "a line is required: release/X.Y or X.Y"
  usage
  exit 1
fi
TARGET="release/${LINE}"

for tool in gh jq git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    _error "'${tool}' is required but not installed"
    exit 1
  fi
done
if [[ ! -d "$REPO_DIR" ]]; then
  _error "no such directory: $REPO_DIR"
  exit 1
fi

# ---------------------------------------------------------------------------
# Release state
# ---------------------------------------------------------------------------

rc=0
STATE_OUT="$(bash "$STATE_SCRIPT" --repo "$REPO_DIR" get)" || rc=$?
if [[ "$rc" -eq 2 ]]; then
  _error "RELEASING.md has a malformed state block (see above); run ${RELEASE_MODEL_CMD} --audit"
  exit 2
elif [[ "$rc" -ne 0 ]]; then
  _error "could not read the release state"
  exit 1
fi

state_key() {
  printf '%s\n' "$STATE_OUT" | sed -n "s/^$1=//p"
}

if [[ "$(state_key present)" != "1" ]]; then
  _error "no RELEASING.md in ${REPO_DIR}; the audit needs a release-branches model (run ${RELEASE_MODEL_CMD})"
  exit 1
fi
if [[ "$(state_key model)" != "release-branches" ]]; then
  _error "model is '$(state_key model)'; only release-branches has backports to audit"
  exit 1
fi

TRUNK="$(state_key trunk)"
LABEL_TEMPLATE="$(state_key backport_label)"
LABEL="$(bp_label_for_line "$LABEL_TEMPLATE" "$LINE")"
LABEL_PREFIX="$(bp_label_prefix "$LABEL_TEMPLATE")"

# ---------------------------------------------------------------------------
# Git: the release branch and the unlabelled-check window
# ---------------------------------------------------------------------------

cd "$REPO_DIR"

if ! git fetch -q origin "$TARGET" 2>/dev/null ||
  ! git rev-parse -q --verify "refs/remotes/origin/${TARGET}" >/dev/null; then
  _error "${TARGET} does not exist on origin (or fetch failed); nothing to audit"
  exit 1
fi

if [[ -z "$SINCE" ]]; then
  if ! SINCE="$(git describe --tags --abbrev=0 "origin/${TARGET}" 2>/dev/null)"; then
    _error "no tag is reachable from origin/${TARGET}; pass --since <ref>"
    exit 1
  fi
fi

# The window starts when REF was created: the tagger date for an annotated
# tag, else the commit date. UTC, because GitHub search reads the offset.
SINCE_DATE="$(TZ=UTC git for-each-ref --format='%(creatordate:iso-strict-local)' "refs/tags/${SINCE}")"
if [[ -z "$SINCE_DATE" ]] &&
  ! SINCE_DATE="$(TZ=UTC git log -1 --date=iso-strict-local --format=%cd "${SINCE}^{commit}" 2>/dev/null)"; then
  _error "cannot resolve --since '${SINCE}'"
  exit 1
fi

# has_cherry_pick_trailer <sha> — true when a commit on origin/release/X.Y
# records "cherry picked from commit <sha>" (git cherry-pick -x).
has_cherry_pick_trailer() {
  [[ -n "$1" ]] &&
    [[ -n "$(git log --format=%H -F --grep "cherry picked from commit $1" "origin/${TARGET}" -n 1)" ]]
}

# ---------------------------------------------------------------------------
# GitHub queries
# ---------------------------------------------------------------------------

if ! gh auth status >/dev/null 2>&1; then
  _error "gh is not authenticated; run 'gh auth login'"
  exit 1
fi

# require_below_limit <json> <description> — gh pr list truncates silently at
# --limit, so a full page may hide PRs; fail rather than report a false clean.
require_below_limit() {
  local count
  count="$(jq length <<<"$1")"
  if [[ "$count" -ge "$PR_LIMIT" ]]; then
    _error "${PR_LIMIT} or more PRs returned by $2; results would be truncated (narrow the window with --since, or raise PR_LIMIT)"
    exit 1
  fi
}

TWINS_JSON="$(bp_gh_json "$NAME" pr list --state all --base "$TARGET" --limit "$PR_LIMIT" --json number,state,body)"
require_below_limit "$TWINS_JSON" "the ${TARGET} PR query"
LABELLED_JSON="$(bp_gh_json "$NAME" pr list --state merged --base "$TRUNK" --label "$LABEL" \
  --limit "$PR_LIMIT" --json number,title,mergeCommit)"
require_below_limit "$LABELLED_JSON" "the ${LABEL} labelled PR query"
RECENT_JSON="$(bp_gh_json "$NAME" pr list --state merged --base "$TRUNK" --search "merged:>=${SINCE_DATE}" \
  --limit "$PR_LIMIT" --json number,title,labels,mergeCommit)"
require_below_limit "$RECENT_JSON" "the merged-since-${SINCE} PR query"

ROWS_MISSING=""
ROWS_REVIEW=""
ROWS_UNLABELLED=""
ROWS_OK=""

# add_row <status> <pr> <sha> <title> <detail>
add_row() {
  local line
  line="$1"$'\t'"$2"$'\t'"$3"$'\t'"$4"$'\t'"$5"$'\n'
  case "$1" in
    missing) ROWS_MISSING+="$line" ;;
    in-review) ROWS_REVIEW+="$line" ;;
    unlabelled) ROWS_UNLABELLED+="$line" ;;
    ok) ROWS_OK+="$line" ;;
  esac
}

# classify <pr> <sha> <title> <status-when-no-twin> <detail-when-no-twin>
# Merged twin or cherry-pick trailer -> ok; open twin -> in-review; a closed
# twin is named in the detail; otherwise the given fallback status.
classify() {
  local pr="$1" sha="$2" title="$3" fallback="$4" detail="$5" twin state number
  twin="$(bp_best_twin "$TWINS_JSON" "$pr")"
  state="${twin%%$'\t'*}"
  number="${twin#*$'\t'}"
  if [[ "$state" == "MERGED" ]]; then
    add_row ok "$pr" "$sha" "$title" "#${number}"
  elif has_cherry_pick_trailer "$sha"; then
    add_row ok "$pr" "$sha" "$title" "cherry-pick ${sha:0:7}"
  elif [[ "$state" == "OPEN" ]]; then
    add_row in-review "$pr" "$sha" "$title" "#${number}"
  elif [[ "$state" == "CLOSED" ]]; then
    add_row "$fallback" "$pr" "$sha" "$title" "backport #${number} closed unmerged"
  else
    add_row "$fallback" "$pr" "$sha" "$title" "$detail"
  fi
}

# Rows are joined with the unit separator (\x1f), not @tsv: tab is IFS
# whitespace, so an empty field (a missing merge sha) would collapse and shift
# the rest. Titles have \x1f stripped so they cannot split a row.
JQ_ROW='(.number | tostring), (.mergeCommit.oid // ""), (.title | gsub("[\t\n\u001f]"; " "))'

while IFS=$'\x1f' read -r number sha title; do
  [[ -z "$number" ]] && continue
  classify "$number" "$sha" "$title" missing "no backport PR"
done < <(jq -r ".[] | [${JQ_ROW}] | join(\"\u001f\")" <<<"$LABELLED_JSON")

while IFS=$'\x1f' read -r number sha title labels; do
  [[ -z "$number" ]] && continue
  labels="${labels:-}"
  [[ ",${labels}," == *",${LABEL},"* ]] && continue
  is_fix=0
  [[ "$title" =~ $BP_FIX_TITLE_RE ]] && is_fix=1
  [[ ",${labels}," == *",type:bug,"* ]] && is_fix=1
  [[ "$is_fix" -eq 1 ]] || continue
  other=""
  if [[ -n "$LABEL_PREFIX" ]]; then
    other="$(tr ',' '\n' <<<"$labels" | awk -v p="$LABEL_PREFIX" 'index($0, p) == 1' | paste -sd, -)"
  fi
  classify "$number" "$sha" "$title" unlabelled \
    "no ${LABEL} label${other:+ (has ${other})}"
done < <(jq -r ".[] | [${JQ_ROW}, ([.labels[]?.name] | join(\",\"))] | join(\"\u001f\")" <<<"$RECENT_JSON")

for rows in "$ROWS_MISSING" "$ROWS_REVIEW" "$ROWS_UNLABELLED" "$ROWS_OK"; do
  [[ -n "$rows" ]] && printf '%s' "$rows" | sort -t $'\t' -k2,2n
done
echo "since=${SINCE}"

if [[ -n "${ROWS_MISSING}${ROWS_REVIEW}${ROWS_UNLABELLED}" ]]; then
  exit 3
fi
exit 0
