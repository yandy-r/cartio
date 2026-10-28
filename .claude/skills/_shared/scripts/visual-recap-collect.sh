#!/usr/bin/env bash
#
# visual-recap-collect.sh — remote-agnostic local diff collector for visual recap.
#
# Collects a diff bundle using ONLY local git refs so it behaves identically on
# any remote (public GitHub or the private Forgejo). It NEVER calls a vendor PR
# API and NEVER hardcodes a remote host or branch name.
#
# Usage:
#   visual-recap-collect.sh [<base>..<head> | <branch>]
#
# Arguments:
#   (none)            Collect the working-tree diff (git diff HEAD).
#   <base>..<head>    Collect git diff <base>...<head> (explicit two endpoints).
#   <branch>          Treat <branch> as head; compute base via `git merge-base`
#                     against the tracked upstream (resolved from @{upstream}).
#
# Base resolution NEVER hardcodes a trunk branch: the upstream is resolved with
#   git rev-parse --abbrev-ref --symbolic-full-name @{upstream}
# and falls back gracefully to merge-base against HEAD when no upstream is set.
#
# Output:
#   - Diff bundle written under docs/prps/reviews/visual/<slug>/
#       diff.patch     full unified diff (local refs only)
#       files.txt      changed-file name list
#       metadata.txt   range/base/head/remote provenance
#   - Results (bundle path + summary) on stdout.
#   - All errors and progress notes on stderr.
#
# Exit codes:
#   0 - Bundle collected successfully
#   1 - Usage / git / I/O failure
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Logging helpers (stdout = results, stderr = diagnostics)
# ---------------------------------------------------------------------------

_error() {
  echo "visual-recap-collect.sh: error: $*" >&2
}

_info() {
  echo "visual-recap-collect.sh: $*" >&2
}

usage() {
  cat >&2 <<'EOF'
Usage:
  visual-recap-collect.sh [<base>..<head> | <branch>]

Arguments:
  (none)            Collect the working-tree diff (git diff HEAD).
  <base>..<head>    Collect git diff <base>...<head>.
  <branch>          Use <branch> as head; base = merge-base(@{upstream}, <branch>).
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing (strict case-based flag handling)
# ---------------------------------------------------------------------------

RANGE_ARG=""

set_range_arg() {
  if [[ -z "$RANGE_ARG" ]]; then
    RANGE_ARG="$1"
  else
    _error "unexpected positional argument: $1"
    usage
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        set_range_arg "$1"
        shift
      done
      break
      ;;
    -*)
      _error "unknown flag: $1"
      usage
      exit 1
      ;;
    *)
      set_range_arg "$1"
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Git context
# ---------------------------------------------------------------------------

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  _error "not inside a git repository"
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

CURRENT_BRANCH="$(git branch --show-current || true)"

# Resolve the tracked upstream without ever hardcoding a trunk branch name.
resolve_upstream() {
  git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true
}

