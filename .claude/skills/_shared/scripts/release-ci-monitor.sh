#!/usr/bin/env bash
# release-ci-monitor.sh — Poll release-event CI for a published tag and emit
# structured handoff signals for the releaser auto-fix loop.
#
# Sibling to ci-monitor.sh (PR mode). Same exit codes, same RESULT= signaling,
# same JSONL audit-log schema, same failure-classification law (sourced from
# lib/ci-classify.sh). The only material difference is the resource being
# watched: a GitHub Actions workflow run triggered by a release event (or by a
# push to a v* tag), identified by tag rather than PR number.
#
# Contract summary:
#   See _shared/references/ci-monitoring.md, "Release mode" section, for the
#   authoritative contract. Any change here MUST be reflected there.
#
# Exit codes (identical to ci-monitor.sh):
#   0  = green (all release-CI checks passed)
#   1  = arg/usage error
#   2  = release/run not found
#   10 = bail-recurrence (same failure seen too many times)
#   11 = bail-nonfixable (non-fixable failure category)
#   12 = bail-pushes (push cap exceeded)
#   13 = bail-timeout (wall-clock or run-registration timeout)
#   20 = handoff (caller must fix, commit, push, re-cut release, re-invoke)
#   21 = rerun-pending (flake-suspected; caller must sleep ~30s, then re-invoke)

set -euo pipefail
IFS=$'\n\t'

VERSION="1.0.0"

# ---------------------------------------------------------------------------
# Shared classifier (sourced)
# ---------------------------------------------------------------------------

# shellcheck source=lib/ci-classify.sh
. "$(dirname "$0")/lib/ci-classify.sh"

# Forge provider detection. The release CI-autofix loop depends on
# `gh run` controls with no Forgejo/Gitea equivalent — GitHub-only.
# shellcheck source=lib/forge.sh
. "$(dirname "$0")/lib/forge.sh"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
  cat <<'EOF'
Usage:
  release-ci-monitor.sh \
    --tag <vX.Y.Z> \
    --max-pushes <N> \
    --max-same-failure <N> \
    --timeout-min <N> \
    --log-file <path> \
    [--workflow <name-or-path>] \
    [--repo <owner/name>] \
    [--run-id <id> --expected-sha <40-hex>] \
    [--dry-run]

  release-ci-monitor.sh --help
  release-ci-monitor.sh --version

