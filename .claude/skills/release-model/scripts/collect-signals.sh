#!/usr/bin/env bash
# collect-signals.sh — read-only survey of a repository's branching and
# release habits, used to propose a release model for RELEASING.md.
#
# Usage:
#   collect-signals.sh [--repo DIR]
#
# Prints key=value lines, in this order:
#   default_branch        origin/HEAD's branch, else main|master|trunk|develop,
#                         else the current branch
#   long_lived_branches   comma list of origin branches (local branches when there
#                         is no origin) other than the default and release/*,
#                         with a commit in the last 30 days and more than 20
#                         commits not on the default branch
#   tags_count            number of tags
#   latest_tag            highest semver tag (empty when none)
#   tag_pattern           vX.Y.Z | X.Y.Z | none
#   older_line_patched    1 when a semver tag's X.Y is lower than the X.Y of a
#                         tag created before it, else 0
#   release_branches      comma list of release/* branches (origin or local),
#                         newest first
#   gh_releases           GitHub release count, or "unknown" when gh is missing,
#                         unauthenticated, or the call fails
#   ci_trigger_branches   branches named under push: branches: in
#                         .github/workflows/*.yml|*.yaml
#   publish_workflow      first workflow triggered by push: tags: or a release
#                         event (empty when none)
#   version_files         comma list of version-bearing manifests
#   changelog             CHANGELOG.md path (empty when none)
#   sync_merges_recent    merge commits among the last 200 first-parent commits
#                         of the default branch that look like "sync" merges
#   linear_refs           1 when recent commit subjects or branch names contain
#                         an issue key such as ABC-123, else 0
#   suggested_model       release-branches | trunk-only
#   suggested_reason      one sentence explaining the suggestion
#
# Never fetches, pushes, or writes. Forge failures degrade to "unknown".
#
# Exit codes:
#   0  success
#   1  usage error, or DIR is not a git repository

set -euo pipefail

usage() {
  sed -n '2,42p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
}

_error() {
  echo "collect-signals: $*" >&2
}

REPO_DIR="."
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      if [[ $# -lt 2 || -z "$2" ]]; then
        _error "--repo requires a directory"
        exit 1
      fi
      REPO_DIR="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      _error "unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ ! -d "$REPO_DIR" ]]; then
  _error "no such directory: $REPO_DIR"
  exit 1
fi
if ! ROOT="$(git -C "$REPO_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
  _error "not a git repository: $REPO_DIR"
  exit 1
fi

SEMVER_RE='^v?([0-9]+)\.([0-9]+)\.([0-9]+)([-+].*)?$'
LONG_LIVED_MIN_AHEAD=20
LONG_LIVED_MAX_AGE_DAYS=30
RECENT_COMMITS=200

g() {
  git -C "$ROOT" "$@"
}

ref_exists() {
  g rev-parse --verify --quiet "$1^{commit}" >/dev/null 2>&1
}

has_origin() {
  g remote get-url origin >/dev/null 2>&1
}

# join_lines — read lines on stdin, print them comma-joined (no trailing comma).
join_lines() {
  paste -sd, - | sed 's/,$//'
}

# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------

