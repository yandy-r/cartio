#!/usr/bin/env bash
# ci-monitor.sh — Poll CI checks for a PR and emit structured handoff signals.
#
# Purpose:
#   Monitor GitHub Actions checks on a PR branch, classify failures, and
#   either return a green signal or a structured handoff for a fix agent.
#
# Contract summary:
#   See _shared/references/ci-monitoring.md for the full contract.
#
# Exit codes:
#   0  = green (all checks passed)
#   1  = arg/usage error
#   2  = pr not found OR refused-default-branch
#   10 = bail-recurrence (same failure seen too many times)
#   11 = bail-nonfixable (non-fixable failure category)
#   12 = bail-pushes (push cap exceeded)
#   13 = bail-timeout (wall-clock or check-registration timeout)
#   20 = handoff (caller must fix, commit, push, re-invoke)
#   21 = rerun-pending (flake-suspected; caller must sleep ~30s, then re-invoke)

set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

VERSION="1.0.0"

# ---------------------------------------------------------------------------
# Shared classifier (sourced)
# ---------------------------------------------------------------------------
# classify_failure, compute_signature, and is_fixable_category live in the
# shared lib so the PR-mode and release-mode monitors agree on the law.

# shellcheck source=lib/ci-classify.sh
. "$(dirname "$0")/lib/ci-classify.sh"

# ---------------------------------------------------------------------------
# Forge provider detection (sourced)
# ---------------------------------------------------------------------------
# The CI-autofix loop depends on `gh run view --log-failed` / `gh run rerun`,
# which have no Forgejo/Gitea equivalent. The loop is GitHub-only; on any other
# provider this script exits early with RESULT=unsupported-provider.

# shellcheck source=lib/forge.sh
. "$(dirname "$0")/lib/forge.sh"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
  cat <<'EOF'
Usage:
  ci-monitor.sh \
    --pr <number> \
    --branch <head-branch> \
    --base <base-branch> \
    --max-pushes <N> \
    --max-same-failure <N> \
    --timeout-min <N> \
    --log-file <path> \
    [--dry-run]

  ci-monitor.sh --help
  ci-monitor.sh --version

Options:
  --pr <number>           PR number to monitor
  --branch <branch>       Head branch of the PR
  --base <branch>         Base branch (used for context)
  --max-pushes <N>        Exit bail-pushes after N successful handoffs
  --max-same-failure <N>  Exit bail-recurrence after N identical failures
  --timeout-min <N>       Wall-clock timeout in minutes
  --log-file <path>       JSONL audit log path (appended each invocation)
  --dry-run               Skip CI polling; emit RESULT=green and exit 0

Exit codes:
  0   green
  1   arg/usage error
  2   pr not found or refused-default-branch
  10  bail-recurrence
  11  bail-nonfixable
  12  bail-pushes
  13  bail-timeout
  20  handoff (caller must fix, commit, push, re-invoke)
  21  rerun-pending (flake-suspected; caller must sleep ~30s, then re-invoke)
EOF
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

now_utc() {
  date -u +%FT%TZ
}

