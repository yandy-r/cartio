#!/usr/bin/env bash
# Write and optionally commit the portable repository artifact-store pointer.
# workspace/label/project are read indirectly via ${!name} in the validation and merge loops.
# shellcheck disable=SC2034
set -euo pipefail
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd -P)"
usage() {
  printf '%s\n' 'Usage: artifact-store-pointer.sh --store=linear|local [--linear-workspace=<slug>] [--linear-team=<KEY>] [--linear-label=<label> | --linear-project=<id-or-slug>] [--dry-run] [--no-commit] [--force] [<dir>]'
}
die() { printf 'ERROR: %s\n' "$2" >&2; exit "$1"; }
store='' workspace='' team='' label='' project='' dir=''
workspace_set=0 team_set=0 label_set=0 project_set=0 dry_run=0 no_commit=0 force=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --store=*) store=${1#*=} ;;
    --linear-workspace=*) workspace=${1#*=}; workspace_set=1 ;;
    --linear-team=*) team=${1#*=}; team_set=1 ;;
    --linear-label=*) label=${1#*=}; label_set=1 ;;
    --linear-project=*) project=${1#*=}; project_set=1 ;;
    --dry-run) dry_run=1 ;;
    --no-commit) no_commit=1 ;;
    --force) force=1 ;;
    -*) die 2 "Unknown option: $1" ;;
    *) [[ -z "$dir" && -n "$1" ]] || die 2 'Expected one directory'; dir=$1 ;;
  esac
  shift
done
[[ "$store" == linear || "$store" == local ]] || die 2 '--store must be linear or local'
value_pattern='^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$'
for name in workspace label project; do
  set_name="${name}_set"
  if [[ "${!set_name}" == 1 ]]; then
    [[ "${!name}" =~ $value_pattern ]] || die 2 "Invalid --linear-${name} value"
  fi
done
if [[ "$team_set" == 1 ]]; then
  [[ "$team" =~ ^[A-Z][A-Z0-9]{0,9}$ ]] || die 2 'Invalid --linear-team value'
fi
[[ "$label_set$project_set" != 11 ]] || die 2 '--linear-label and --linear-project are mutually exclusive'
if [[ "$store" == linear ]]; then
  [[ "$workspace_set$team_set" == 11 && "$label_set$project_set" != 00 ]] || die 2 'Linear store requires --linear-workspace, --linear-team, and --linear-label or --linear-project'
fi
dir=${dir:-.}
[[ "$(git -C "$dir" rev-parse --is-inside-work-tree 2>/dev/null || :)" == true ]] || die 2 'Artifact-store pointer requires a Git work tree'
root=$(git -C "$dir" rev-parse --show-toplevel)
root=$(CDPATH='' cd -- "$root" >/dev/null && pwd -P)
pointer="${root}/.plans-config"
for path in .plans-config .gitignore .ycc; do
  [[ ! -L "${root}/${path}" ]] || die 2 "Refusing symlinked ${path}"
done
[[ ! -e "$pointer" || -f "$pointer" ]] || die 2 '.plans-config must be a regular file'
[[ ! -e "${root}/.gitignore" || -f "${root}/.gitignore" ]] || die 2 '.gitignore must be a regular file'
if [[ -f "$pointer" ]] && grep -q $'\r' "$pointer"; then
  die 2 'CRLF .plans-config; convert .plans-config to LF line endings and retry'
fi
# Source in the target repository so auto-resolution never consults the caller.
start_pwd=$PWD
CDPATH='' cd -- "$root"
# shellcheck source=resolve-artifact-store.sh
source "${SCRIPT_DIR}/resolve-artifact-store.sh"
CDPATH='' cd -- "$start_pwd"
set -euo pipefail
keys=(store) values=("$store") seen=(0) old_values=()
for name in workspace team label project; do
  set_name="${name}_set"
  if [[ "${!set_name}" == 1 ]]; then
    keys+=("linear_${name}"); values+=("${!name}"); seen+=(0)
  fi
