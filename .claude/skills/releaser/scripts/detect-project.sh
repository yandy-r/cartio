#!/usr/bin/env bash
# detect-project.sh — Emit a single JSON document describing the release-relevant
# facts about the current repo: language, build system, version-bearing manifests,
# default OS/arch matrix, existing release-ish workflows, current version, and the
# latest git tag.
#
# Output goes to stdout.  Diagnostics go to stderr.  Exit 0 on success even when
# fields are null (the skill handles missing data); exit 1 only for hard failures
# (not inside a git repo, no jq-compatible printing possible).
#
# Usage:
#   detect-project.sh
#
# No arguments.  Runs in the current working directory; must be inside a git repo.

set -euo pipefail

# 1. Must be inside a git repo
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
    echo "detect-project.sh: not inside a git repository" >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "${REPO_ROOT}"

# 2. Helpers: JSON-safe string emitter (basic, sufficient for paths + versions)
json_str() {
    # Escape backslashes and double-quotes, wrap in quotes.  Pass `null` to emit null.
    if [[ "$1" == "__NULL__" ]]; then
        printf 'null'
        return
    fi
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    printf '"%s"' "$s"
}

json_str_array() {
    # $@ are array elements.  Emits ["a","b"].
    local first=1
    printf '['
    for el in "$@"; do
        [[ ${first} -eq 1 ]] || printf ','
        first=0
        json_str "${el}"
    done
    printf ']'
}

# 3. Detect language
LANGUAGE="generic"
BUILD_SYSTEM="none"
MANIFEST_FILES=()

# Order matters: more specific signals first.  Mark "mixed" if several match.
matches=0

if [[ -f "Cargo.toml" ]]; then
    LANGUAGE="rust"
    MANIFEST_FILES+=("Cargo.toml")
    if [[ -f "dist-workspace.toml" ]] || grep -q '\[workspace.metadata.dist\]' Cargo.toml 2>/dev/null; then
        BUILD_SYSTEM="cargo-dist"
    else
        BUILD_SYSTEM="cargo"
    fi
    matches=$((matches + 1))
fi

if [[ -f "go.mod" ]]; then
    [[ ${matches} -gt 0 ]] && LANGUAGE="mixed" || LANGUAGE="go"
    BUILD_SYSTEM="go-modules"
    if [[ -f ".goreleaser.yml" ]] || [[ -f ".goreleaser.yaml" ]]; then
        BUILD_SYSTEM="goreleaser"
    fi
    matches=$((matches + 1))
fi

if [[ -f "package.json" ]]; then
    [[ ${matches} -gt 0 ]] && LANGUAGE="mixed" || LANGUAGE="node"
    MANIFEST_FILES+=("package.json")
    if [[ -f "pnpm-lock.yaml" ]]; then
        BUILD_SYSTEM="pnpm"
    elif [[ -f "yarn.lock" ]]; then
        BUILD_SYSTEM="yarn"
    elif [[ -f "bun.lockb" ]]; then
        BUILD_SYSTEM="bun"
    else
        BUILD_SYSTEM="npm"
    fi
    matches=$((matches + 1))
fi

if [[ -f "pyproject.toml" ]] || [[ -f "setup.py" ]] || [[ -f "setup.cfg" ]]; then
    [[ ${matches} -gt 0 ]] && LANGUAGE="mixed" || LANGUAGE="python"
    [[ -f "pyproject.toml" ]] && MANIFEST_FILES+=("pyproject.toml")
    [[ -f "setup.py" ]] && MANIFEST_FILES+=("setup.py")
    [[ -f "setup.cfg" ]] && MANIFEST_FILES+=("setup.cfg")
    if [[ -f "uv.lock" ]]; then
        BUILD_SYSTEM="uv"
    elif [[ -f "pyproject.toml" ]] && grep -q '\[tool.poetry\]' pyproject.toml 2>/dev/null; then
        BUILD_SYSTEM="poetry"
    elif [[ -f "pyproject.toml" ]] && grep -q '\[tool.hatch\]' pyproject.toml 2>/dev/null; then
        BUILD_SYSTEM="hatch"
    else
        BUILD_SYSTEM="setuptools"
    fi
    matches=$((matches + 1))
fi

# Docker fallback — only if nothing else matched
if [[ ${matches} -eq 0 ]] && [[ -f "Dockerfile" ]]; then
    LANGUAGE="docker"
    BUILD_SYSTEM="docker"
    matches=1
