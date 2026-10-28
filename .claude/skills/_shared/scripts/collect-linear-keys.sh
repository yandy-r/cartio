#!/usr/bin/env bash
# Collect candidate Linear keys only; callers verify them before PR linking.
set -euo pipefail
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd -P)"
base='' team='' team_set=0 start_dir=''
usage() { printf '%s\n' 'Usage: collect-linear-keys.sh [--base <ref>] [--team <TEAM>] [start-dir]' >&2; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base|--team)
      if [[ $# -lt 2 || "$2" == --* ]]; then usage; exit 1; fi
      if [[ "$1" == --base ]]; then
        [[ -n "$2" ]] || { usage; exit 1; }
        base=$2
      else
        team=$2; team_set=1
      fi
      shift
      ;;
    --*) usage; exit 1 ;;
    *)
      [[ -z "$start_dir" && -n "$1" ]] || { usage; exit 1; }
      start_dir=$1
      ;;
  esac
  shift
done
start_dir=${start_dir:-$PWD}
cd -- "$start_dir" 2>/dev/null || { usage; exit 1; }
# shellcheck source=resolve-artifact-store.sh
source "${SCRIPT_DIR}/resolve-artifact-store.sh"
if [[ "$team_set" == 0 ]]; then team=${ARTIFACT_LINEAR_TEAM:-}; fi
team=$(printf '%s' "$team" | tr '[:lower:]' '[:upper:]')
[[ -z "$team" || "$team" =~ ^[A-Z0-9]+$ ]] || { usage; exit 1; }
# Apply an explicit team override to the branch matcher as well as commit keys.
if [[ "$team_set" == 1 ]]; then ARTIFACT_LINEAR_HINT=$(_artifact_branch_hint "$PWD" "$team"); fi
# Buffer candidates so a later closing keyword upgrades an earlier Refs row
# without changing its first-seen position or source.
{
  if [[ -n "${ARTIFACT_LINEAR_HINT:-}" ]]; then
    printf '%s\tbranch\tcloses\n' "$ARTIFACT_LINEAR_HINT"
  fi
  if [[ -n "$base" ]] && git show-ref --verify --quiet "refs/remotes/origin/$base"; then
    base="refs/remotes/origin/$base"
  fi
  if [[ -z "$base" ]]; then
    for candidate in origin/HEAD origin/main main; do
      if base=$(git merge-base HEAD "$candidate" 2>/dev/null); then break; fi
      base=''
    done
  fi
  if [[ -n "$base" ]] && git rev-parse --verify "${base}^{commit}" >/dev/null 2>&1; then
    git log --format=%B "${base}..HEAD" -- 2>/dev/null | awk '
      {
        text = $0
        while (match(tolower(text), /(^|[^a-z0-9_])(closes|fixes|resolves|refs)[:[:space:]]+[a-z][a-z0-9]+-[0-9]+/)) {
          end = RSTART + RLENGTH
          matched = substr(text, RSTART, RLENGTH)
          key = matched
          sub(/^.*[:[:space:]]/, "", key)
          prefix = key
          sub(/-[0-9]+$/, "", prefix)
          action = (tolower(matched) ~ /(^|[^a-z0-9_])refs[:[:space:]]/) ? "refs" : "closes"
          if (length(prefix) <= 10 && substr(text, end, 1) !~ /[A-Za-z0-9]/)
            print toupper(key) "\tcommit\t" action
          text = substr(text, end)
        }
      }
    '
  else
    printf '%s\n' 'Warning: skipping Linear commit scan: no resolvable base.' >&2
  fi
} | awk -F '\t' -v team="$team" '
  team != "" && index($1, team "-") != 1 { next }
  !seen[$1]++ { keys[++count] = $1; source[$1] = $2; action[$1] = $3 }
  $3 == "closes" { action[$1] = "closes" }
  END { for (i = 1; i <= count; i++) print keys[i] "\t" source[keys[i]] "\t" action[keys[i]] }
'
exit 0
