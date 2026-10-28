#!/usr/bin/env bash
# Resolve the artifact store without writing files or contacting Linear.
# Usage: source resolve-artifact-store.sh; get_feature_artifact_dir feature-name
# See ../references/artifact-store.md for the exported API and sync protocol.
# Like resolve-plans-dir.sh, leave errexit under the sourcing caller's control.
set -uo pipefail

# shellcheck source=resolve-plans-dir.sh
source "$(CDPATH='' cd -- "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd -P)/resolve-plans-dir.sh" 2>/dev/null || {
  if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then exit 1; else return 1; fi
}

# Unlike _parse_config, a defined but empty value is still a hit. In particular,
# an empty store must fail validation rather than silently inherit a default.
_artifact_config_value() {
  local key="$1" file="$2"
  [[ -f "$file" ]] || return 1
  grep -qE "^${key}[[:space:]]*[:=]" "$file" || return 1
  _parse_config "$key" "$file" || printf '%s' ''
}

# Each key inherits independently, but never from outside this repository.
_artifact_find_config_value() {
  local key="$1" dir="$2" boundary="$3" value
  # A boundary that is not an ancestor cannot bound a walk: inspect only start.
  if [[ "$dir" != "$boundary" && "$dir" != "${boundary%/}/"* ]]; then boundary=$dir; fi
  while :; do
    if value=$(_artifact_config_value "$key" "${dir}/.plans-config"); then
      printf '%s' "$value"
      return 0
    fi
    [[ "$dir" != "$boundary" && "$dir" != / ]] || break
    dir=$(dirname "$dir")
  done
  return 1
}

_artifact_branch_hint() {
  local start_dir="$1" team="$2" branch prefix regex
  branch=$(git -C "$start_dir" branch --show-current 2>/dev/null) || return 0
  [[ -n "$branch" ]] || return 0
  branch=$(printf '%s' "$branch" | tr '[:lower:]' '[:upper:]')
  prefix='[A-Z][A-Z0-9]{1,9}'
  if [[ -n "$team" ]]; then
    team=$(printf '%s' "$team" | tr '[:lower:]' '[:upper:]')
    [[ "$team" =~ ^[A-Z0-9]+$ ]] || return 0
    prefix=$team
  fi
  regex="(^|[/_-])(${prefix})-([0-9]+)([/_-]|$)"
  if [[ "$branch" =~ $regex ]]; then
    printf '%s-%s' "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}"
  fi
  return 0
}

resolve_artifact_store() {
  unset ARTIFACT_STORE ARTIFACT_STORE_SOURCE ARTIFACT_ROOT ARTIFACT_DIR \
    ARTIFACT_LINEAR_WORKSPACE ARTIFACT_LINEAR_TEAM ARTIFACT_LINEAR_PROJECT \
    ARTIFACT_LINEAR_LABEL ARTIFACT_LINEAR_HINT ARTIFACT_REPO_ROOT
  local start_dir='' store='' source_name='' flag_set=0 global_config git_root boundary
  local phys_start phys_plans_root
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --store=*) store=${1#--store=}; source_name=flag; flag_set=1 ;;
      --*) _error "Unknown artifact-store option: $1"; return 1 ;;
      *)
        if [[ -n "$start_dir" || -z "$1" ]]; then
          _error 'Expected one start directory'; return 1
        fi
        start_dir=$1
        ;;
    esac
    shift
  done
  start_dir=${start_dir:-$PWD}
  start_dir=$(CDPATH='' cd -- "$start_dir" >/dev/null 2>&1 && pwd) || {
    _error 'Artifact-store start directory does not exist'; return 1
  }
  resolve_plans_dir "$start_dir" || return 1
  phys_start=$(CDPATH='' cd -- "$start_dir" >/dev/null && pwd -P) || return 1
  phys_plans_root=$(CDPATH='' cd -- "$PLANS_ROOT" >/dev/null 2>&1 && pwd -P) || phys_plans_root=$PLANS_ROOT
  git_root=$(git -C "$phys_start" rev-parse --show-toplevel 2>/dev/null) || git_root=''
  boundary=${git_root:-$phys_plans_root}
  if [[ "$flag_set" == 0 ]]; then
    if store=$(_artifact_find_config_value store "$phys_start" "$boundary"); then
      source_name=config
    else
      global_config="${XDG_CONFIG_HOME:-${HOME}/.config}/ycc/config"
      if store=$(_artifact_config_value store "$global_config"); then
        source_name=global
      else
        store=local
        source_name=default
      fi
    fi
  fi
  case "$store" in
    linear|local) ;;
    *)
      if [[ "${_ARTIFACT_AUTO_RESOLVE:-}" == 1 ]]; then
        _warn "Invalid artifact store '${store}' (${source_name}); expected linear or local"
      else
        _error "Invalid artifact store '${store}' (${source_name}); expected linear or local"
      fi
      return 1
      ;;
  esac

  export ARTIFACT_STORE="$store" ARTIFACT_STORE_SOURCE="$source_name"
  # Current checkout for repo-level kinds (prd, spec, prp-plan, prp-research,
  # prp-report, blueprint) in local mode; not part of the --shell output.
  export ARTIFACT_REPO_ROOT="${git_root:-$phys_start}"
  export ARTIFACT_ROOT="$PLANS_ROOT"
  if [[ "$store" == linear ]]; then
    if [[ -n "$git_root" && "$phys_plans_root" != "$git_root" && "$phys_plans_root" != "$git_root/"* ]]; then
      ARTIFACT_ROOT=$git_root
    fi
    export ARTIFACT_DIR="${ARTIFACT_ROOT}/.ycc/artifacts"
  else
    export ARTIFACT_DIR="$PLANS_DIR"
  fi
  ARTIFACT_LINEAR_WORKSPACE=$(_artifact_find_config_value linear_workspace "$phys_start" "$boundary") || ARTIFACT_LINEAR_WORKSPACE=''
  ARTIFACT_LINEAR_TEAM=$(_artifact_find_config_value linear_team "$phys_start" "$boundary") || ARTIFACT_LINEAR_TEAM=''
  ARTIFACT_LINEAR_PROJECT=$(_artifact_find_config_value linear_project "$phys_start" "$boundary") || ARTIFACT_LINEAR_PROJECT=''
  ARTIFACT_LINEAR_LABEL=$(_artifact_find_config_value linear_label "$phys_start" "$boundary") || ARTIFACT_LINEAR_LABEL=''
  export ARTIFACT_LINEAR_WORKSPACE ARTIFACT_LINEAR_TEAM ARTIFACT_LINEAR_PROJECT ARTIFACT_LINEAR_LABEL
  export ARTIFACT_LINEAR_HINT
  ARTIFACT_LINEAR_HINT=$(_artifact_branch_hint "$start_dir" "$ARTIFACT_LINEAR_TEAM")
  _debug "Artifact store: $ARTIFACT_STORE ($ARTIFACT_STORE_SOURCE), directory: $ARTIFACT_DIR"
  return 0
}