Options:
  --tag <vX.Y.Z>          Released tag whose workflow run to monitor (required)
  --max-pushes <N>        Exit bail-pushes after N successful handoffs
  --max-same-failure <N>  Exit bail-recurrence after N identical failures
  --timeout-min <N>       Wall-clock timeout in minutes
  --log-file <path>       JSONL audit log path (appended each invocation)
  --workflow <name>       Workflow filename or display name. Auto-detects from
                          .github/workflows/*release*.yml when omitted.
  --repo <owner/name>     Target repo. Auto-detects via `gh repo view` when
                          omitted.
  --run-id <id>           Monitor exactly this run instead of selecting by tag
                          (workflow_dispatch retry, or the new release-event run
                          after a re-cut). Requires --expected-sha and an
                          explicit --workflow file; any metadata mismatch exits
                          2 (RESULT=not-found, REASON=retry-correlation-mismatch).
  --expected-sha <sha>    Full lowercase 40-hex head SHA the run must have.
  --dry-run               Skip CI polling; emit RESULT=green and exit 0

Exit codes:
  0   green
  1   arg/usage error
  2   release/run not found
  10  bail-recurrence
  11  bail-nonfixable
  12  bail-pushes
  13  bail-timeout
  20  handoff (caller must fix, commit, push, re-cut release, re-invoke)
  21  rerun-pending (flake-suspected; caller must sleep ~30s, then re-invoke)
EOF
}

# ---------------------------------------------------------------------------
# Generic helpers (parallel to ci-monitor.sh)
# ---------------------------------------------------------------------------

now_utc() {
  date -u +%FT%TZ
}

require_arg() {
  local flag="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    printf 'release-ci-monitor.sh: Missing required arg: %s\n' "$flag" >&2
    usage >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Audit log (JSONL) — same schema as ci-monitor.sh but with "tag" instead of "pr"
# ---------------------------------------------------------------------------

log_jsonl() {
  local log_file="$1"
  local timestamp="$2"
  local iteration="$3"
  local tag="$4"
  local run_id="$5"
  local workflow="$6"
  local conclusion="$7"
  local classification="$8"
  local action="$9"
  local push_count="${10}"
  local signature="${11}"
  local extra="${12:-}"

  local entry
  entry="{"
  entry+="\"timestamp\":\"${timestamp}\","
  entry+="\"iteration\":${iteration},"
  entry+="\"tag\":\"${tag}\","
  entry+="\"run_id\":${run_id},"
  entry+="\"workflow\":\"${workflow}\","
  entry+="\"conclusion\":\"${conclusion}\","
  entry+="\"classification\":\"${classification}\","
  entry+="\"action\":\"${action}\","
  entry+="\"push_count\":${push_count},"
  entry+="\"signature\":\"${signature}\""
  if [[ -n "$extra" ]]; then
    entry+=",${extra}"
  fi
  entry+="}"

  printf '%s\n' "$entry" >> "$log_file"
}

count_log_action() {
  local log_file="$1"
  local action="$2"
  if [[ ! -f "$log_file" ]]; then
    printf '0'
    return
  fi
  local count
  count="$(grep -c "\"action\":\"${action}\"" "$log_file" 2>/dev/null || true)"
  printf '%s' "${count:-0}"
}

count_log_signature() {
  local log_file="$1"
  local sig="$2"
  if [[ ! -f "$log_file" ]]; then
    printf '0'
    return
  fi
  local count
  count="$(grep -c "\"signature\":\"${sig}\"" "$log_file" 2>/dev/null || true)"
  printf '%s' "${count:-0}"
}

first_log_timestamp() {
  local log_file="$1"
  if [[ ! -f "$log_file" ]] || [[ ! -s "$log_file" ]]; then
    printf ''
    return
  fi
  head -n1 "$log_file" | grep -o '"timestamp":"[^"]*"' | head -n1 | sed 's/"timestamp":"//;s/"//'
}

log_line_count() {
  local log_file="$1"
  if [[ ! -f "$log_file" ]]; then
    printf '0'
    return
  fi
  wc -l < "$log_file" | tr -d ' '
}

# ---------------------------------------------------------------------------
# Release workflow detection
# ---------------------------------------------------------------------------

# Find the release workflow file. Preference order:
#   1. An explicit --workflow value (filename or display name).
#   2. A file under .github/workflows/ whose basename matches *release*.yml.
#
# Prints the workflow filename (with .yml extension) to stdout, or empty on
# failure.
detect_workflow_file() {
  local explicit="$1"
  if [[ -n "$explicit" ]]; then
    # Accept either basename or full path; emit basename only.
    printf '%s' "$(basename "$explicit")"
    return
  fi
  local candidate=""
  if [[ -d .github/workflows ]]; then
    candidate="$(find .github/workflows -maxdepth 1 -type f \( -iname '*release*.yml' -o -iname '*release*.yaml' \) 2>/dev/null | head -n1 || true)"
  fi
  if [[ -n "$candidate" ]]; then
    printf '%s' "$(basename "$candidate")"
  fi
}

# Auto-detect repo (owner/name). Prints stdout, or empty.
detect_repo() {
  local explicit="$1"
  if [[ -n "$explicit" ]]; then
    printf '%s' "$explicit"
    return
  fi
  gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null || printf ''
}

# ---------------------------------------------------------------------------
# Wait for the release workflow run to register
# ---------------------------------------------------------------------------
#
# A release event takes a few seconds to fan out into a workflow run. Poll
# `gh run list` until a run for this tag appears, with a bounded backoff.
# Returns 0 if a run is found, 1 if none registered within the backoff window.

list_runs_for_tag() {
  local tag="$1"
  local workflow="$2"
  local repo="$3"
  local repo_arg=()
  [[ -n "$repo" ]] && repo_arg=(--repo "$repo")

  # Strategy 1: filter by workflow + event=release, then narrow to runs whose
  # headBranch matches the tag (gh exposes the tag in headBranch for release-
  # event runs).
  local runs
  runs="$(gh run list "${repo_arg[@]}" \
    --workflow "$workflow" \
    --event release \
    --limit 20 \
    --json databaseId,headBranch,conclusion,status,workflowName,createdAt \
    2>/dev/null || true)"
  if [[ -n "$runs" && "$runs" != "[]" ]]; then
    # Filter to entries matching this tag.
    local filtered
    filtered="$(printf '%s' "$runs" | grep -o "{[^}]*\"headBranch\":\"$tag\"[^}]*}" || true)"
    if [[ -n "$filtered" ]]; then
      printf '%s' "$filtered"
      return 0
    fi
  fi

  # Strategy 2: fall back to branch=refs/tags/<tag> for workflows triggered by
  # `push: tags: v*`.
  runs="$(gh run list "${repo_arg[@]}" \
    --workflow "$workflow" \
    --branch "refs/tags/$tag" \
    --limit 5 \
    --json databaseId,headBranch,conclusion,status,workflowName,createdAt \
    2>/dev/null || true)"
  if [[ -n "$runs" && "$runs" != "[]" ]]; then
    printf '%s' "$runs"
    return 0
  fi

  return 1
}

has_release_run() {
  local tag="$1"
  local workflow="$2"
  local repo="$3"
  list_runs_for_tag "$tag" "$workflow" "$repo" >/dev/null
}

wait_for_release_run() {
  local tag="$1"
  local workflow="$2"
  local repo="$3"
  local backoffs=(2 4 8 16 30 30 30 30)

  for delay in "${backoffs[@]}"; do
    if has_release_run "$tag" "$workflow" "$repo"; then
      return 0
    fi
    printf 'release-ci-monitor.sh: No release workflow run for %s yet; waiting %ss...\n' "$tag" "$delay" >&2
    sleep "$delay"
  done

  has_release_run "$tag" "$workflow" "$repo"
}

# Pick the most recent run for this tag and emit its run id and workflow name
# on stdout as 'RUN_ID|WORKFLOW_NAME'. Empty string on failure.
latest_run_for_tag() {
  local tag="$1"
  local workflow="$2"
  local repo="$3"
  local runs
  runs="$(list_runs_for_tag "$tag" "$workflow" "$repo" 2>/dev/null || true)"
  if [[ -z "$runs" ]]; then
    printf ''
    return
  fi

  local run_id workflow_name
  run_id="$(printf '%s\n' "$runs" | grep -o '"databaseId":[0-9]*' | head -n1 | grep -o '[0-9]*' || true)"
  workflow_name="$(printf '%s\n' "$runs" | grep -o '"workflowName":"[^"]*"' | head -n1 | sed 's/"workflowName":"//;s/"//' || true)"
  if [[ -z "$run_id" ]]; then
    printf ''
    return
  fi
  printf '%s|%s' "$run_id" "${workflow_name:-unknown}"
}

# Verify --run-id is the exact retry target: repo-scoped run metadata must match
# the id, expected head SHA, event workflow_dispatch (branch-ref retry) or
# release (post-re-cut re-publish) and the workflow file path. The expected SHA
# is the fix-bearing commit, so a pre-re-cut run on the same tag fails closed.
# Prints the workflow name on success; returns 1 on any mismatch or fetch error.
verify_exact_run() {
  local run_id="$1" sha="$2" workflow_file="$3" repo="$4"
  [[ -n "$repo" ]] || return 1
  local meta id head event name api path
  meta="$(gh run view --repo "$repo" "$run_id" \
    --json databaseId,headSha,event,workflowName \
    --jq '[.databaseId,.headSha,.event,.workflowName]|@tsv' 2>/dev/null)" || return 1
  # Fail closed on anything but a single metadata line (read -r sees line 1 only).
  [[ "$meta" != *$'\n'* ]] || return 1
  IFS=$'\t' read -r id head event name <<<"$meta"
  [[ "$id" == "$run_id" && "$head" == "$sha" ]] || return 1
  [[ "$event" == "workflow_dispatch" || "$event" == "release" ]] || return 1
  # gh run view exposes no workflow path field; the run API does.
  api="$(gh api "repos/$repo/actions/runs/$run_id" --jq '[.id,.path]|@tsv' 2>/dev/null)" || return 1
  [[ "$api" != *$'\n'* ]] || return 1
  IFS=$'\t' read -r id path <<<"$api"
  [[ "$id" == "$run_id" && "$path" == ".github/workflows/$workflow_file" ]] || return 1
  printf '%s' "${name:-unknown}"
}

# Watch a single run to completion.
watch_run() {
  local run_id="$1"
  local repo="$2"
  local repo_arg=()
  [[ -n "$repo" ]] && repo_arg=(--repo "$repo")
  gh run watch "${repo_arg[@]}" "$run_id" --exit-status 2>/dev/null
}

# Fetch run conclusion: success | failure | cancelled | ''
run_conclusion() {
  local run_id="$1"
  local repo="$2"
  local repo_arg=()
  [[ -n "$repo" ]] && repo_arg=(--repo "$repo")
  gh run view "${repo_arg[@]}" "$run_id" --json conclusion --jq '.conclusion' 2>/dev/null || printf ''
}

# Extract step name + job name for the first failing step.
extract_step_name() {
  local log_content="$1"
  local step
  step="$(grep -m1 '^\S' <<<"$log_content" | awk '{print $2}' | head -n1 || printf '')"
  if [[ -z "$step" ]]; then
    step="unknown-step"
  fi
  printf '%s' "$step"
}

extract_job_name() {
  local run_id="$1"
  local repo="$2"
  local repo_arg=()
  [[ -n "$repo" ]] && repo_arg=(--repo "$repo")
  gh run view "${repo_arg[@]}" "$run_id" --json jobs --jq '[.jobs[] | select(.conclusion=="failure")] | .[0].name // "unknown"' 2>/dev/null || printf 'unknown'
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  local tag="" workflow="" repo="" max_pushes="" max_same_failure="" timeout_min="" log_file="" dry_run="false" run_id_arg="" expected_sha=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tag)               tag="${2:-}";              shift 2 ;;
      --workflow)          workflow="${2:-}";         shift 2 ;;
      --repo)              repo="${2:-}";             shift 2 ;;
      --max-pushes)        max_pushes="${2:-}";       shift 2 ;;
      --max-same-failure)  max_same_failure="${2:-}"; shift 2 ;;
      --timeout-min)       timeout_min="${2:-}";      shift 2 ;;
      --log-file)          log_file="${2:-}";         shift 2 ;;
      --run-id)            run_id_arg="${2:-}";       shift 2 ;;
      --expected-sha)      expected_sha="${2:-}";     shift 2 ;;
      --dry-run)           dry_run="true";            shift   ;;
      --help|-h)           usage; exit 0 ;;
      --version)           printf 'release-ci-monitor.sh %s\n' "$VERSION"; exit 0 ;;
      -*)
        printf 'release-ci-monitor.sh: unknown flag: %s\n' "$1" >&2
        usage >&2
        exit 1
        ;;
      *)
        printf 'release-ci-monitor.sh: unexpected argument: %s\n' "$1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done

  require_arg "--tag"               "$tag"
  require_arg "--max-pushes"        "$max_pushes"
  require_arg "--max-same-failure"  "$max_same_failure"
  require_arg "--timeout-min"       "$timeout_min"
  require_arg "--log-file"          "$log_file"

  for pair in "max-pushes:$max_pushes" "max-same-failure:$max_same_failure" "timeout-min:$timeout_min"; do
    local flag_n="${pair%%:*}"
    local val_n="${pair#*:}"
    if ! grep -qE '^[0-9]+$' <<<"$val_n"; then
      printf 'release-ci-monitor.sh: --%s must be a non-negative integer, got: %s\n' "$flag_n" "$val_n" >&2
      exit 1
    fi
  done

  # Exact-run (retry) mode: --run-id and --expected-sha travel together.
  if [[ -n "$run_id_arg" || -n "$expected_sha" ]]; then
    if [[ ! "$run_id_arg" =~ ^[1-9][0-9]*$ ]]; then
      printf 'release-ci-monitor.sh: --run-id must be a positive integer\n' >&2
      exit 1
    fi
    if [[ ! "$expected_sha" =~ ^[0-9a-f]{40}$ ]]; then
      printf 'release-ci-monitor.sh: --expected-sha must be a full lowercase 40-hex SHA\n' >&2
      exit 1
    fi
    require_arg "--workflow" "$workflow"
  fi

  # Resolve workflow + repo (auto-detect when unset).
  local resolved_workflow resolved_repo
  resolved_workflow="$(detect_workflow_file "$workflow")"
  resolved_repo="$(detect_repo "$repo")"

  # Ensure log directory exists.
  local log_dir
  log_dir="$(dirname "$log_file")"
  if [[ "$log_dir" != "." && ! -d "$log_dir" ]]; then
    mkdir -p "$log_dir"
  fi

  # Dry-run short-circuit.
  if [[ "$dry_run" == "true" ]]; then
    local ts iteration push_count
    ts="$(now_utc)"
    iteration="$(( $(log_line_count "$log_file") + 1 ))"
    push_count="$(count_log_action "$log_file" "handoff")"
    log_jsonl "$log_file" "$ts" "$iteration" "$tag" "0" "" "success" "" "green" "$push_count" "" '"dry_run":true'
    printf 'RESULT=green\n'
    exit 0
  fi

  # Forge provider gate (GitHub-only feature).
  local provider
  provider="$(forge_detect_provider origin)"
  if [[ "$provider" != "github" ]]; then
    forge_unsupported_notice "$provider" "the release --ci auto-fix loop"
    printf 'RESULT=unsupported-provider\n'
    exit 2
  fi

  # Workflow file must be resolvable in non-dry-run mode.
  if [[ -z "$resolved_workflow" ]]; then
    printf 'release-ci-monitor.sh: Could not auto-detect a release workflow. Pass --workflow <name>.\n' >&2
    printf 'RESULT=not-found\n'
    exit 2
  fi

  # Push-cap pre-check.
  local push_count
  push_count="$(count_log_action "$log_file" "handoff")"
  if [[ "$push_count" -ge "$max_pushes" ]]; then
    local ts iteration
    ts="$(now_utc)"
    iteration="$(( $(log_line_count "$log_file") + 1 ))"
    log_jsonl "$log_file" "$ts" "$iteration" "$tag" "0" "$resolved_workflow" "" "" "bail-pushes" "$push_count" ""
    printf 'RESULT=bail-pushes\n'
    exit 12
  fi

  # Wall-clock pre-check.
  local first_ts
  first_ts="$(first_log_timestamp "$log_file")"
  if [[ -n "$first_ts" ]]; then
    local first_epoch now_epoch elapsed_min
    first_epoch="$(date -u -d "$first_ts" +%s 2>/dev/null || date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$first_ts" +%s 2>/dev/null || printf '0')"
    now_epoch="$(date -u +%s)"
    elapsed_min="$(( (now_epoch - first_epoch) / 60 ))"
    if [[ "$elapsed_min" -ge "$timeout_min" ]]; then
      local ts iteration
      ts="$(now_utc)"
      iteration="$(( $(log_line_count "$log_file") + 1 ))"
      log_jsonl "$log_file" "$ts" "$iteration" "$tag" "0" "$resolved_workflow" "" "" "bail-timeout" "$push_count" ""
      printf 'RESULT=bail-timeout\n'
      exit 13
    fi
  fi

  local run_meta run_id workflow_name
  if [[ -n "$run_id_arg" ]]; then
    # Exact-run mode: fail closed unless the requested run matches; re-checked
    # on every invocation. Tag selection is skipped.
    if ! workflow_name="$(verify_exact_run "$run_id_arg" "$expected_sha" "$resolved_workflow" "$resolved_repo")"; then
      local ts iteration
      ts="$(now_utc)"
      iteration="$(( $(log_line_count "$log_file") + 1 ))"
      log_jsonl "$log_file" "$ts" "$iteration" "$tag" "$run_id_arg" "$resolved_workflow" "" "" "bail-nonfixable" "$push_count" "" "\"head_sha\":\"${expected_sha}\",\"reason\":\"retry-correlation-mismatch\""
      printf 'RESULT=not-found\n'
      printf 'REASON=retry-correlation-mismatch\n'
      exit 2
    fi
    run_id="$run_id_arg"
    local ts iteration
    ts="$(now_utc)"
    iteration="$(( $(log_line_count "$log_file") + 1 ))"
    log_jsonl "$log_file" "$ts" "$iteration" "$tag" "$run_id" "$workflow_name" "" "" "retry-verified" "$push_count" "" "\"head_sha\":\"${expected_sha}\""
  else
    # Wait for the release workflow run to register.
    if ! wait_for_release_run "$tag" "$resolved_workflow" "$resolved_repo"; then
      local ts iteration
      ts="$(now_utc)"
      iteration="$(( $(log_line_count "$log_file") + 1 ))"
      log_jsonl "$log_file" "$ts" "$iteration" "$tag" "0" "$resolved_workflow" "" "" "bail-timeout" "$push_count" ""
      printf 'release-ci-monitor.sh: No release workflow run registered for %s within backoff window.\n' "$tag" >&2
      printf 'RESULT=bail-timeout\n'
      exit 13
    fi

    # Resolve the current run id.
    run_meta="$(latest_run_for_tag "$tag" "$resolved_workflow" "$resolved_repo")"
    if [[ -z "$run_meta" ]]; then
      local ts iteration
      ts="$(now_utc)"
      iteration="$(( $(log_line_count "$log_file") + 1 ))"
      log_jsonl "$log_file" "$ts" "$iteration" "$tag" "0" "$resolved_workflow" "" "" "bail-nonfixable" "$push_count" ""
      printf 'RESULT=not-found\n'
      exit 2
    fi
    run_id="${run_meta%%|*}"
    workflow_name="${run_meta#*|}"
  fi

  printf 'release-ci-monitor.sh: Watching release workflow run %s (%s) for %s...\n' "$run_id" "$workflow_name" "$tag" >&2

  # Watch the run to completion.
  local watch_result=0
  if watch_run "$run_id" "$resolved_repo"; then
    watch_result=0
  else
    watch_result=$?
  fi

  local conclusion
  conclusion="$(run_conclusion "$run_id" "$resolved_repo")"

  local ts iteration
  ts="$(now_utc)"
  iteration="$(( $(log_line_count "$log_file") + 1 ))"

  # Green path.
  if [[ "$conclusion" == "success" && "$watch_result" -eq 0 ]]; then
    log_jsonl "$log_file" "$ts" "$iteration" "$tag" "$run_id" "$workflow_name" "success" "" "green" "$push_count" ""
    printf 'RESULT=green\n'
    exit 0
  fi

  # Failure path.
  local tmpfile
  tmpfile="$(mktemp /tmp/release-ci-monitor-logs.XXXXXX)"
  local repo_arg=()
  [[ -n "$resolved_repo" ]] && repo_arg=(--repo "$resolved_repo")
  gh run view "${repo_arg[@]}" "$run_id" --log-failed > "$tmpfile" 2>/dev/null || true

  local log_content
  log_content="$(cat "$tmpfile")"

  local step_name sig
  step_name="$(extract_step_name "$log_content")"
  sig="$(compute_signature "$workflow_name" "$step_name")"

  local prior_count
  prior_count="$(count_log_signature "$log_file" "$sig")"
  if [[ $(( prior_count + 1 )) -ge "$max_same_failure" ]]; then
    log_jsonl "$log_file" "$ts" "$iteration" "$tag" "$run_id" "$workflow_name" "failure" "" "bail-recurrence" "$push_count" "$sig"
    printf 'RESULT=bail-recurrence\n'
    rm -f "$tmpfile"
    exit 10
  fi

  local category
  category="$(classify_failure "$log_content")"

  if [[ "$category" == "flake-suspected" ]]; then
    local rerun_count
    rerun_count="$(grep "\"action\":\"rerun\"" "$log_file" 2>/dev/null | grep -c "\"signature\":\"${sig}\"" 2>/dev/null || true)"
    rerun_count="${rerun_count:-0}"
    if [[ "$rerun_count" -eq 0 ]]; then
      log_jsonl "$log_file" "$ts" "$iteration" "$tag" "$run_id" "$workflow_name" "failure" "$category" "rerun" "$push_count" "$sig"
      gh run rerun "${repo_arg[@]}" --failed "$run_id" >/dev/null 2>&1 || true
      printf 'release-ci-monitor.sh: Flaky failure suspected; retried release run %s.\n' "$run_id" >&2
      printf 'RESULT=rerun-pending\n'
      printf 'REASON=flake-suspected (rerun triggered; sleep 30s then re-invoke)\n'
      printf 'RUN_ID=%s\n' "$run_id"
      printf 'WORKFLOW=%s\n' "$workflow_name"
      printf 'CATEGORY=%s\n' "$category"
      printf 'SIGNATURE=%s\n' "$sig"
      rm -f "$tmpfile"
      exit 21
    else
      log_jsonl "$log_file" "$ts" "$iteration" "$tag" "$run_id" "$workflow_name" "failure" "$category" "bail-nonfixable" "$push_count" "$sig"
      printf 'RESULT=bail-nonfixable\n'
      printf 'REASON=flake-suspected (already retried; manual investigation required)\n'
      rm -f "$tmpfile"
      exit 11
    fi
  fi

  if ! is_fixable_category "$category"; then
    log_jsonl "$log_file" "$ts" "$iteration" "$tag" "$run_id" "$workflow_name" "failure" "$category" "bail-nonfixable" "$push_count" "$sig"
    printf 'RESULT=bail-nonfixable\n'
    printf 'REASON=%s\n' "$category"
    rm -f "$tmpfile"
    exit 11
  fi

  # Fixable — emit handoff.
  local job_name
  job_name="$(extract_job_name "$run_id" "$resolved_repo")"

  local new_push_count
  new_push_count="$(( push_count + 1 ))"
  log_jsonl "$log_file" "$ts" "$iteration" "$tag" "$run_id" "$workflow_name" "failure" "$category" "handoff" "$new_push_count" "$sig"

  printf 'RESULT=handoff\n'
  printf 'RUN_ID=%s\n' "$run_id"
  printf 'WORKFLOW=%s\n' "$workflow_name"
  printf 'WORKFLOW_FILE=%s\n' "$resolved_workflow"
  printf 'JOB=%s\n' "$job_name"
  printf 'STEP=%s\n' "$step_name"
  printf 'CATEGORY=%s\n' "$category"
  printf 'SIGNATURE=%s\n' "$sig"
  printf 'LOG_EXCERPT_FILE=%s\n' "$tmpfile"
  printf 'TAG=%s\n' "$tag"
  printf 'REPO=%s\n' "$resolved_repo"
  printf 'SUGGESTED_COMMIT_TYPE=fix\n'
  printf 'SUGGESTED_COMMIT_SCOPE=ci\n'
  exit 20
}

main "$@"
