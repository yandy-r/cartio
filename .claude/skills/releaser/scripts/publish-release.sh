#!/usr/bin/env bash
# publish-release.sh — Publish or update a release for a given tag.
#
# Provider-aware: routes through the CLI that matches the detected forge.
#   github          → gh release create/edit  (behavior unchanged)
#   forgejo / gitea → tea release create/edit
#   gitlab / unknown→ release creation is not supported here; exits 1 with the
#                     manual command to run.
#
# Usage:
#   publish-release.sh [--mode=create|edit|auto] [--confirm] <tag> <notes-file>
#
# By default (no --confirm) prints the resolved command and exits 0.
# With --confirm the resolved command is executed.
set -euo pipefail

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"

# --- forge detection -------------------------------------------------------
# Detect the forge provider and select the matching CLI (gh / tea). Prefer the
# plugin-root install path; fall back to the source-tree relative path.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
FORGE_LIB="${SCRIPT_DIR}/../../_shared/scripts/lib/forge.sh"
[[ -f "$FORGE_LIB" ]] || FORGE_LIB=".claude/skills/_shared/scripts/lib/forge.sh"
# shellcheck source=/dev/null
source "$FORGE_LIB"

# --- diagnostic helpers ---
fail()  { echo "${SCRIPT_NAME}: FAIL: $*" >&2; }
warn()  { echo "${SCRIPT_NAME}: WARN: $*" >&2; }
hint()  { echo "${SCRIPT_NAME}: HINT: $*" >&2; }

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} [options] <tag> <notes-file>

Publish (create or update) a release for <tag> using <notes-file> as the
release body. The forge provider is auto-detected from origin (GitHub via gh,
Forgejo/Gitea via tea).

Arguments:
  <tag>          Git tag (e.g. v1.4.0 or 1.4.0 — normalised to v-prefix if semver)
  <notes-file>   Path to a readable Markdown file with release notes

Options:
  --mode=create  Create a new release (fails if release already exists)
  --mode=edit    Edit an existing release (fails if release does not exist)
  --mode=auto    Auto-detect: create if missing, edit if present (default)
  --confirm      Actually run the resolved command (default: print only)
  -h, --help     Show this help

Print-only mode (no --confirm):
  Prints the exact command that would be run, then exits 0. The forge CLI is
  NOT called.

Authentication:
  In --mode=auto, the detected forge CLI's auth state is checked. If
  unauthenticated, the script exits 1 with a warning. In --mode=create or
  --mode=edit, the CLI itself surfaces authentication errors when --confirm
  is used.

Examples:
  # Preview what would be run for tag v1.4.0:
  ${SCRIPT_NAME} v1.4.0 docs/releases/v1.4.0.md

  # Auto-detect and actually publish:
  ${SCRIPT_NAME} --mode=auto --confirm v1.4.0 docs/releases/v1.4.0.md

  # Force-create:
  ${SCRIPT_NAME} --mode=create --confirm v1.4.0 docs/releases/v1.4.0.md

  # Update an existing release:
  ${SCRIPT_NAME} --mode=edit --confirm v1.4.0 docs/releases/v1.4.0.md
EOF
}

# --- tag normalisation ---
# Prepend 'v' if the input looks like semver (digits.digits...) but lacks the prefix.
normalize_tag() {
    local input="$1"
    if [[ "${input}" =~ ^[0-9]+\.[0-9]+ ]]; then
        printf 'v%s\n' "${input}"
    else
        printf '%s\n' "${input}"
    fi
}

# --- argument parsing ---
MODE="auto"
CONFIRM=0
TAG_RAW=""
NOTES_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --mode=*)
            MODE="${1#--mode=}"
            case "${MODE}" in
                create|edit|auto) ;;
                *)
                    fail "unknown mode '${MODE}' — must be create, edit, or auto"
                    usage >&2
                    exit 1
                    ;;
            esac
            shift
            ;;
        --confirm)
            CONFIRM=1
            shift
            ;;
        -*)
            fail "unknown option: $1"
            usage >&2
            exit 1
            ;;
        *)
            if [[ -z "${TAG_RAW}" ]]; then
                TAG_RAW="$1"
            elif [[ -z "${NOTES_FILE}" ]]; then
                NOTES_FILE="$1"
            else
                fail "unexpected argument: $1"
                usage >&2
                exit 1
            fi
            shift
            ;;
    esac
done

# --- required-argument validation ---
if [[ -z "${TAG_RAW}" ]]; then
    fail "<tag> is required"
    usage >&2
    exit 1
fi

if [[ -z "${NOTES_FILE}" ]]; then
    fail "<notes-file> is required"
    usage >&2
    exit 1
fi

# --- tag normalisation ---
TAG="$(normalize_tag "${TAG_RAW}")"

# --- notes file readability guard ---
if [[ ! -r "${NOTES_FILE}" ]]; then
    fail "notes file not readable: ${NOTES_FILE}"
    exit 1
fi

# --- verify tag exists as a real tag ref (not a branch/commit named like the tag) ---
if ! git show-ref --verify --quiet "refs/tags/${TAG}"; then
    fail "tag ${TAG} does not exist in this repo"
    hint "create the tag first: git tag ${TAG} && git push origin ${TAG}"
    exit 1
fi

# --- provider routing -------------------------------------------------------
# Resolve the forge provider for origin and reject providers this script cannot
# create releases for. github keeps its exact prior semantics; forgejo/gitea
# route through tea; gitlab/unknown exit with the manual command.
PROVIDER="$(forge_detect_provider origin)"
FORGE_CLI="$(forge_cli "${PROVIDER}")"