get_feature_artifact_dir() {
  local slug="${1:-}"
  if [[ ! "$slug" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    _error 'Artifact feature slug must start with a letter or digit and contain only letters, digits, ., _, or -'
    return 1
  fi
  [[ -n "${ARTIFACT_DIR:-}" ]] || resolve_artifact_store || return 1
  printf '%s/%s\n' "$ARTIFACT_DIR" "$slug"
}

get_artifact_path() {
  local kind="${1:-}" slug="${2:-}" base lens name repo
  [[ -n "${ARTIFACT_STORE:-}" && -n "${ARTIFACT_DIR:-}" ]] || resolve_artifact_store || return 1
  base=$(get_feature_artifact_dir "$slug") || return 1
  # Repo-level kinds stay relative to the current checkout in local mode (not
  # PLANS_ROOT), matching the cwd-relative docs/prps/... paths used before.
  repo=${ARTIFACT_REPO_ROOT:-$ARTIFACT_ROOT}
  case "$kind" in
    prd|spec|prp-plan)
      if [[ "$ARTIFACT_STORE" == linear ]]; then
        printf '%s/%s.md\n' "$base" "$kind"
      else
        case "$kind" in
          prd) printf '%s/docs/prps/prds/%s.prd.md\n' "$repo" "$slug" ;;
          spec) printf '%s/docs/prps/specs/%s.spec.md\n' "$repo" "$slug" ;;
          prp-plan) printf '%s/docs/prps/plans/%s.plan.md\n' "$repo" "$slug" ;;
        esac
      fi
      ;;
    feature-spec|parallel-plan|shared|report)
      printf '%s/%s.md\n' "$base" "$kind"
      ;;
    prp-report)
      if [[ "$ARTIFACT_STORE" == linear ]]; then
        printf '%s/report.md\n' "$base"
      else
        printf '%s/docs/prps/reports/%s-report.md\n' "$repo" "$slug"
      fi
      ;;
    research-dir)
      if [[ "$ARTIFACT_STORE" == linear ]]; then
        printf '%s/research\n' "$base"
      else
        printf '%s\n' "$base"
      fi
      ;;
    research-*)
      lens=${kind#research-}
      if [[ ! "$lens" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
        _error 'Artifact research lens must contain only lowercase letters, digits, or - and start with a letter or digit'
        return 1
      fi
      if [[ "$ARTIFACT_STORE" == linear ]]; then
        printf '%s/research/research-%s.md\n' "$base" "$lens"
      else
        printf '%s/research-%s.md\n' "$base" "$lens"
      fi
      ;;
    analysis-*)
      name=${kind#analysis-}
      if [[ ! "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
        _error 'Artifact analysis name must contain only lowercase letters, digits, or - and start with a letter or digit'
        return 1
      fi
      printf '%s/analysis-%s.md\n' "$base" "$name"
      ;;
    prp-research)
      if [[ "$ARTIFACT_STORE" == linear ]]; then
        printf '%s/.prp-research\n' "$base"
      else
        printf '%s/docs/prps/plans/.prp-research/%s\n' "$repo" "$slug"
      fi
      ;;
    deep-research)
      if [[ "$ARTIFACT_STORE" == local ]]; then
        _error "local mode keeps the skill's own output dir"
        return 1
      fi
      printf '%s/deep-research\n' "$base"
      ;;
    blueprint)
      if [[ "$ARTIFACT_STORE" == linear ]]; then
        printf '%s/blueprint.md\n' "$base"
      else
        printf '%s/docs/blueprint.md\n' "$repo"
      fi
      ;;
    feature-dir) printf '%s\n' "$base" ;;
    *)
      _error "Unknown artifact kind: ${kind:-<empty>}"
      return 1
      ;;
  esac
}

