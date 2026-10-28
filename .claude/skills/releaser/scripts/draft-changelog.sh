#!/usr/bin/env bash
# draft-changelog.sh — Fill the release-notes template with bucketized conventional-commit
# entries for a new release.  Output goes to stdout; callers redirect or pipe as needed.
#
# Usage:
#   draft-changelog.sh [options] <new-version> [<from-ref>]
#
# Arguments:
#   new-version   Semver to title the fragment (e.g. 1.4.0 or v1.4.0).
#   from-ref      Optional "since" ref.  Defaults to the latest tag, or the repo root
#                 commit if no tag exists.
#
# Options:
#   --exclude-internal   Omit the Maintenance <details> block entirely.
#   --template <path>    Path to the Markdown template (default: adjacent references/).
#   -h, --help           Show this help.
#
# Output: filled Markdown on stdout.  Diagnostics on stderr.

set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [options] <new-version> [<from-ref>]

Draft release notes by filling the release-notes template.

Arguments:
  new-version         Semver to title the fragment (e.g. 1.4.0 or v1.4.0).
  from-ref            Optional explicit "since" ref.  Defaults to latest tag or root commit.

Options:
  --exclude-internal  Omit the Maintenance <details> block from the output.
  --template <path>   Markdown template to fill (default: references/release-notes-template.md).
  -h, --help          Show this help.
EOF
}

# ── defaults ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Template is always adjacent to this script under the skill (…/skills/releaser/references/).
# Keep this SCRIPT_DIR-relative path: generated Codex bundles forbid the plugin-root env var
# literal in shell sources (content policy).
TEMPLATE="${SCRIPT_DIR}/../references/release-notes-template.md"

# ── argument parsing ──────────────────────────────────────────────────────────
EXCLUDE_INTERNAL=0
NEW_VERSION=""
FROM_REF=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --exclude-internal)
            EXCLUDE_INTERNAL=1
            shift
            ;;
        --template)
            if [[ $# -lt 2 ]]; then
                echo "draft-changelog.sh: FAIL: --template requires a path argument" >&2
                exit 1
            fi
            TEMPLATE="$2"
            shift 2
            ;;
        --template=*)
            TEMPLATE="${1#--template=}"
            shift
            ;;
        -*)
            echo "draft-changelog.sh: FAIL: unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            if [[ -z "${NEW_VERSION}" ]]; then
                NEW_VERSION="$1"
            elif [[ -z "${FROM_REF}" ]]; then
                FROM_REF="$1"
            else
                echo "draft-changelog.sh: FAIL: unexpected argument: $1" >&2
                usage >&2
                exit 1
            fi
            shift
            ;;
    esac
done

if [[ -z "${NEW_VERSION}" ]]; then
    echo "draft-changelog.sh: FAIL: missing required argument: <new-version>" >&2
    usage >&2
    exit 1
fi

if [[ "${FROM_REF}" == -* ]]; then
    echo "draft-changelog.sh: FAIL: from-ref cannot begin with '-'" >&2
    exit 1
fi

# ── validate template ─────────────────────────────────────────────────────────
if [[ ! -f "${TEMPLATE}" ]]; then
    echo "draft-changelog.sh: FAIL: template not found: ${TEMPLATE}" >&2
    exit 1
fi

# ── git sanity check ──────────────────────────────────────────────────────────
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
    echo "draft-changelog.sh: FAIL: not inside a git repository" >&2
    exit 1
fi

# ── resolve from-ref ──────────────────────────────────────────────────────────
if [[ -z "${FROM_REF}" ]]; then
    PREVIOUS_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
    if [[ -n "${PREVIOUS_TAG}" ]]; then
        FROM_REF="${PREVIOUS_TAG}"
    else
        ROOT_HASH="$(git rev-list --max-parents=0 HEAD 2>/dev/null | head -n1)"
        if [[ -z "${ROOT_HASH}" ]]; then
            echo "draft-changelog.sh: FAIL: could not resolve repository root commit" >&2
            exit 1
        fi
        FROM_REF="${ROOT_HASH}"
        PREVIOUS_TAG="(no previous tag)"
    fi
