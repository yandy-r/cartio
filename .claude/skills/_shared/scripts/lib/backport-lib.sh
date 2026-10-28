#!/usr/bin/env bash
# backport-lib.sh — helpers shared by the backport scripts: find-pending.sh
# (backport) and backport-audit.sh (backport --audit, releaser's
# patch gate). Sourced, not executed.
#
# Rules: .claude/skills/_shared/references/branching-model.md#backporting

# A conventional-commit fix title: "fix:", "fix(scope):", "fix!:".
# shellcheck disable=SC2034  # used by the scripts that source this file
BP_FIX_TITLE_RE='^fix(\([^)]*\))?!?:'

# bp_label_for_line <backport_label template> <X.Y> — the label for one line,
# e.g. "backport:{X.Y}" + "0.5" -> "backport:0.5".
bp_label_for_line() {
  printf '%s' "${1//\{X.Y\}/$2}"
}

# bp_label_prefix <backport_label template> — the part before "{X.Y}".
bp_label_prefix() {
  printf '%s' "${1%%\{X.Y\}*}"
}

# bp_gh_json <caller> <gh args...> — run gh and print its stdout. On failure
# print "<caller>: gh <args> failed: <output>" on stderr and exit 1.
bp_gh_json() {
  local caller="$1" out
  shift
  if ! out="$(gh "$@" 2>&1)"; then
    echo "${caller}: gh $* failed: ${out}" >&2
    exit 1
  fi
  printf '%s\n' "$out"
}

# bp_best_twin <prs-json> <pr> — among PRs into a maintenance branch
# (a JSON array of {number, state, body}), find the ones whose body says
# "Backport of #<pr>" (not followed by another digit) and print the best as
# "<STATE>\t<number>": MERGED first, then OPEN, then CLOSED. Prints nothing
# when there is none.
bp_best_twin() {
  jq -r --arg n "$2" '
    [ .[] | select((.body // "") | test("Backport of #" + $n + "(?![0-9])")) ]
    | sort_by({"MERGED": 0, "OPEN": 1}[.state // ""] // 2, .number)
    | first // empty
    | "\(.state)\t\(.number)"
  ' <<<"$1"
}
