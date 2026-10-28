#!/usr/bin/env bash
# check-release-branch.sh — verify that a version is being released from the
# branch the repo's RELEASING.md names for it.
#
# Usage:
#   check-release-branch.sh <version> [--repo DIR] [--branch NAME]
#
# Arguments:
#   version        X.Y.Z or X.Y.Z-pre (a leading "v" and "+build" are accepted)
#
# Options:
#   --repo DIR     repository to check (default: current directory)
#   --branch NAME  branch being released (default: git rev-parse --abbrev-ref HEAD)
#
# Rules (see the shared branching-model reference, "Models"):
#   no RELEASING.md   no check; a note on stderr suggests /release-model
#   trunk-only        every release is cut from the trunk
#   release-branches  X.Y.0 (minor/major, incl. pre-releases) from the trunk;
#                     X.Y.Z with Z > 0 from release/X.Y, which must be listed
#                     under maintenance (a frozen branch gets no patches)
#
# Output (stdout, key=value) on success:
#   present model version kind branch trunk creates_maintenance
#   backport_label forward_port
#   kind is major (Y=0 and Z=0), minor (Z=0) or patch (Z>0).
#   creates_maintenance is release/X.Y for a final X.Y.0 under release-branches.
#   forward_port=1 for a patch under release-branches.
#
# Exit codes:
#   0  the branch is right for this version (or there is no RELEASING.md)
#   1  usage error, or wrong branch (fix commands printed on stderr)
#   2  RELEASING.md exists but its state block is malformed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_SCRIPTS="${SCRIPT_DIR}/../../_shared/scripts"
STATE_SCRIPT="${SHARED_SCRIPTS}/release-state.sh"

usage() {
  sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
}

_error() {
  echo "check-release-branch: $*" >&2
}