else
    PREVIOUS_TAG="${FROM_REF}"
fi

# When from-ref is a revision range (two- or three-dot), use only the left endpoint for
# "Previous release" lines and compare URLs (never the raw "A..B" / "A...B" string).
if [[ "${FROM_REF}" == *...* ]]; then
    PREVIOUS_TAG="${FROM_REF%%...*}"
elif [[ "${FROM_REF}" == *..* ]]; then
    PREVIOUS_TAG="${FROM_REF%%..*}"
fi

# Validate the from-ref.  If it is a range, validate the left side only; otherwise
# validate the whole value as a single ref.  Check three-dot ranges before two-dot.
if [[ "${FROM_REF}" == *...* ]]; then
    _left_ref="${FROM_REF%%...*}"
    if [[ -n "${_left_ref}" ]] && \
       ! git rev-parse --verify --quiet "${_left_ref}" >/dev/null 2>&1; then
        echo "draft-changelog.sh: FAIL: ref not found: ${_left_ref} (from range ${FROM_REF})" >&2
        exit 1
    fi
elif [[ "${FROM_REF}" == *..* ]]; then
    _left_ref="${FROM_REF%%..*}"
    if [[ -n "${_left_ref}" ]] && \
       ! git rev-parse --verify --quiet "${_left_ref}" >/dev/null 2>&1; then
        echo "draft-changelog.sh: FAIL: ref not found: ${_left_ref} (from range ${FROM_REF})" >&2
        exit 1
    fi
else
    if ! git rev-parse --verify --quiet "${FROM_REF}" >/dev/null 2>&1; then
        echo "draft-changelog.sh: FAIL: ref not found: ${FROM_REF}" >&2
        exit 1
    fi
fi

# ── normalize version ─────────────────────────────────────────────────────────
TITLE_VERSION="${NEW_VERSION#v}"
DATE="$(date -u +%Y-%m-%d)"

# ── collect commits ───────────────────────────────────────────────────────────
# Build the git revision range.  If FROM_REF already contains "..", treat it as
# the full range expression; otherwise append "..HEAD".
if [[ "${FROM_REF}" == *..* ]]; then
    GIT_RANGE="${FROM_REF}"
else
    GIT_RANGE="${FROM_REF}..HEAD"
fi

# LOG_TMP: NUL-separated records; within each record, tab separates hash/subject/body.
# Kept for breaking-change body detection (body access requires this format).
LOG_TMP="$(mktemp)"
trap 'rm -f "${LOG_TMP}"' EXIT

git log "${GIT_RANGE}" --no-merges --reverse -z \
    --format='%h%x09%s%x09%b' > "${LOG_TMP}"

# ── stub when range is empty ──────────────────────────────────────────────────
COMMITS_ONELINE=""
if [[ ! -s "${LOG_TMP}" ]]; then
    echo "draft-changelog.sh: no commits between ${FROM_REF} and HEAD" >&2
    # Emit minimal stub via template substitution.
    COMMITS_ONELINE="(no commits since ${FROM_REF})"
fi

# ── breaking-change regex constants (preserved from original) ─────────────────
_BREAKING_PREFIX_RE='^[a-z]+(\([^)]*\))?!'
_BREAKING_BODY_RE='BREAKING[[:space:]]CHANGE'

# ── bucketize commits ─────────────────────────────────────────────────────────
# Arrays for each section.
BREAKING_LINES=()
FEATURES_LINES=()
FIXES_LINES=()
INTERNAL_LINES=()
OTHER_LINES=()
ONELINE_LINES=()