case "${PROVIDER}" in
    github|forgejo|gitea)
        ;;  # supported for release create/edit
    gitlab)
        fail "GitLab release creation is not supported by this script"
        hint "create the release manually: glab release create ${TAG} --notes-file ${NOTES_FILE}"
        exit 1
        ;;
    *)
        fail "could not determine the forge provider for 'origin'"
        hint "origin = $(forge_remote_url origin)"
        hint "supported: GitHub (gh), Forgejo/Gitea (tea)"
        exit 1
        ;;
esac

# --- forge CLI availability check ---
if ! command -v "${FORGE_CLI}" >/dev/null 2>&1; then
    fail "${FORGE_CLI} CLI (for ${PROVIDER}) is not installed"
    case "${PROVIDER}" in
        github) hint "install gh:  https://github.com/cli/cli#installation" ;;
        *)      hint "install tea: https://gitea.com/gitea/tea#installation" ;;
    esac
    exit 1
fi

# --- authentication check (auto mode only) ---
if [[ "${MODE}" == "auto" ]]; then
    if ! forge_auth_ok "${PROVIDER}"; then
        warn "${FORGE_CLI} not authenticated (${PROVIDER}); cannot determine release state — re-run with explicit --mode=create or --mode=edit"
        case "${PROVIDER}" in
            github) hint "authenticate with: gh auth login" ;;
            *)      hint "authenticate with: tea login add" ;;
        esac
        exit 1
    fi
fi

# --- release existence detection ---
# Always probe the forge (including --mode=create) so create-mode can detect an
# existing release and suggest --mode=edit instead of failing later in the CLI.
RELEASE_JSON=""
RELEASE_EXISTS=0
if [[ "${PROVIDER}" == "github" ]]; then
    RELEASE_JSON="$(gh release view "${TAG}" --json url,tagName,body 2>/dev/null || true)"
    if [[ -n "${RELEASE_JSON}" ]]; then
        RELEASE_EXISTS=1
    fi
else
    # tea has no per-tag "view"; list releases as JSON and match the tag_name.
    # Capture the list first and match with a here-string, not
    # `tea ... | grep -q`: under pipefail the early-exiting grep SIGPIPEs tea
    # on a large release list and an existing release reads as absent
    # (YAN-1587).
    TEA_RELEASES="$(tea release list --output json 2>/dev/null || true)"
    if grep -qE "\"tag_name\"[[:space:]]*:[[:space:]]*\"${TAG}\"" <<<"${TEA_RELEASES}"; then
        RELEASE_EXISTS=1
    fi
fi

# --- mode conflict checks ---
case "${MODE}" in
    create)
        if [[ "${RELEASE_EXISTS}" -eq 1 ]]; then
            fail "release already exists for ${TAG}; use --mode=edit to overwrite"
            exit 1
        fi
        ;;
    edit)
        if [[ "${RELEASE_EXISTS}" -eq 0 ]]; then
            fail "cannot edit non-existent release ${TAG}"
            exit 1
        fi
        ;;
    auto)
        if [[ "${RELEASE_EXISTS}" -eq 1 ]]; then
            MODE="edit"
        else
            MODE="create"
        fi
        ;;
esac

# --- build resolved command ---
# github keeps its prior gh commands verbatim. tea (forgejo/gitea) uses
# `tea release create --note-file` for create, but `tea release edit` has no
# --note-file in 0.14.x, so edit passes the notes inline via --note.
if [[ "${PROVIDER}" == "github" ]]; then
    if [[ "${MODE}" == "create" ]]; then
        RESOLVED_CMD="gh release create $(printf '%q' "${TAG}") --notes-file $(printf '%q' "${NOTES_FILE}") --title $(printf '%q' "${TAG}") --verify-tag"
    else
        RESOLVED_CMD="gh release edit $(printf '%q' "${TAG}") --notes-file $(printf '%q' "${NOTES_FILE}")"
    fi
else
    if [[ "${MODE}" == "create" ]]; then
        RESOLVED_CMD="tea release create --tag $(printf '%q' "${TAG}") --title $(printf '%q' "${TAG}") --note-file $(printf '%q' "${NOTES_FILE}")"
    else
        # `tea release edit` lacks --note-file; pass the notes file inline.
        RESOLVED_CMD="tea release edit $(printf '%q' "${TAG}") --note \"\$(cat $(printf '%q' "${NOTES_FILE}"))\""
    fi
fi

# --- print-only mode (no --confirm) ---
if [[ "${CONFIRM}" -eq 0 ]]; then
    echo "${RESOLVED_CMD}"
    exit 0
fi

# --- confirm mode: warn before destructive edit ---
if [[ "${MODE}" == "edit" ]]; then
    if [[ "${PROVIDER}" == "github" ]]; then
        existing_body="$(printf '%s' "${RELEASE_JSON}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("body","") or "")' 2>/dev/null || true)"
        warn "about to overwrite release body for ${TAG}. Current body:"
        printf '%s\n' "---" "${existing_body}" "---" >&2
    else
        warn "about to overwrite release notes for ${TAG} on ${PROVIDER}."
    fi
fi

# --- execute ---
if [[ "${PROVIDER}" == "github" ]]; then
    if [[ "${MODE}" == "create" ]]; then
        gh release create "${TAG}" --notes-file "${NOTES_FILE}" --title "${TAG}" --verify-tag
    else
        gh release edit "${TAG}" --notes-file "${NOTES_FILE}"
    fi
else
    if [[ "${MODE}" == "create" ]]; then
        tea release create --tag "${TAG}" --title "${TAG}" --note-file "${NOTES_FILE}"
    else
        # `tea release edit` lacks --note-file; read the notes file inline.
        NOTES_BODY="$(cat "${NOTES_FILE}")"
        tea release edit "${TAG}" --note "${NOTES_BODY}"
    fi
fi