fi

# 4. Default OS / arch by language
case "${LANGUAGE}" in
    node)
        DEFAULT_OS="linux,darwin,windows"
        DEFAULT_ARCH="amd64,arm64"
        ;;
    python)
        DEFAULT_OS="linux"
        DEFAULT_ARCH="amd64"
        ;;
    go|rust)
        DEFAULT_OS="linux,darwin,windows"
        DEFAULT_ARCH="amd64,arm64"
        ;;
    docker)
        DEFAULT_OS="linux"
        DEFAULT_ARCH="amd64,arm64"
        ;;
    mixed|generic|*)
        DEFAULT_OS="linux"
        DEFAULT_ARCH="amd64"
        ;;
esac

# 5. Existing CI workflows (any .yml / .yaml under .github/workflows)
EXISTING_CI=()
if [[ -d ".github/workflows" ]]; then
    while IFS= read -r -d '' wf; do
        EXISTING_CI+=("${wf#./}")
    done < <(find ./.github/workflows -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) -print0 2>/dev/null)
fi

# 6. Current version — extracted from the first manifest that carries one
CURRENT_VERSION="__NULL__"
extract_version_from() {
    local f="$1"
    case "$f" in
        package.json)
            # Grep-based (avoids adding jq dependency).  Match "version": "1.2.3".
            grep -E '^\s*"version"\s*:' "$f" | head -n1 \
                | sed -E 's/.*"version"\s*:\s*"([^"]+)".*/\1/'
            ;;
        pyproject.toml)
            # Match `version = "1.2.3"` in either [project] or [tool.poetry].
            awk '
                /^\[project\]/ { section="project"; next }
                /^\[tool\.poetry\]/ { section="poetry"; next }
                /^\[/ { section=""; next }
                (section=="project" || section=="poetry") && /^version[[:space:]]*=/ {
                    gsub(/.*=[[:space:]]*"/, "");
                    gsub(/".*/, "");
                    print; exit
                }
            ' "$f"
            ;;
        Cargo.toml)
            awk '
                /^\[package\]/ { section="package"; next }
                /^\[workspace\.package\]/ { section="workspace"; next }
                /^\[/ { section=""; next }
                (section=="package" || section=="workspace") && /^version[[:space:]]*=/ {
                    gsub(/.*=[[:space:]]*"/, "");
                    gsub(/".*/, "");
                    print; exit
                }
            ' "$f"
            ;;
        setup.py)
            grep -E 'version\s*=' "$f" | head -n1 \
                | sed -E "s/.*version\s*=\s*['\"]([^'\"]+)['\"].*/\1/"
            ;;
        setup.cfg)
            awk '/^\[metadata\]/ { s=1; next } /^\[/ { s=0 } s && /^version[[:space:]]*=/ { sub(/^version[[:space:]]*=[[:space:]]*/, ""); print; exit }' "$f"
            ;;
    esac
}

for mf in "${MANIFEST_FILES[@]}"; do
    v=$(extract_version_from "$mf" || true)
    if [[ -n "${v:-}" ]]; then
        CURRENT_VERSION="${v}"
        break
    fi
done

# 7. Latest git tag (if any)
LATEST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
[[ -z "${LATEST_TAG}" ]] && LATEST_TAG="__NULL__"

# 8. Emit JSON
{
    printf '{\n'
    printf '  "language": '; json_str "${LANGUAGE}"; printf ',\n'
    printf '  "build_system": '; json_str "${BUILD_SYSTEM}"; printf ',\n'
    printf '  "manifest_files": '
    if [[ ${#MANIFEST_FILES[@]} -eq 0 ]]; then
        printf '[]'
    else
        json_str_array "${MANIFEST_FILES[@]}"
    fi
    printf ',\n'
    printf '  "default_os": '; json_str "${DEFAULT_OS}"; printf ',\n'
    printf '  "default_arch": '; json_str "${DEFAULT_ARCH}"; printf ',\n'
    printf '  "existing_ci": '
    if [[ ${#EXISTING_CI[@]} -eq 0 ]]; then
        printf '[]'
    else
        json_str_array "${EXISTING_CI[@]}"
    fi
    printf ',\n'
    printf '  "current_version": '; json_str "${CURRENT_VERSION}"; printf ',\n'
    printf '  "latest_tag": '; json_str "${LATEST_TAG}"; printf '\n'
    printf '}\n'
}

exit 0