done
for ((i=0; i<${#keys[@]}; i++)); do
  if old=$(_artifact_config_value "${keys[i]}" "$pointer"); then
    [[ "$old" == "${values[i]}" || "$force" == 1 ]] || die 3 "Conflicting ${keys[i]}: ${old} -> ${values[i]} (use --force)"
    old_values+=("$old")
  else
    old_values+=('')
  fi
done
# A label <-> project mapping switch must be explicit: the other key's existing
# line conflicts, and --force removes it instead of leaving both mappings.
remove_keys=()
other_key=''
if [[ "$label_set$project_set" == 10 ]]; then
  other_key=linear_project
elif [[ "$label_set$project_set" == 01 ]]; then
  other_key=linear_label
fi
if [[ -n "$other_key" && -f "$pointer" ]]; then
  if old=$(_artifact_config_value "$other_key" "$pointer"); then
    if [[ "$force" == 1 ]]; then
      remove_keys=("$other_key")
    elif [[ "$other_key" == linear_project ]]; then
      die 3 'Conflicting linear_project mapping; use --force to switch to --linear-label'
    else
      die 3 'Conflicting linear_label mapping; use --force to switch to --linear-project'
    fi
  fi
fi
# Creating a root pointer would make nested, non-local configs inherit its
# plans_dir. Refuse before changing that resolution unless explicitly forced.
if [[ ! -e "$pointer" && "$force" == 0 ]]; then
  nested_configs=()
  while IFS= read -r -d '' nested_config; do
    scope=$(_artifact_config_value scope "${root}/${nested_config}") || scope=''
    [[ "$scope" == local ]] || nested_configs+=("$nested_config")
  done < <({
    git -C "$root" ls-files -z --cached -- '*/.plans-config'
    git -C "$root" ls-files -z --others --exclude-standard -- '*/.plans-config'
  } | sort -zu)
  if [[ ${#nested_configs[@]} -gt 0 ]]; then
    printf 'ERROR: Creating a root .plans-config would change plan resolution for nested configs without scope: local:\n' >&2
    printf '  %s\n' "${nested_configs[@]}" >&2
    die 3 'Add scope: local to those configs or rerun with --force.'
  fi
fi
# Every refusal runs before any write, for both stores.
if git -C "$root" check-ignore -q --no-index -- .plans-config; then
  die 4 '.plans-config is ignored; a committed artifact-store pointer cannot be ignored. Remove the ignore rule and retry.'
fi
ignore=skipped
# Dry-run checks coverage without invoking the resolver's write entry point.
# The actual write always uses the canonical helper, including wider repo rules.
if [[ "$store" == linear ]]; then
  export ARTIFACT_STORE=linear ARTIFACT_ROOT="$root" ARTIFACT_DIR="${root}/.ycc/artifacts"
  _artifact_require_git_root || die 2 'Cannot ensure repository artifact-cache ignore rule'
  if [[ -e "${root}/.gitignore" ]]; then
    [[ -w "${root}/.gitignore" ]] || die 2 '.gitignore is not writable'
  else
    [[ -w "$root" ]] || die 2 'Repository root is not writable; cannot create .gitignore'
  fi
  coverage=0
  _artifact_gitignore_present || coverage=$?
  case "$coverage" in
    0) ignore=present ;;
    1) ignore=added ;;
    *) die 2 'Cannot ensure repository artifact-cache ignore rule' ;;
  esac
fi
# Pre-existing uncommitted .plans-config edits (unstaged or staged-only) keep
# the user's commit boundary: the pointer is written but never committed.
pointer_dirty=0
if [[ -f "$pointer" ]]; then
  if git -C "$root" rev-parse -q --verify HEAD >/dev/null 2>&1; then
    if ! git -C "$root" diff --quiet HEAD -- .plans-config || ! git -C "$root" diff --cached --quiet -- .plans-config; then
      pointer_dirty=1
    fi
  fi
  if [[ "$pointer_dirty" == 0 ]] && ! git --literal-pathspecs -C "$root" ls-files --error-unmatch -- .plans-config >/dev/null 2>&1; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
      [[ "$line" =~ ^(store|linear_[A-Za-z0-9_]*)[[:space:]]*[:=] ]] || pointer_dirty=1
    done <"$pointer"
  fi
fi
planned=''
if [[ -f "$pointer" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    drop=0
    if [[ ${#remove_keys[@]} -gt 0 ]]; then
      for remove_key in "${remove_keys[@]}"; do
        if [[ "$line" =~ ^${remove_key}[[:space:]]*[:=] ]]; then drop=1; fi
      done
    fi
    if [[ "$drop" == 0 ]]; then
      for ((i=0; i<${#keys[@]}; i++)); do
        if [[ "$line" =~ ^${keys[i]}[[:space:]]*[:=] ]]; then
          seen[i]=1
          old=${old_values[i]}
          if [[ "$old" != "${values[i]}" ]]; then
            comment=''
            comment_pattern='([[:space:]]*#.*)$'
            if [[ "$line" =~ $comment_pattern ]]; then comment=${BASH_REMATCH[1]}; fi
            line="${keys[i]}: ${values[i]}${comment}"
          fi
          break
        fi
      done
      planned+="${line}"$'\n'
    fi
  done <"$pointer"
fi
for ((i=0; i<${#keys[@]}; i++)); do
  if [[ "${seen[i]}" == 0 ]]; then planned+="${keys[i]}: ${values[i]}"$'\n'; fi
done
change=created
if [[ -f "$pointer" ]]; then
  change=updated
  same=1
  for ((i=0; i<${#keys[@]}; i++)); do
    [[ "${seen[i]}" == 1 && "${old_values[i]}" == "${values[i]}" ]] || same=0
  done
  if [[ "$same" == 1 && ${#remove_keys[@]} -eq 0 ]]; then change=unchanged; fi
fi
commit=none
if [[ "$dry_run" == 1 ]]; then
  printf '%s\n' '--- .plans-config (planned) ---'
  printf '%s' "$planned"
  printf '%s\n' '--- end ---' "Plan: ${change} .plans-config; gitignore=${ignore}; commit only eligible pointer paths."
  commit=skipped
else
  if [[ "$store" == linear ]]; then
    before_ignore=$(if [[ -f "${root}/.gitignore" ]]; then cat "${root}/.gitignore"; fi; printf '\001')
    ensure_artifact_gitignore || die 2 'Cannot ensure repository artifact-cache ignore rule'
    after_ignore=$(cat "${root}/.gitignore" 2>/dev/null || :; printf '\001')
    if [[ "$before_ignore" != "$after_ignore" ]]; then ignore=added; else ignore=present; fi
  fi
  if [[ "$change" != unchanged ]]; then printf '%s' "$planned" >"$pointer"; fi
  paths=()
  if ! git -C "$root" ls-files --error-unmatch -- .plans-config >/dev/null 2>&1 || ! git -C "$root" diff --quiet HEAD -- .plans-config; then paths+=(.plans-config); fi
  if [[ "$store" == linear && -f "${root}/.gitignore" ]]; then
    base=$(git -C "$root" show HEAD:.gitignore 2>/dev/null && printf '\001') || base=$'\001'
    base=${base%$'\001'}
    expected=$base
    if [[ -n "$expected" && "$expected" != *$'\n' ]]; then expected+=$'\n'; fi
    expected+=$'.ycc/artifacts/\n'
    if ! git -C "$root" ls-files --error-unmatch -- .gitignore >/dev/null 2>&1 || ! git -C "$root" diff --quiet HEAD -- .gitignore; then
      if cmp -s "${root}/.gitignore" <(printf '%s' "$expected"); then
        paths+=(.gitignore)
      else
        printf 'WARNING: .gitignore has unrelated changes; committing only .plans-config.\n' >&2
      fi
    fi
  fi
  if [[ "$no_commit" == 1 ]]; then
    commit=skipped
  elif [[ "$pointer_dirty" == 1 ]]; then
    printf 'WARNING: .plans-config has pre-existing uncommitted changes; leaving the pointer commit to you (commit=skipped).\n' >&2
    commit=skipped
  elif [[ ${#paths[@]} -gt 0 ]]; then
    if ! git -C "$root" add -- "${paths[@]}" || ! git -C "$root" commit -m "chore(ycc): configure ${store/linear/Linear} artifact store" -- "${paths[@]}"; then
      if git -C "$root" rev-parse -q --verify HEAD >/dev/null 2>&1; then
        git -C "$root" reset -q -- "${paths[@]}" || :
      else
        git -C "$root" rm --cached -q --ignore-unmatch -- "${paths[@]}" || :
      fi
      die 5 'Artifact-store pointer commit failed; files remain written and staged pointer paths were unstaged (Git error above).'
    fi
    commit=$(git -C "$root" rev-parse --short HEAD)
  elif [[ "$store" == local ]]; then
    commit=skipped
  fi
fi
printf 'pointer=%s\npointer_change=%s\ngitignore=%s\ncommit=%s\n' "$pointer" "$change" "$ignore" "$commit"