require_arg() {
  local flag="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    printf 'ci-monitor.sh: Missing required arg: %s\n' "$flag" >&2
    usage >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Audit log (JSONL)
# ---------------------------------------------------------------------------

log_jsonl() {
  local log_file="$1"
  local timestamp="$2"
  local iteration="$3"
  local pr="$4"
  local run_id="$5"
  local workflow="$6"
  local conclusion="$7"
  local classification="$8"
  local action="$9"
  local push_count="${10}"
  local signature="${11}"
  local extra="${12:-}"  # optional k:v pairs like '"dry_run":true'

  local entry
  entry="{"
  entry+="\"timestamp\":\"${timestamp}\","
  entry+="\"iteration\":${iteration},"
  entry+="\"pr\":${pr},"
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

# ---------------------------------------------------------------------------
# Log query helpers
# ---------------------------------------------------------------------------

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
# Default branch
# ---------------------------------------------------------------------------

default_branch() {
  gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null
}

# ---------------------------------------------------------------------------
# PR state
# ---------------------------------------------------------------------------

pr_state() {
  local pr="$1"
  gh pr view "$pr" --json number,headRefName,state --jq '.state' 2>/dev/null || printf ''
}

# ---------------------------------------------------------------------------
# Wait for checks to register (handles "no checks yet" race)
# ---------------------------------------------------------------------------
#
# Two-strategy probe — older `gh` versions don't expose `--json` on `pr checks`
# (the flag was added in gh 2.30), and even on newer versions the JSON output
# of `gh pr checks --json name,status` can return [] while plain
# `gh pr checks` returns rows. Both paths are checked so a green PR is never
# misread as "no checks registered".

has_pr_checks() {
  local pr="$1"
  local n

  # Strategy 1: gh pr view --json statusCheckRollup — authoritative; same field
  # GitHub uses to compute the PR's overall status.
  n="$(gh pr view "$pr" --json statusCheckRollup --jq '.statusCheckRollup | length' 2>/dev/null)"
  if [[ -n "$n" && "$n" =~ ^[0-9]+$ && "$n" -gt 0 ]]; then
    return 0
  fi

  # Strategy 2: plain `gh pr checks` text output (one row per check). Works on
  # all supported gh versions and survives shape changes in --json. Capture the
  # output and match with a here-string, not `gh ... | grep -q`: under pipefail
  # the early-exiting grep SIGPIPEs gh on long check lists (YAN-1587). gh exits
  # non-zero for failing/pending checks, which still counts as "has checks".
  local rows
  rows="$(gh pr checks "$pr" 2>/dev/null || true)"
  if grep -qE '^[^[:space:]]' <<<"$rows"; then
    return 0
  fi

  return 1
}

wait_for_checks() {
  local pr="$1"
  local backoffs=(1 2 4 8 16 30 30 30)

  for delay in "${backoffs[@]}"; do
    if has_pr_checks "$pr"; then
      return 0
    fi
    printf 'ci-monitor.sh: No CI checks registered yet; waiting %ss...\n' "$delay" >&2
    sleep "$delay"
  done

  # Final check after all backoffs
  has_pr_checks "$pr"
}

# ---------------------------------------------------------------------------
# Failure classification and signature
# ---------------------------------------------------------------------------
# classify_failure() and compute_signature() are sourced from lib/ci-classify.sh
# above so PR-mode and release-mode monitors stay aligned on the failure law.

# ---------------------------------------------------------------------------
# Extract first failing step name from gh run log output
# ---------------------------------------------------------------------------

extract_step_name() {
  local log_content="$1"
  # gh run view --log-failed output has lines like:
  # <job-name>  <step-name>  <timestamp>  <log-line>
  local step
  step="$(grep -m1 '^\S' <<<"$log_content" | awk '{print $2}' | head -n1 || printf '')"
  if [[ -z "$step" ]]; then
    step="unknown-step"
  fi
  printf '%s' "$step"
}

# ---------------------------------------------------------------------------
# Extract failing job name from run list
# ---------------------------------------------------------------------------

extract_job_name() {
  local run_id="$1"
  gh run view "$run_id" --json jobs --jq '[.jobs[] | select(.conclusion=="failure")] | .[0].name // "unknown"' 2>/dev/null || printf 'unknown'
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  # -------------------------------------------------------------------------
  # Argument parsing
  # -------------------------------------------------------------------------

  local pr="" branch="" base="" max_pushes="" max_same_failure="" timeout_min="" log_file="" dry_run="false"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --pr)
        pr="${2:-}"
        shift 2
        ;;
      --branch)
        branch="${2:-}"
        shift 2
        ;;
      --base)
        base="${2:-}"
        shift 2
        ;;
      --max-pushes)
        max_pushes="${2:-}"
        shift 2
        ;;
      --max-same-failure)
        max_same_failure="${2:-}"
        shift 2
        ;;
      --timeout-min)
        timeout_min="${2:-}"
        shift 2
        ;;
      --log-file)
        log_file="${2:-}"
        shift 2
        ;;
      --dry-run)
        dry_run="true"
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      --version)
        printf 'ci-monitor.sh %s\n' "$VERSION"
        exit 0
        ;;
      -*)
        printf 'ci-monitor.sh: unknown flag: %s\n' "$1" >&2
        usage >&2
        exit 1
        ;;
      *)
        printf 'ci-monitor.sh: unexpected argument: %s\n' "$1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done

  # Validate required args
  require_arg "--pr"             "$pr"
  require_arg "--branch"         "$branch"
  require_arg "--base"           "$base"
  require_arg "--max-pushes"     "$max_pushes"
  require_arg "--max-same-failure" "$max_same_failure"
  require_arg "--timeout-min"    "$timeout_min"
  require_arg "--log-file"       "$log_file"

  # Validate numeric args
  for pair in "max-pushes:$max_pushes" "max-same-failure:$max_same_failure" "timeout-min:$timeout_min" "pr:$pr"; do
    local flag_n="${pair%%:*}"
    local val_n="${pair#*:}"
    if ! grep -qE '^[0-9]+$' <<<"$val_n"; then
      printf 'ci-monitor.sh: --%s must be a non-negative integer, got: %s\n' "$flag_n" "$val_n" >&2
      exit 1
    fi
  done

  # -------------------------------------------------------------------------
  # Ensure log directory exists
  # -------------------------------------------------------------------------

  local log_dir
  log_dir="$(dirname "$log_file")"
  if [[ "$log_dir" != "." && ! -d "$log_dir" ]]; then
    mkdir -p "$log_dir"
  fi

  # -------------------------------------------------------------------------
  # Dry-run mode
  # -------------------------------------------------------------------------

  if [[ "$dry_run" == "true" ]]; then
    local ts iteration push_count
    ts="$(now_utc)"
    iteration="$(( $(log_line_count "$log_file") + 1 ))"
    push_count="$(count_log_action "$log_file" "handoff")"
    log_jsonl "$log_file" "$ts" "$iteration" "$pr" "0" "" "success" "" "green" "$push_count" "" '"dry_run":true'
    printf 'RESULT=green\n'
    exit 0
  fi

  # -------------------------------------------------------------------------
  # Step 0: Forge provider gate (GitHub-only feature)
  # -------------------------------------------------------------------------

  local provider
  provider="$(forge_detect_provider origin)"
  if [[ "$provider" != "github" ]]; then
    forge_unsupported_notice "$provider" "the --ci auto-fix loop"
    printf 'RESULT=unsupported-provider\n'
    exit 2
  fi

  # -------------------------------------------------------------------------
  # Step 1: Default-branch refusal
  # -------------------------------------------------------------------------

  local def_branch
  def_branch="$(default_branch)"
  if [[ -n "$def_branch" && "$branch" == "$def_branch" ]]; then
    printf 'ci-monitor.sh: Refusing to monitor PR on default branch %s; --ci only operates on feature branches.\n' "$def_branch" >&2
    printf 'RESULT=refused-default-branch\n'
    exit 2
  fi

  # -------------------------------------------------------------------------
  # Step 2: PR existence check
  # -------------------------------------------------------------------------

  local state
  state="$(pr_state "$pr")"
  if [[ -z "$state" ]]; then
    printf 'ci-monitor.sh: PR #%s not found.\n' "$pr" >&2
    printf 'RESULT=pr-not-found\n'
    exit 2
  fi

  # -------------------------------------------------------------------------
  # Step 3: Push-cap pre-check
  # -------------------------------------------------------------------------

  local push_count
  push_count="$(count_log_action "$log_file" "handoff")"
  if [[ "$push_count" -ge "$max_pushes" ]]; then
    local ts iteration
    ts="$(now_utc)"
    iteration="$(( $(log_line_count "$log_file") + 1 ))"
    log_jsonl "$log_file" "$ts" "$iteration" "$pr" "0" "" "" "" "bail-pushes" "$push_count" ""
    printf 'RESULT=bail-pushes\n'
    exit 12
  fi

  # -------------------------------------------------------------------------
  # Step 4: Wall-clock pre-check
  # -------------------------------------------------------------------------

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
      log_jsonl "$log_file" "$ts" "$iteration" "$pr" "0" "" "" "" "bail-timeout" "$push_count" ""
      printf 'RESULT=bail-timeout\n'
      exit 13
    fi
  fi

  # -------------------------------------------------------------------------
  # Step 5: Wait for checks to register
  # -------------------------------------------------------------------------

  if ! wait_for_checks "$pr"; then
    local ts iteration
    ts="$(now_utc)"
    iteration="$(( $(log_line_count "$log_file") + 1 ))"
    log_jsonl "$log_file" "$ts" "$iteration" "$pr" "0" "" "" "" "bail-timeout" "$push_count" ""
    printf 'ci-monitor.sh: No CI checks registered after 2 minutes.\n' >&2
    printf 'RESULT=bail-timeout\n'
    exit 13
  fi

  # -------------------------------------------------------------------------
  # Step 6: Watch in-flight runs (poll until all checks complete)
  # -------------------------------------------------------------------------

  printf 'ci-monitor.sh: Watching CI checks for PR #%s on branch %s...\n' "$pr" "$branch" >&2

  local watch_result=0
  # Use gh pr checks --watch; fallback to polling loop on older gh
  if gh pr checks "$pr" --watch 2>/dev/null; then
    watch_result=0
  else
    watch_result=$?
  fi

  # -------------------------------------------------------------------------
  # Determine outcome: query final check status
  # -------------------------------------------------------------------------

  local failed_count
  failed_count="$(gh run list --branch "$branch" \
    --json databaseId,conclusion,workflowName,headSha \
    --jq '[.[] | select(.conclusion=="failure")] | length' 2>/dev/null || printf '0')"

  local ts iteration
  ts="$(now_utc)"
  iteration="$(( $(log_line_count "$log_file") + 1 ))"

  # -------------------------------------------------------------------------
  # Step 7: All-green
  # -------------------------------------------------------------------------

  if [[ "$failed_count" -eq 0 && "$watch_result" -eq 0 ]]; then
    log_jsonl "$log_file" "$ts" "$iteration" "$pr" "0" "" "success" "" "green" "$push_count" ""
    printf 'RESULT=green\n'
    exit 0
  fi

  # -------------------------------------------------------------------------
  # Step 8: On any failure
  # -------------------------------------------------------------------------

  # 8a: Identify first failing run
  local failing_json
  failing_json="$(gh run list --branch "$branch" \
    --json databaseId,conclusion,workflowName,headSha \
    --jq '[.[] | select(.conclusion=="failure")] | .[0]' 2>/dev/null || printf '{}')"

  local run_id workflow_name
  run_id="$(printf '%s\n' "$failing_json" | grep -o '"databaseId":[0-9]*' | grep -o '[0-9]*' | head -n1 || printf '0')"
  workflow_name="$(printf '%s\n' "$failing_json" | grep -o '"workflowName":"[^"]*"' | sed 's/"workflowName":"//;s/"//' | head -n1 || printf 'unknown')"

  if [[ -z "$run_id" || "$run_id" == "0" ]]; then
    # Could not identify a specific run; bail as nonfixable
    log_jsonl "$log_file" "$ts" "$iteration" "$pr" "0" "${workflow_name:-unknown}" "failure" "unknown" "bail-nonfixable" "$push_count" ""
    printf 'RESULT=bail-nonfixable\n'
    printf 'REASON=Could not identify failing run ID\n'
    exit 11
  fi

  # 8b: Fetch failing logs to tmpfile
  local tmpfile
  tmpfile="$(mktemp /tmp/ci-monitor-logs.XXXXXX)"
  gh run view "$run_id" --log-failed > "$tmpfile" 2>/dev/null || true

  local log_content
  log_content="$(cat "$tmpfile")"

  # 8c: Compute failure signature
  local step_name sig
  step_name="$(extract_step_name "$log_content")"
  sig="$(compute_signature "$workflow_name" "$step_name")"

  # 8d: Same-failure recurrence check
  local prior_count
  prior_count="$(count_log_signature "$log_file" "$sig")"
  if [[ $(( prior_count + 1 )) -ge "$max_same_failure" ]]; then
    log_jsonl "$log_file" "$ts" "$iteration" "$pr" "$run_id" "$workflow_name" "failure" "" "bail-recurrence" "$push_count" "$sig"
    printf 'RESULT=bail-recurrence\n'
    rm -f "$tmpfile"
    exit 10
  fi

  # 8e: Classify failure
  local category
  category="$(classify_failure "$log_content")"

  # 8f: Non-fixable handling
  local is_fixable="false"
  case "$category" in
    lint|format|type-check|unit-test|build)
      is_fixable="true"
      ;;
    flake-suspected)
      # Count prior rerun attempts for this signature
      local rerun_count
      rerun_count="$(grep "\"action\":\"rerun\"" "$log_file" 2>/dev/null | grep -c "\"signature\":\"${sig}\"" 2>/dev/null || true)"
      rerun_count="${rerun_count:-0}"
      if [[ "$rerun_count" -eq 0 ]]; then
        # One retry attempt; caller must wait for rerun to settle then re-invoke.
        log_jsonl "$log_file" "$ts" "$iteration" "$pr" "$run_id" "$workflow_name" "failure" "$category" "rerun" "$push_count" "$sig"
        gh run rerun --failed "$run_id" >/dev/null 2>&1 || true
        printf 'ci-monitor.sh: Flaky failure suspected; retried run %s.\n' "$run_id" >&2
        printf 'RESULT=rerun-pending\n'
        printf 'REASON=flake-suspected (rerun triggered; sleep 30s then re-invoke)\n'
        printf 'RUN_ID=%s\n' "$run_id"
        printf 'WORKFLOW=%s\n' "$workflow_name"
        printf 'CATEGORY=%s\n' "$category"
        printf 'SIGNATURE=%s\n' "$sig"
        rm -f "$tmpfile"
        exit 21
      else
        # Already retried once; escalate
        log_jsonl "$log_file" "$ts" "$iteration" "$pr" "$run_id" "$workflow_name" "failure" "$category" "bail-nonfixable" "$push_count" "$sig"
        printf 'RESULT=bail-nonfixable\n'
        printf 'REASON=flake-suspected (already retried; manual investigation required)\n'
        rm -f "$tmpfile"
        exit 11
      fi
      ;;
    *)
      # integration-test, infra, secret-missing, unknown
      is_fixable="false"
      ;;
  esac

  if [[ "$is_fixable" == "false" ]]; then
    log_jsonl "$log_file" "$ts" "$iteration" "$pr" "$run_id" "$workflow_name" "failure" "$category" "bail-nonfixable" "$push_count" "$sig"
    printf 'RESULT=bail-nonfixable\n'
    printf 'REASON=%s\n' "$category"
    rm -f "$tmpfile"
    exit 11
  fi

  # 8g: Fixable — emit handoff
  local job_name
  job_name="$(extract_job_name "$run_id")"

  local new_push_count
  new_push_count="$(( push_count + 1 ))"
  log_jsonl "$log_file" "$ts" "$iteration" "$pr" "$run_id" "$workflow_name" "failure" "$category" "handoff" "$new_push_count" "$sig"

  printf 'RESULT=handoff\n'
  printf 'RUN_ID=%s\n' "$run_id"
  printf 'WORKFLOW=%s\n' "$workflow_name"
  printf 'JOB=%s\n' "$job_name"
  printf 'CATEGORY=%s\n' "$category"
  printf 'SIGNATURE=%s\n' "$sig"
  printf 'LOG_EXCERPT_FILE=%s\n' "$tmpfile"
  printf 'SUGGESTED_COMMIT_TYPE=fix\n'
  printf 'SUGGESTED_COMMIT_SCOPE=ci\n'
  exit 20
}

main "$@"