# Resolve the remote URL for the given remote name (never hardcode a host).
remote_url_for() {
  local name="$1"
  git remote get-url "$name" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Range / mode resolution
# ---------------------------------------------------------------------------

MODE=""
BASE_REF=""
HEAD_REF=""
RANGE_LABEL=""

if [[ -z "$RANGE_ARG" ]]; then
  # Default: working-tree diff against HEAD.
  MODE="worktree"
  HEAD_REF="HEAD"
  RANGE_LABEL="working-tree"
elif [[ "$RANGE_ARG" == *".."* ]]; then
  MODE="range"
  BASE_REF="${RANGE_ARG%%..*}"
  HEAD_REF="${RANGE_ARG##*..}"
  if [[ -z "$BASE_REF" || -z "$HEAD_REF" ]]; then
    _error "malformed range '$RANGE_ARG'; expected <base>..<head>"
    exit 1
  fi
  RANGE_LABEL="${BASE_REF}...${HEAD_REF}"
else
  MODE="branch"
  HEAD_REF="$RANGE_ARG"
  if ! git rev-parse --verify --quiet "${HEAD_REF}^{commit}" >/dev/null; then
    _error "not a valid ref: $HEAD_REF"
    exit 1
  fi
  UPSTREAM="$(resolve_upstream)"
  if [[ -n "$UPSTREAM" ]]; then
    _info "tracked upstream: $UPSTREAM"
    BASE_REF="$(git merge-base "$UPSTREAM" "$HEAD_REF" 2>/dev/null || true)"
  fi
  if [[ -z "$BASE_REF" ]]; then
    _info "no upstream merge-base; falling back to merge-base against HEAD"
    BASE_REF="$(git merge-base HEAD "$HEAD_REF" 2>/dev/null || true)"
  fi
  if [[ -z "$BASE_REF" ]]; then
    _error "could not compute a merge-base for '$HEAD_REF'"
    exit 1
  fi
  RANGE_LABEL="${BASE_REF}...${HEAD_REF}"
fi

# ---------------------------------------------------------------------------
# Slug derivation (filesystem-safe; lowercase alnum + dashes)
# ---------------------------------------------------------------------------

slugify() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's#[^a-z0-9]\+#-#g' -e 's#^-\+##' -e 's#-\+$##'
}

case "$MODE" in
  worktree)
    SLUG_SOURCE="${CURRENT_BRANCH:-detached-head}"
    ;;
  range)
    SLUG_SOURCE="${BASE_REF}-to-${HEAD_REF}"
    ;;
  branch)
    SLUG_SOURCE="$HEAD_REF"
    ;;
esac

SLUG="$(slugify "$SLUG_SOURCE")"
if [[ -z "$SLUG" ]]; then
  SLUG="recap"
fi

# ---------------------------------------------------------------------------
# Collect the diff (LOCAL refs only — identical on GitHub and Forgejo)
# ---------------------------------------------------------------------------

OUT_DIR="${REPO_ROOT}/docs/prps/reviews/visual/${SLUG}"
mkdir -p "$OUT_DIR"

DIFF_FILE="${OUT_DIR}/diff.patch"
FILES_FILE="${OUT_DIR}/files.txt"
META_FILE="${OUT_DIR}/metadata.txt"

if [[ "$MODE" == "worktree" ]]; then
  git diff HEAD >"$DIFF_FILE" || { _error "git diff failed"; exit 1; }
  git diff --name-only HEAD >"$FILES_FILE" || { _error "git diff --name-only failed"; exit 1; }
else
  # Three-dot diff against the merge-base of base and head.
  git diff "${BASE_REF}...${HEAD_REF}" >"$DIFF_FILE" || { _error "git diff failed"; exit 1; }
  git diff --name-only "${BASE_REF}...${HEAD_REF}" >"$FILES_FILE" || { _error "git diff --name-only failed"; exit 1; }
fi

# Remote provenance: record every remote URL via `git remote get-url` so the
# bundle is auditable without hardcoding any host.
{
  echo "generated-by: visual-recap-collect.sh"
  echo "mode: ${MODE}"
  echo "range: ${RANGE_LABEL}"
  echo "base: ${BASE_REF:-<working-tree>}"
  echo "head: ${HEAD_REF}"
  echo "current-branch: ${CURRENT_BRANCH:-<detached>}"
  echo "slug: ${SLUG}"
  echo "remotes:"
  while IFS= read -r _rname; do
    [[ -z "$_rname" ]] && continue
    echo "  - ${_rname}: $(remote_url_for "$_rname")"
  done < <(git remote)
} >"$META_FILE"

CHANGED_COUNT="$(grep -c . "$FILES_FILE" 2>/dev/null)" || CHANGED_COUNT=0

# ---------------------------------------------------------------------------
# Results -> stdout
# ---------------------------------------------------------------------------

echo "$OUT_DIR"
_info "collected ${CHANGED_COUNT} changed file(s) for range '${RANGE_LABEL}'"
_info "bundle: ${OUT_DIR}/{diff.patch,files.txt,metadata.txt}"

exit 0