while IFS=$'\t' read -r -d '' hash subject body; do
    # Strip a leading '-' from the subject before type/breaking regexes so
    # subjects like "- feat!: foo" classify consistently with "feat!: foo".
    subject="${subject#-}"
    subject="${subject# }"

    # Detect breaking-change markers.
    is_breaking=0
    if [[ "${subject}" =~ ${_BREAKING_PREFIX_RE} ]]; then
        is_breaking=1
    fi
    if [[ "${body}" =~ ${_BREAKING_BODY_RE} ]]; then
        is_breaking=1
    fi
    line="- ${subject} (${hash})"
    ONELINE_LINES+=("${hash} ${subject}")

    # Parse bucket via case (most readable and aligns with original style).
    case "${subject}" in
        feat:*|feat\(*\):*|feat!*|feat\(*\)!:*|\
        perf:*|perf\(*\):*|perf!*|perf\(*\)!:*)
            bucket="features"
            ;;
        fix:*|fix\(*\):*|fix!*|fix\(*\)!:*|\
        revert:*|revert\(*\):*|revert!*|revert\(*\)!:*)
            bucket="fixes"
            ;;
        docs:*|docs\(*\):*|\
        chore:*|chore\(*\):*|\
        test:*|test\(*\):*|\
        build:*|build\(*\):*|\
        ci:*|ci\(*\):*|\
        style:*|style\(*\):*|\
        refactor:*|refactor\(*\):*)
            bucket="internal"
            ;;
        *)
            bucket="other"
            ;;
    esac

    # Breaking changes go into BREAKING_LINES and skip the type bucket to avoid duplication.
    if [[ ${is_breaking} -eq 1 ]]; then
        BREAKING_LINES+=("${line}")
        continue
    fi

    case "${bucket}" in
        features) FEATURES_LINES+=("${line}") ;;
        fixes)    FIXES_LINES+=("${line}") ;;
        internal) INTERNAL_LINES+=("${line}") ;;
        other)    OTHER_LINES+=("${line} (non-conventional)") ;;
    esac
done < "${LOG_TMP}"

