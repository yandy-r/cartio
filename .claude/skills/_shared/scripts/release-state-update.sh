#!/usr/bin/env bash
# release-state-update.sh — change the ycc release state in RELEASING.md and
# re-render its Current state table. Never commits.
#
# Usage:
#   release-state-update.sh [--repo DIR] [--dry-run] <op>
#   release-state-update.sh --print-block --model M --trunk B
#                           [--support S] [--tracker T] [--tracker-ref R]
#
# Ops (exactly one):
#   --set-model M             trunk-only | release-branches
#   --set-trunk B             rename the trunk
#   --add-maintenance BR      add release/X.Y as the newest maintenance branch;
#                             branches beyond the support window move to frozen
#   --freeze BR               move release/X.Y from maintenance to frozen
#   --render                  re-render the table from the block only
#   --print-block             print a new block (and table) for the given
#                             values without reading or writing any file
#
# Options:
#   --repo DIR   repository root to operate on (default: current directory)
#   --dry-run    print a unified diff instead of writing
#
# Exit codes:
#   0  success
#   1  usage error, missing RELEASING.md, or an op the state does not allow
#   2  the existing state block is malformed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/release-state-lib.sh
source "${SCRIPT_DIR}/lib/release-state-lib.sh"

usage() {
  sed -n '2,29p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
}

_error() {
  echo "release-state-update: $*" >&2
}

REPO_DIR="."
DRY_RUN=false
OP=""
OP_ARG=""
NEW_MODEL=""
NEW_TRUNK=""
NEW_SUPPORT=""
NEW_TRACKER=""
NEW_TRACKER_REF=""

set_op() {
  if [[ -n "$OP" ]]; then
    _error "only one op is allowed (got $OP and $1)"
    exit 1
  fi
  OP="$1"
}

need_arg() {
  if [[ $# -lt 2 || -z "$2" ]]; then
    _error "$1 requires a value"
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) need_arg "$@"; REPO_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --set-model) need_arg "$@"; set_op set-model; OP_ARG="$2"; shift 2 ;;
    --set-trunk) need_arg "$@"; set_op set-trunk; OP_ARG="$2"; shift 2 ;;
    --add-maintenance) need_arg "$@"; set_op add-maintenance; OP_ARG="$2"; shift 2 ;;
    --freeze) need_arg "$@"; set_op freeze; OP_ARG="$2"; shift 2 ;;
    --render) set_op render; shift ;;
    --print-block) set_op print-block; shift ;;
    --model) need_arg "$@"; NEW_MODEL="$2"; shift 2 ;;
    --trunk) need_arg "$@"; NEW_TRUNK="$2"; shift 2 ;;
    --support) need_arg "$@"; NEW_SUPPORT="$2"; shift 2 ;;
    --tracker) need_arg "$@"; NEW_TRACKER="$2"; shift 2 ;;
    --tracker-ref) need_arg "$@"; NEW_TRACKER_REF="$2"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) _error "unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$OP" ]]; then
  _error "no op given"
  usage
  exit 1
fi

# window_size — how many maintenance branches the support window keeps, or 0
# for "no automatic freezing" (free-text support policies).
window_size() {
  case "$RS_SUPPORT" in
    latest-minor) echo 1 ;;
    latest-two-minors) echo 2 ;;
    *) echo 0 ;;
  esac
}

# list_remove <list> <item> — print the comma list without item.
list_remove() {
  local list="$1" drop="$2" item out=""
  local IFS=','
  for item in $list; do
    [[ "$item" == "$drop" ]] && continue
    out="${out:+$out,}$item"
  done
  printf '%s' "$out"
}

# list_contains <list> <item>
list_contains() {
  [[ ",$1," == *",$2,"* ]]
}

op_print_block() {
  rs_reset
  RS_MODEL="$NEW_MODEL"
  RS_TRUNK="$NEW_TRUNK"
  [[ -n "$NEW_SUPPORT" ]] && RS_SUPPORT="$NEW_SUPPORT"
  [[ -n "$NEW_TRACKER" ]] && RS_TRACKER="$NEW_TRACKER"
  RS_TRACKER_REF="$NEW_TRACKER_REF"
  export RS_TRACKER RS_TRACKER_REF
  if ! rs_validate; then
    _error "$RS_ERROR"
    exit 1
  fi
  rs_format_block
  echo
  rs_render_table
}

if [[ "$OP" == "print-block" ]]; then
  op_print_block
  exit 0
fi

ROOT="$(rs_repo_root "$REPO_DIR")"
FILE="${ROOT}/RELEASING.md"
if [[ ! -f "$FILE" ]]; then
  _error "no RELEASING.md at ${ROOT} (run ${RS_HINT_CREATE})"
  exit 1
fi
if ! rs_parse_file "$FILE"; then
  _error "${RS_ERROR} (run ${RS_HINT_AUDIT})"
  exit 2
fi

case "$OP" in
  set-model)
    RS_MODEL="$OP_ARG"
    ;;
  set-trunk)
    RS_TRUNK="$OP_ARG"
    export RS_TRUNK
    ;;
  add-maintenance)
    if [[ "$RS_MODEL" != "release-branches" ]]; then
      _error "model is $RS_MODEL; run --set-model release-branches first"
      exit 1
    fi
    if [[ ! "$OP_ARG" =~ $RS_BRANCH_RE ]]; then
      _error "'$OP_ARG' is not of the form release/X.Y"
      exit 1
    fi
    RS_MAINTENANCE="$(list_remove "$RS_MAINTENANCE" "$OP_ARG")"
    RS_FROZEN="$(list_remove "$RS_FROZEN" "$OP_ARG")"
    RS_MAINTENANCE="${OP_ARG}${RS_MAINTENANCE:+,$RS_MAINTENANCE}"
    keep="$(window_size)"
    if [[ "$keep" -gt 0 ]]; then
      kept=""
      moved=""
      count=0
      IFS=',' read -r -a entries <<<"$RS_MAINTENANCE"
      for item in "${entries[@]}"; do
        count=$((count + 1))
        if [[ "$count" -le "$keep" ]]; then
          kept="${kept:+$kept,}$item"
        else
          moved="${moved:+$moved,}$item"
        fi
      done
      RS_MAINTENANCE="$kept"
      [[ -n "$moved" ]] && RS_FROZEN="${moved}${RS_FROZEN:+,$RS_FROZEN}"
    fi
    ;;
  freeze)
    if ! list_contains "$RS_MAINTENANCE" "$OP_ARG"; then
      _error "'$OP_ARG' is not a maintenance branch"
      exit 1
    fi
    RS_MAINTENANCE="$(list_remove "$RS_MAINTENANCE" "$OP_ARG")"
    RS_FROZEN="${OP_ARG}${RS_FROZEN:+,$RS_FROZEN}"
    ;;
  render) ;;
esac

if ! rs_validate; then
  _error "$RS_ERROR"
  exit 1
fi

TMP_OUT="$(mktemp)"
trap 'rm -f "$TMP_OUT"' EXIT
rs_write_file "$FILE" "$TMP_OUT"

if [[ "$DRY_RUN" == true ]]; then
  diff -u --label "a/RELEASING.md" --label "b/RELEASING.md" "$FILE" "$TMP_OUT" || true
  exit 0
fi

cat "$TMP_OUT" >"$FILE"