detect_default_branch() {
  local branch candidate
  branch="$(g symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  branch="${branch#origin/}"
  if [[ -n "$branch" ]]; then
    echo "$branch"
    return
  fi
  for candidate in main master trunk develop; do
    if ref_exists "refs/heads/${candidate}" || ref_exists "refs/remotes/origin/${candidate}"; then
      echo "$candidate"
      return
    fi
  done
  branch="$(g symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  echo "${branch:-main}"
}

# default_ref — the ref that best represents the default branch's history.
default_ref() {
  if ref_exists "refs/remotes/origin/${DEFAULT_BRANCH}"; then
    echo "origin/${DEFAULT_BRANCH}"
  elif ref_exists "refs/heads/${DEFAULT_BRANCH}"; then
    echo "$DEFAULT_BRANCH"
  else
    echo "HEAD"
  fi
}

# candidate_branches — short names of branches to inspect for long-lived use.
candidate_branches() {
  if has_origin; then
    g for-each-ref --format='%(refname:short)' refs/remotes/origin |
      sed -n 's|^origin/||p' | grep -vx 'HEAD' || true
  else
    g for-each-ref --format='%(refname:short)' refs/heads
  fi
}

branch_ref() {
  if has_origin; then
    echo "origin/$1"
  else
    echo "$1"
  fi
}

detect_long_lived_branches() {
  local base="$1" branch ref ahead last now cutoff
  now="$(date +%s)"
  cutoff=$((now - LONG_LIVED_MAX_AGE_DAYS * 86400))
  candidate_branches | while IFS= read -r branch; do
    [[ -z "$branch" || "$branch" == "$DEFAULT_BRANCH" || "$branch" == release/* ]] && continue
    ref="$(branch_ref "$branch")"
    ref_exists "$ref" || continue
    last="$(g log -1 --format=%ct "$ref" 2>/dev/null || echo 0)"
    [[ "$last" -ge "$cutoff" ]] || continue
    ahead="$(g rev-list --count "$ref" "^${base}" 2>/dev/null || echo 0)"
    if [[ "$ahead" -gt "$LONG_LIVED_MIN_AHEAD" ]]; then
      echo "$branch"
    fi
  done | join_lines
}

detect_release_branches() {
  {
    g for-each-ref --format='%(refname:short)' refs/heads/release
    g for-each-ref --format='%(refname:short)' refs/remotes/origin/release | sed 's|^origin/||'
  } | { grep -E '^release/[^/]+$' || true; } | sort -u | sort -t/ -k2 -Vr | join_lines
}

# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

semver_tags_by_creation() {
  g for-each-ref --sort=creatordate --format='%(refname:short)' refs/tags |
    grep -E "$SEMVER_RE" || true
}

detect_latest_tag() {
  g tag --sort=-v:refname | grep -E "$SEMVER_RE" | head -n 1 || true
}

detect_tag_pattern() {
  local latest="$1"
  if [[ -z "$latest" ]]; then
    echo "none"
  elif [[ "$latest" == v* ]]; then
    echo "vX.Y.Z"
  else
    echo "X.Y.Z"
  fi
}

# detect_older_line_patch — print "<tag> <newer-line-tag>" for the first tag
# whose X.Y is lower than an earlier-created tag's X.Y; print nothing otherwise.
detect_older_line_patch() {
  local tag major minor line max_line=-1 max_tag=""
  while IFS= read -r tag; do
    [[ "$tag" =~ $SEMVER_RE ]] || continue
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    line=$((10#$major * 100000 + 10#$minor))
    if [[ "$line" -lt "$max_line" ]]; then
      echo "$tag $max_tag"
      return
    fi
    if [[ "$line" -gt "$max_line" ]]; then
      max_line="$line"
      max_tag="$tag"
    fi
  done < <(semver_tags_by_creation)
}

# ---------------------------------------------------------------------------
# Forge
# ---------------------------------------------------------------------------

detect_gh_releases() {
  local count
  local runner=()
  if ! command -v gh >/dev/null 2>&1; then
    echo "unknown"
    return
  fi
  command -v timeout >/dev/null 2>&1 && runner=(timeout 20)
  if ! (cd "$ROOT" && ${runner[@]+"${runner[@]}"} gh auth status >/dev/null 2>&1); then
    echo "unknown"
    return
  fi
  if count="$(cd "$ROOT" && ${runner[@]+"${runner[@]}"} gh release list --limit 1000 --json tagName --jq 'length' 2>/dev/null)" &&
    [[ "$count" =~ ^[0-9]+$ ]]; then
    echo "$count"
  else
    echo "unknown"
  fi
}

# ---------------------------------------------------------------------------
# CI workflows
# ---------------------------------------------------------------------------

# scan_workflow <file> — print "B <branch>" for each push: branches: entry,
# "T" when push: has tags:, and "R" when the workflow runs on a release event.
scan_workflow() {
  LC_ALL=C awk '
    function indent_of(s) { match(s, /^ */); return RLENGTH }
    function unquote(s) { gsub(/^[ \t\047"]+|[ \t\047"]+$/, "", s); return s }
    function emit_list(s,   n, i, parts) {
      gsub(/^[ \t]*\[|\][ \t]*$/, "", s)
      n = split(s, parts, ",")
      for (i = 1; i <= n; i++) if (unquote(parts[i]) != "") print "B " unquote(parts[i])
    }
    {
      if (NR == 1) sub(/^\357\273\277/, "")
      sub(/\r$/, "")
      line = $0
      sub(/[ \t]+#.*$/, "", line)
      if (line ~ /^[ \t]*(#|$)/) next
      ind = indent_of(line)
      if (ind == 0) {
        in_on = 0; in_push = 0; in_branches = 0; event_ind = -1
        if (line ~ /^["\047]?on["\047]?:[ \t]*$/) { in_on = 1; next }
        if (line ~ /^["\047]?on["\047]?:/ && line ~ /release/) print "R"
        next
      }
      if (!in_on) next
      if (event_ind < 0) event_ind = ind
      if (ind <= event_ind) {
        in_push = 0; in_branches = 0
        if (line ~ /^ *push:/) { in_push = 1; push_ind = ind }
        else if (line ~ /^ *release:/) print "R"
        next
      }
      if (!in_push) next
      if (in_branches && line ~ /^ *- / && ind >= branches_ind) {
        item = line
        sub(/^ *- */, "", item)
        if (unquote(item) != "") print "B " unquote(item)
        next
      }
      in_branches = 0
      if (line ~ /^ *branches:/) {
        rest = line
        sub(/^ *branches:[ \t]*/, "", rest)
        if (rest == "") { in_branches = 1; branches_ind = ind }
        else emit_list(rest)
      } else if (line ~ /^ *tags:/) {
        print "T"
      }
    }
  ' "$1"
}

workflow_files() {
  local dir="${ROOT}/.github/workflows"
  [[ -d "$dir" ]] || return 0
  find "$dir" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) | LC_ALL=C sort
}

detect_ci() {
  local file scan branches="" publish=""
  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    scan="$(scan_workflow "$file")"
    branches="${branches}$(printf '%s\n' "$scan" | sed -n 's/^B //p')"$'\n'
    if [[ -z "$publish" ]] && grep -qE '^(T|R)$' <<<"$scan"; then
      publish="${file#"${ROOT}"/}"
    fi
  done < <(workflow_files)
  CI_TRIGGER_BRANCHES="$(printf '%s' "$branches" | awk 'NF && !seen[$0]++' | join_lines)"
  PUBLISH_WORKFLOW="$publish"
}

# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

json_has_version() {
  grep -Eq '"version"[[:space:]]*:' "$1"
}

toml_has_version() {
  grep -Eq '^[[:space:]]*version[[:space:]]*=' "$1"
}

# emit_if_versioned <rel> <checker> — print rel when the file exists and the
# checker finds a version field in it.
emit_if_versioned() {
  if [[ -f "${ROOT}/$1" ]] && "$2" "${ROOT}/$1"; then
    echo "$1"
  fi
}

detect_version_files() {
  local rel dir
  # Built from parts so the bundle generators, which rewrite the literal
  # manifest directory name for each target, leave this detection intact.
  local manifest_dir=".claude"
  manifest_dir="${manifest_dir}-plugin"
  {
    emit_if_versioned package.json json_has_version
    emit_if_versioned cli/package.json json_has_version
    emit_if_versioned Cargo.toml toml_has_version
    emit_if_versioned pyproject.toml toml_has_version
    emit_if_versioned "${manifest_dir}/plugin.json" json_has_version
    emit_if_versioned "${manifest_dir}/marketplace.json" json_has_version
    for dir in "${ROOT}"/*/; do
      [[ -d "$dir" ]] || continue
      rel="${dir#"${ROOT}"/}${manifest_dir}/plugin.json"
      emit_if_versioned "$rel" json_has_version
    done
  } | LC_ALL=C sort -u | join_lines
}

detect_changelog() {
  local rel
  for rel in CHANGELOG.md docs/CHANGELOG.md; do
    if [[ -f "${ROOT}/${rel}" ]]; then
      echo "$rel"
      return
    fi
  done
}

# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

detect_sync_merges() {
  local base="$1" names
  names="main|master|trunk|develop|${DEFAULT_BRANCH//./\\.}"
  g log --first-parent -n "$RECENT_COMMITS" --format='%P%x09%s' "$base" 2>/dev/null |
    awk -F'\t' 'split($1, parents, " ") >= 2 { print $2 }' |
    grep -Eic "Merge (remote-tracking )?branch '?(${names})'? into|sync[ _/-].*[ _/-]into([ _/-]|\$)" ||
    true
}

detect_linear_refs() {
  local base="$1" key_re='(^|[^A-Za-z0-9])[A-Z]{2,}-[0-9]+' deny_re='(CVE|UTF|ISO|RFC|SHA|PEP)-[0-9]'
  # Capture each stage and match with here-strings, not a pipeline ending in
  # `grep -q`: under pipefail the early-exiting reader SIGPIPEs the writer on
  # repos with many refs and a present key reads as absent (YAN-1587).
  local names filtered
  names="$({
    g log -n "$RECENT_COMMITS" --format='%s' "$base" 2>/dev/null
    g for-each-ref --format='%(refname:short)' refs/heads refs/remotes
  } || true)"
  filtered="$(grep -Ev "$deny_re" <<<"$names" || true)"
  if grep -Eq "$key_re" <<<"$filtered"; then
    echo 1
  else
    echo 0
  fi
}

# ---------------------------------------------------------------------------
# Suggestion
# ---------------------------------------------------------------------------

suggest() {
  if [[ -n "$OLDER_LINE_PATCH" ]]; then
    SUGGESTED_MODEL="release-branches"
    SUGGESTED_REASON="${OLDER_LINE_PATCH%% *} was tagged after ${OLDER_LINE_PATCH#* }, so an older release line already receives patches."
  elif [[ -n "$RELEASE_BRANCHES" ]]; then
    SUGGESTED_MODEL="release-branches"
    SUGGESTED_REASON="Maintenance branches already exist (${RELEASE_BRANCHES//,/, })."
  elif [[ "$TAGS_COUNT" -eq 0 || -z "$LATEST_TAG" ]]; then
    SUGGESTED_MODEL="trunk-only"
    SUGGESTED_REASON="No release tags yet, so every release can come from ${DEFAULT_BRANCH}."
  else
    SUGGESTED_MODEL="trunk-only"
    SUGGESTED_REASON="All ${TAGS_COUNT} tags were cut in order from one line up to ${LATEST_TAG}, and no older line was patched."
  fi
}

# ---------------------------------------------------------------------------

DEFAULT_BRANCH="$(detect_default_branch)"
BASE_REF="$(default_ref)"
LONG_LIVED="$(detect_long_lived_branches "$BASE_REF")"
TAGS_COUNT="$(g tag | wc -l | tr -d ' ')"
LATEST_TAG="$(detect_latest_tag)"
TAG_PATTERN="$(detect_tag_pattern "$LATEST_TAG")"
OLDER_LINE_PATCH="$(detect_older_line_patch)"
RELEASE_BRANCHES="$(detect_release_branches)"
GH_RELEASES="$(detect_gh_releases)"
CI_TRIGGER_BRANCHES=""
PUBLISH_WORKFLOW=""
detect_ci
VERSION_FILES="$(detect_version_files)"
CHANGELOG="$(detect_changelog)"
SYNC_MERGES="$(detect_sync_merges "$BASE_REF")"
LINEAR_REFS="$(detect_linear_refs "$BASE_REF")"
SUGGESTED_MODEL=""
SUGGESTED_REASON=""
suggest

echo "default_branch=${DEFAULT_BRANCH}"
echo "long_lived_branches=${LONG_LIVED}"
echo "tags_count=${TAGS_COUNT}"
echo "latest_tag=${LATEST_TAG}"
echo "tag_pattern=${TAG_PATTERN}"
echo "older_line_patched=$([[ -n "$OLDER_LINE_PATCH" ]] && echo 1 || echo 0)"
echo "release_branches=${RELEASE_BRANCHES}"
echo "gh_releases=${GH_RELEASES}"
echo "ci_trigger_branches=${CI_TRIGGER_BRANCHES}"
echo "publish_workflow=${PUBLISH_WORKFLOW}"
echo "version_files=${VERSION_FILES}"
echo "changelog=${CHANGELOG}"
echo "sync_merges_recent=${SYNC_MERGES}"
echo "linear_refs=${LINEAR_REFS}"
echo "suggested_model=${SUGGESTED_MODEL}"
echo "suggested_reason=${SUGGESTED_REASON}"