# Build COMMITS_ONELINE from the loop (only when not already set by the empty-range stub).
if [[ -z "${COMMITS_ONELINE}" ]]; then
    if [[ ${#ONELINE_LINES[@]} -gt 0 ]]; then
        COMMITS_ONELINE="$(printf '%s\n' "${ONELINE_LINES[@]}")"
    fi
fi

# ── build placeholder values ──────────────────────────────────────────────────

# BREAKING section.
if [[ ${#BREAKING_LINES[@]} -gt 0 ]]; then
    BREAKING_SECTION="$(printf '%s\n' "${BREAKING_LINES[@]}")"
else
    BREAKING_SECTION="_None_"
fi

# FEATURES section.
if [[ ${#FEATURES_LINES[@]} -gt 0 ]]; then
    FEATURES_SECTION="$(printf '%s\n' "${FEATURES_LINES[@]}")"
else
    FEATURES_SECTION="_None_"
fi

# FIXES section.
if [[ ${#FIXES_LINES[@]} -gt 0 ]]; then
    FIXES_SECTION="$(printf '%s\n' "${FIXES_LINES[@]}")"
else
    FIXES_SECTION="_None_"
fi

# INTERNAL section: either a fully-formed <details> block or empty string.
# OTHER commits are folded into INTERNAL with a (non-conventional) suffix.
ALL_INTERNAL_LINES=()
[[ ${#INTERNAL_LINES[@]} -gt 0 ]] && ALL_INTERNAL_LINES+=("${INTERNAL_LINES[@]}")
[[ ${#OTHER_LINES[@]} -gt 0 ]]    && ALL_INTERNAL_LINES+=("${OTHER_LINES[@]}")
INTERNAL_COUNT=${#ALL_INTERNAL_LINES[@]}

if [[ ${EXCLUDE_INTERNAL} -eq 1 ]] || [[ ${INTERNAL_COUNT} -eq 0 ]]; then
    INTERNAL_BLOCK=""
else
    INTERNAL_LIST="$(printf '%s\n' "${ALL_INTERNAL_LINES[@]}")"
    INTERNAL_BLOCK="$(printf '<details><summary>Maintenance (%d commits)</summary>\n\n%s\n\n</details>' \
        "${INTERNAL_COUNT}" "${INTERNAL_LIST}")"
fi

# HIGHLIGHTS stub.
HIGHLIGHTS_STUB="<!-- TODO: top 1–3 things this release delivers -->"

# COMPARE_URL: best-effort from remote.origin.url.
# Only constructed when PREVIOUS_TAG is a real ref (not the sentinel string).
# NOTE: Only GitHub SSH (`git@github.com:`) and HTTPS (`https://github.com/`)
# remotes are normalized to a compare URL. GitLab SSH, Bitbucket, self-hosted,
# and other forge formats are not recognized and will produce an empty
# COMPARE_URL (best-effort / no output). No error is raised for unknown remotes.
COMPARE_URL=""
REMOTE_URL="$(git config --get remote.origin.url 2>/dev/null || true)"
if [[ -n "${REMOTE_URL}" ]] && [[ "${PREVIOUS_TAG}" != "(no previous tag)" ]]; then
    http_url="${REMOTE_URL}"
    http_url="${http_url%.git}"
    http_url="${http_url/git@github.com:/https://github.com/}"
    if [[ "${http_url}" != https://* ]]; then
        COMPARE_URL=""
    else
        COMPARE_URL="${http_url}/compare/${PREVIOUS_TAG}...v${TITLE_VERSION}"
    fi
fi

# ── template substitution via python3 ─────────────────────────────────────────
# {{COMMITS}} is the raw oneline log; the template wraps it in <details>.
# {{INTERNAL}} is either a fully-formed <details> block or empty string.
# NOTE: COMMITS_ONELINE is passed as sys.argv[9]. On repos with thousands of
# commits since the last tag this can be tens of kilobytes. This is bounded by
# ARG_MAX (~2 MB on Linux) so it is not a hard limit, but unusually large repos
# may approach that ceiling. If that becomes a concern, switch to passing via a
# temp file and reading inside the Python block with pathlib.Path.read_text().
python3 - \
    "${TEMPLATE}" \
    "${TITLE_VERSION}" \
    "${DATE}" \
    "${HIGHLIGHTS_STUB}" \
    "${BREAKING_SECTION}" \
    "${FEATURES_SECTION}" \
    "${FIXES_SECTION}" \
    "${INTERNAL_BLOCK}" \
    "${COMMITS_ONELINE}" \
    "${COMPARE_URL}" \
    "${PREVIOUS_TAG}" \
    <<'PY'
import re, sys, pathlib

template_path   = pathlib.Path(sys.argv[1])
version         = sys.argv[2]
date_str        = sys.argv[3]
highlights      = sys.argv[4]
breaking        = sys.argv[5]
features        = sys.argv[6]
fixes           = sys.argv[7]
internal_block  = sys.argv[8]
commits_raw     = sys.argv[9]
compare_url     = sys.argv[10]
previous_tag    = sys.argv[11]

text = template_path.read_text()
text = (text
        .replace("{{VERSION}}",      version)
        .replace("{{DATE}}",         date_str)
        .replace("{{HIGHLIGHTS}}",   highlights)
        .replace("{{BREAKING}}",     breaking)
        .replace("{{FEATURES}}",     features)
        .replace("{{FIXES}}",        fixes)
        .replace("{{INTERNAL}}",     internal_block)
        .replace("{{COMMITS}}",      commits_raw)
        .replace("{{COMPARE_URL}}",  compare_url)
        .replace("{{PREVIOUS_TAG}}", previous_tag)
       )
# When INTERNAL is empty, the template leaves a blank line before and after the
# placeholder site.  Collapse runs of 3+ newlines to 2 (one blank line).
text = re.sub(r'\n{3,}', '\n\n', text)
if compare_url == "":
    text = re.sub(r'\n*<!-- COMPARE_URL_LINE -->.*?<!-- /COMPARE_URL_LINE -->\n*', '\n', text, flags=re.DOTALL)
else:
    text = text.replace("<!-- COMPARE_URL_LINE -->", "").replace("<!-- /COMPARE_URL_LINE -->", "")
print(text, end="")
PY

exit 0