# Feature directory for the read-only planning checks (check-prerequisites,
# check-state). Linear mode uses the strict artifact slug. Local mode keeps the
# legacy ${PLANS_DIR}/<name> resolution so every name accepted before still
# works, except empty names and names with a .. path segment, which would
# escape the plans directory.
get_plans_feature_dir() {
  local name="${1:-}"
  [[ -n "${ARTIFACT_STORE:-}" && -n "${ARTIFACT_DIR:-}" ]] || resolve_artifact_store || return 1
  if [[ "$ARTIFACT_STORE" == linear ]]; then
    get_artifact_path feature-dir "$name"
    return
  fi
  if [[ -z "$name" || "$name" == /* || "$name" == .. || "$name" == ../* || "$name" == */.. || "$name" == */../* ]]; then
    _error "Invalid feature name: ${name:-<empty>}"
    return 1
  fi
  printf '%s/%s\n' "$PLANS_DIR" "$name"
}

# Check before either write entry point so a non-repository cannot gain a cache.
_artifact_require_git_root() {
  local git_root inside phys_root
  if [[ -L "${ARTIFACT_ROOT}/.gitignore" || -L "${ARTIFACT_ROOT}/.ycc" || -L "$ARTIFACT_DIR" ]]; then
    _error 'Artifact writes refuse symlinked .gitignore, .ycc, or artifact directory'
    return 1
  fi
  phys_root=$(CDPATH='' cd -- "$ARTIFACT_ROOT" >/dev/null 2>&1 && pwd -P) || phys_root=''
  inside=$(git -C "$ARTIFACT_ROOT" rev-parse --is-inside-work-tree 2>/dev/null) || inside=''
  git_root=$(git -C "$ARTIFACT_ROOT" rev-parse --show-toplevel 2>/dev/null) || git_root=''
  if [[ "$inside" != true || -z "$git_root" || ( "$phys_root" != "$git_root" && "$phys_root" != "$git_root/"* ) ]]; then
    _error 'Linear artifact writes require ARTIFACT_ROOT inside a Git work tree'
    return 1
  fi
}

# Read-only coverage check: 0 means present, 1 means append, 2 means unsafe.
_artifact_gitignore_present() {
  local file="${ARTIFACT_ROOT}/.gitignore" ignore_source ignore_pattern git_root count
  git_root=$(git -C "$ARTIFACT_ROOT" rev-parse --show-toplevel) || return 1
  # Only committed-style ignores within this repo are portable. A non-negated
  # repo rule must cover direct, nested, and representative feature-cache probes
  # so partial exceptions cannot leave later cache files visible.
  # Fields are NUL-terminated: source, line number (discarded into _), pattern,
  # and path.
  count=0
  while IFS= read -r -d '' ignore_source && IFS= read -r -d '' _ && IFS= read -r -d '' ignore_pattern && IFS= read -r -d '' _; do
    count=$((count + 1))
    [[ "$ignore_source" == /* ]] || ignore_source="${git_root}/${ignore_source}"
    ignore_source="$(CDPATH='' cd -- "$(dirname "$ignore_source")" >/dev/null 2>&1 && pwd -P)/${ignore_source##*/}"
    [[ "$ignore_source" == "$git_root/"* && "${ignore_source##*/}" == .gitignore && "$ignore_pattern" != '!'* ]] || return 2
  done < <(printf '.ycc/artifacts/probe\0.ycc/artifacts/probe-dir/probe\0.ycc/artifacts/feat/cache\0' | git -C "$ARTIFACT_ROOT" check-ignore -v -z --no-index --stdin 2>/dev/null)
  [[ "$count" == 3 ]] && return 0
  if [[ -f "$file" ]] && grep -qxE '/?\.ycc/artifacts/?' "$file"; then
    return 0
  fi
  return 1
}

ensure_artifact_gitignore() {
  [[ -n "${ARTIFACT_STORE:-}" ]] || resolve_artifact_store || return 1
  [[ "$ARTIFACT_STORE" == linear ]] || return 0
  _artifact_require_git_root || return 1
  local file="${ARTIFACT_ROOT}/.gitignore" last_byte status=0
  _artifact_gitignore_present || status=$?
  case "$status" in 0) return 0 ;; 2) return 1 ;; esac
  if [[ -s "$file" ]]; then
    # Command substitution strips a final newline, leaving an empty last byte.
    last_byte=$(tail -c 1 "$file") || return 1
    if [[ -n "$last_byte" ]]; then printf '\n' >>"$file" || return 1; fi
  fi
  printf '.ycc/artifacts/\n' >>"$file"
}