REPO_DIR="."
BRANCH=""
VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -lt 2 ]] && { _error "--repo requires a directory"; exit 1; }
      REPO_DIR="$2"
      shift 2
      ;;
    --branch)
      [[ $# -lt 2 ]] && { _error "--branch requires a name"; exit 1; }
      BRANCH="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    -*)
      _error "unknown option: $1"
      usage
      exit 1
      ;;
    *)
      if [[ -n "$VERSION" ]]; then
        _error "unexpected argument: $1"
        exit 1
      fi
      VERSION="$1"
      shift
      ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  _error "a version is required"
  usage
  exit 1
fi
if [[ ! -d "$REPO_DIR" ]]; then
  _error "no such directory: $REPO_DIR"
  exit 1
fi
if [[ ! -f "$STATE_SCRIPT" ]]; then
  _error "release-state.sh not found at ${STATE_SCRIPT}"
  exit 1
fi
UPDATE_SCRIPT="$(cd "$SHARED_SCRIPTS" && pwd)/release-state-update.sh"
# Single-quoted: the Codex generator rewrites this to its own skill syntax, which
# must stay literal rather than expand as a shell variable.
RELEASE_MODEL_CMD='/release-model'

VERSION_RE='^v?([0-9]+)\.([0-9]+)\.([0-9]+)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$'
if [[ ! "$VERSION" =~ $VERSION_RE ]]; then
  _error "'$VERSION' is not a semver version (X.Y.Z or X.Y.Z-pre)"
  exit 1
fi
MAJOR="${BASH_REMATCH[1]}"
MINOR="${BASH_REMATCH[2]}"
PATCH="${BASH_REMATCH[3]}"
PRERELEASE="${BASH_REMATCH[4]}"
VERSION="${VERSION#v}"
LINE="${MAJOR}.${MINOR}"
LINE_BRANCH="release/${LINE}"

# Semver numbers compare numerically: 0.05.1 is 0.5.1.
if ((10#$PATCH > 0)); then
  KIND="patch"
elif ((10#$MINOR == 0)); then
  KIND="major"
else
  KIND="minor"
fi

if [[ -z "$BRANCH" ]]; then
  if ! BRANCH="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)"; then
    _error "cannot read the current branch in ${REPO_DIR} (not a git repo, or no commits)"
    exit 1
  fi
fi

STATE_RC=0
STATE="$(bash "$STATE_SCRIPT" --repo "$REPO_DIR" get)" || STATE_RC=$?
if [[ "$STATE_RC" -ne 0 ]]; then
  # release-state.sh already printed the parse error on stderr.
  [[ "$STATE_RC" -eq 2 ]] && _error "fix RELEASING.md before releasing (${RELEASE_MODEL_CMD} --audit)"
  exit "$STATE_RC"
fi

# state_value <key> — value of key=value in the release-state output.
state_value() {
  printf '%s\n' "$STATE" | sed -n "s/^$1=//p"
}

# in_list <comma-list> <item> — true when item is an entry of the list.
in_list() {
  case ",$1," in
    *",$2,"*) return 0 ;;
    *) return 1 ;;
  esac
}

# emit <model> <trunk> <creates_maintenance> <backport_label> <forward_port>
emit() {
  echo "present=$([[ -n "$1" ]] && echo 1 || echo 0)"
  echo "model=$1"
  echo "version=${VERSION}"
  echo "kind=${KIND}"
  echo "branch=${BRANCH}"
  echo "trunk=$2"
  echo "creates_maintenance=$3"
  echo "backport_label=$4"
  echo "forward_port=$5"
}

# refuse <reason> <fix-command>... — print the reason and the fix, exit 1.
refuse() {
  local reason="$1"
  shift
  _error "$reason"
  if [[ $# -gt 0 ]]; then
    echo "Fix:" >&2
    printf '  %s\n' "$@" >&2
  fi
  exit 1
}

if [[ "$(state_value present)" != "1" ]]; then
  echo "check-release-branch: no RELEASING.md; releasing from '${BRANCH}' without a branch check. Run ${RELEASE_MODEL_CMD} to record the release model." >&2
  emit "" "" "" "" 0
  exit 0
fi

MODEL="$(state_value model)"
TRUNK="$(state_value trunk)"
MAINTENANCE="$(state_value maintenance)"
FROZEN="$(state_value frozen)"
LABEL_TEMPLATE="$(state_value backport_label)"

if [[ "$MODEL" == "trunk-only" ]]; then
  if [[ "$BRANCH" != "$TRUNK" ]]; then
    refuse "trunk-only: every release is cut from '${TRUNK}', but the branch is '${BRANCH}'" \
      "git switch ${TRUNK}" \
      "git pull --ff-only origin ${TRUNK}"
  fi
  emit "$MODEL" "$TRUNK" "" "" 0
  exit 0
fi

# release-branches
LABEL="${LABEL_TEMPLATE//\{X.Y\}/$LINE}"

if [[ "$KIND" != "patch" ]]; then
  if [[ "$BRANCH" != "$TRUNK" ]]; then
    refuse "release-branches: ${KIND} release ${VERSION} is cut from the trunk '${TRUNK}', not '${BRANCH}'" \
      "git switch ${TRUNK}" \
      "git pull --ff-only origin ${TRUNK}"
  fi
  CREATES=""
  if [[ -z "$PRERELEASE" ]]; then
    if in_list "$MAINTENANCE" "$LINE_BRANCH" || in_list "$FROZEN" "$LINE_BRANCH"; then
      refuse "${LINE_BRANCH} is already recorded in RELEASING.md, so ${LINE}.0 has shipped; a fix for ${LINE} is a patch ${LINE}.<n> cut from ${LINE_BRANCH}"
    fi
    CREATES="$LINE_BRANCH"
  fi
  emit "$MODEL" "$TRUNK" "$CREATES" "$LABEL" 0
  exit 0
fi

# release-branches patch: must come from a maintained release/X.Y.
if in_list "$FROZEN" "$LINE_BRANCH"; then
  refuse "${LINE_BRANCH} is frozen (outside the support window in RELEASING.md); it receives no patches. Ship the fix in the next release from '${TRUNK}', or re-open the branch deliberately with ${UPDATE_SCRIPT} --add-maintenance ${LINE_BRANCH} (reviewed, in its own PR)."
fi
if ! in_list "$MAINTENANCE" "$LINE_BRANCH"; then
  refuse "${LINE_BRANCH} is not a maintenance branch in RELEASING.md (maintenance: ${MAINTENANCE:-none}). If ${LINE}.0 shipped before the repo adopted release-branches, create the branch from its tag and record it (in a reviewed PR):" \
    "git push origin v${LINE}.0^{commit}:refs/heads/${LINE_BRANCH}" \
    "${UPDATE_SCRIPT} --add-maintenance ${LINE_BRANCH}"
fi
if [[ "$BRANCH" != "$LINE_BRANCH" ]]; then
  refuse "release-branches: patch ${VERSION} is cut from '${LINE_BRANCH}', not '${BRANCH}'" \
    "git fetch origin" \
    "git switch ${LINE_BRANCH}" \
    "git pull --ff-only origin ${LINE_BRANCH}"
fi

emit "$MODEL" "$TRUNK" "" "$LABEL" 1
