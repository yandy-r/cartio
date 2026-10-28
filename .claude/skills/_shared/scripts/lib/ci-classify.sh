#!/usr/bin/env bash
# ci-classify.sh — Shared failure classification and signature helpers.
#
# Sourced by:
#   - ycc/skills/_shared/scripts/ci-monitor.sh         (PR-mode monitor)
#   - ycc/skills/_shared/scripts/release-ci-monitor.sh (release-mode monitor)
#
# Contract:
#   See _shared/references/ci-monitoring.md ("Failure classification" section)
#   for the canonical category list and the rules that drive ordering. Any
#   change here MUST be reflected in that document and vice versa — these
#   two artifacts are the law.
#
# This file is intentionally sourced, not executed. It sets no `set -euo`
# so callers control their own shell strictness.

# Guard against double-sourcing.
if [[ -n "${_YCC_CI_CLASSIFY_SOURCED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
_YCC_CI_CLASSIFY_SOURCED=1

# ---------------------------------------------------------------------------
# classify_failure <log-content>
#
# Read a blob of failed-step log output and emit one canonical category to
# stdout. Categories are matched in priority order (fixable first, then
# non-fixable, then flake, then unknown). Always emits exactly one token.
# ---------------------------------------------------------------------------
classify_failure() {
  local log_content="$1"

  # Match the log blob with here-strings, not `printf | grep -q`: CI logs are
  # regularly larger than the 64 KiB pipe buffer, so under pipefail the
  # early-exiting grep SIGPIPEs the writer and a present category signature
  # reads as absent (YAN-1587).
  if grep -qiE '(eslint|pylint|ruff|flake8|rubocop|golangci|lint(ing)?[: ]|linter (error|fail)|SC[0-9]{4})' <<<"$log_content"; then
    printf 'lint'
    return
  fi
  if grep -qiE '(prettier|black|gofmt|rustfmt|format(ting)?[: ]|autoformat|code style)' <<<"$log_content"; then
    printf 'format'
    return
  fi
  if grep -qiE '(tsc|mypy|pyright|type.check|type error|TypeScript error|type-check|typecheck)' <<<"$log_content"; then
    printf 'type-check'
    return
  fi
  if grep -qiE '(jest|pytest|cargo test|go test|rspec|mocha|vitest|unit.test|test (fail|error)|FAIL.*\.test\.)' <<<"$log_content"; then
    printf 'unit-test'
    return
  fi
  if grep -qiE '(npm (run )?build|cargo build|go build|webpack|vite build|next build|build (fail|error)|compilation failed)' <<<"$log_content"; then
    printf 'build'
    return
  fi

  # Non-fixable categories
  if grep -qiE '(integration.test|e2e|end.to.end|cypress|playwright)' <<<"$log_content"; then
    printf 'integration-test'
    return
  fi
  if grep -qiE '(terraform|ansible|kubernetes|k8s|helm|infra(structure)?|deploy(ment)?|provisioning)' <<<"$log_content"; then
    printf 'infra'
    return
  fi
  if grep -qiE '(secret (not found|missing|undefined)|API.?key (not|missing)|token (not found|missing|expired)|credentials missing|env.*not set)' <<<"$log_content"; then
    printf 'secret-missing'
    return
  fi
  if grep -qiE '(flake|intermittent|timeout|rate.limit|network error|connection refused|socket hang|ECONNRESET|ETIMEDOUT)' <<<"$log_content"; then
    printf 'flake-suspected'
    return
  fi

  printf 'unknown'
}

# ---------------------------------------------------------------------------
# compute_signature <workflow-name> <step-name>
#
# Stable 16-char sha256 prefix identifying a failure shape. Step name is
# normalized (lowercased, whitespace collapsed) so trivial casing or spacing
# changes do not produce a different signature.
# ---------------------------------------------------------------------------
compute_signature() {
  local workflow_name="$1"
  local step_name="$2"
  local normalized
  normalized="$(printf '%s' "$step_name" | tr '[:upper:]' '[:lower:]' | tr -s ' \t' ' ' | sed 's/^ //;s/ $//')"
  printf '%s|%s' "$workflow_name" "$normalized" | sha256sum | cut -c1-16
}

# ---------------------------------------------------------------------------
# is_fixable_category <category>
#
# Exit 0 if the category is one the fix loop is allowed to dispatch a fix
# agent against; exit 1 otherwise. Centralised so PR and release monitors
# share the same definition.
# ---------------------------------------------------------------------------
is_fixable_category() {
  case "$1" in
    lint|format|type-check|unit-test|build) return 0 ;;
    *) return 1 ;;
  esac
}