ensure_artifact_dir() {
  [[ -n "${ARTIFACT_DIR:-}" ]] || resolve_artifact_store || return 1
  if [[ "$ARTIFACT_STORE" == linear ]]; then _artifact_require_git_root || return 1; fi
  mkdir -p "$ARTIFACT_DIR" || return 1
  ensure_artifact_gitignore
}

# Local --ensure creates only what the resolved artifact needs: the parent
# directory of a file kind, or the directory itself for a directory kind.
# Linear mode keeps the cache directory and its .gitignore rule.
_artifact_ensure_for() {
  local kind="$1" artifact_path="$2"
  if [[ "$ARTIFACT_STORE" == linear ]]; then
    ensure_artifact_dir
    return
  fi
  case "$kind" in
    research-dir|feature-dir|prp-research|deep-research) mkdir -p -- "$artifact_path" ;;
    *) mkdir -p -- "$(dirname "$artifact_path")" ;;
  esac
}

_artifact_store_cli() {
  local shell_output=0 ensure=0 kind='' slug='' kind_set=0 slug_set=0 key artifact_path=''
  local -a args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help|-h)
        printf '%s\n' 'Usage: resolve-artifact-store.sh [--store=linear|local] [--debug] [--shell] [--kind=<kind> --slug=<slug>] [--ensure] [start-dir]' \
          'Resolve paths and Linear hints without writing files. Default output: ARTIFACT_DIR.' \
          '--kind and --slug print an artifact path. --ensure creates what that path needs before printing' \
          '(linear: the cache directory and its .gitignore rule; local: the parent directory, or the directory itself for directory kinds).' \
          '--ensure without --kind and --slug creates ARTIFACT_DIR.' \
          '--shell prints shell-quoted ARTIFACT_* assignments. PLANS_DEBUG=1 enables debug output.'
        return 0
        ;;
      --debug) export PLANS_DEBUG=1 ;;
      --shell) shell_output=1 ;;
      --ensure) ensure=1 ;;
      --kind=*) kind=${1#--kind=}; kind_set=1 ;;
      --slug=*) slug=${1#--slug=}; slug_set=1 ;;
      *) args+=("$1") ;;
    esac
    shift
  done
  if [[ "$kind_set" != "$slug_set" ]]; then
    _error 'Artifact path output requires both --kind and --slug'
    return 1
  fi
  resolve_artifact_store ${args[@]+"${args[@]}"} || return 1
  if [[ "$kind_set" == 1 ]]; then
    artifact_path=$(get_artifact_path "$kind" "$slug") || return 1
  fi
  if [[ "$ensure" == 1 ]]; then
    if [[ "$kind_set" == 1 ]]; then
      _artifact_ensure_for "$kind" "$artifact_path" || return 1
    else
      ensure_artifact_dir || return 1
    fi
  fi
  if [[ "$kind_set" == 1 ]]; then
    printf '%s\n' "$artifact_path"
  elif [[ "$shell_output" == 1 ]]; then
    for key in ARTIFACT_STORE ARTIFACT_STORE_SOURCE ARTIFACT_ROOT ARTIFACT_DIR \
      ARTIFACT_LINEAR_WORKSPACE ARTIFACT_LINEAR_TEAM ARTIFACT_LINEAR_PROJECT \
      ARTIFACT_LINEAR_LABEL ARTIFACT_LINEAR_HINT; do
      printf '%s=%q\n' "$key" "${!key}"
    done
  else
    printf '%s\n' "$ARTIFACT_DIR"
  fi
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  _artifact_store_cli "$@"
else
  _ARTIFACT_AUTO_RESOLVE=1 resolve_artifact_store || :
fi
