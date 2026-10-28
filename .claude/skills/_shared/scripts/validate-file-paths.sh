#!/usr/bin/env bash
# Validate that file paths referenced in a markdown section exist on disk
# Usage: validate-file-paths.sh <markdown-file> [options]
#
# Options:
#   --section "Name"    Section heading to extract paths from (default: "Files to Change")
#   --format type       Path extraction format: table|list|backtick (default: table)
#   --skip-create       Skip paths in table rows where Action column says CREATE
#
# Exit codes:
#   0 - All non-skipped paths are valid
#   1 - One or more paths not found
#
# Can be sourced by other scripts for the validate_file_paths() function,
# or run standalone.
#
# Supports monorepo configurations via .plans-config file.
# See resolve-plans-dir.sh for configuration options.

set -euo pipefail

# Source the shared resolver to get PLANS_ROOT for path resolution
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

if [[ -f "${SCRIPT_DIR}/resolve-plans-dir.sh" ]]; then
  # shellcheck source=resolve-plans-dir.sh
  source "${SCRIPT_DIR}/resolve-plans-dir.sh"
fi

# Use PLANS_ROOT as base for validating file paths, fallback to current directory
VALIDATE_ROOT="${PLANS_ROOT:-$(pwd)}"

# Colors for output (only if stderr/stdout is a terminal)
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  YELLOW='\033[0;33m'
  GREEN='\033[0;32m'
  NC='\033[0m'
else
  RED=''
  YELLOW=''
  GREEN=''
  NC=''
fi

# Strip leading frontmatter before section extraction (identity metadata is
# not body; see ../references/artifact-store.md, Frontmatter).
# shellcheck source=artifact-body.sh
source "${SCRIPT_DIR}/artifact-body.sh"

# Extract a section from a markdown file (between ## heading and next ##)
_extract_section() {
  local file="$1"
  local section="$2"
  artifact_body "$file" | sed -n "/^## ${section}/,/^## /p" | head -n -1
}

# Extract paths from a markdown table (backtick-quoted first column)
_extract_table_paths() {
  local section_content="$1"
  local skip_create="$2"

  # Skip separator rows (dashes only between pipes)
  echo "$section_content" | grep "^|" | grep -v "^|[[:space:]]*-" | while IFS= read -r line; do
    # Find the first backtick-quoted path in ANY column
    local path
    path=$(grep -oE '`[^`]+/[^`]+`' <<<"$line" | head -1 | sed 's/`//g')

    # If no backtick path with slash found, try first column without backticks
    if [[ -z "$path" ]]; then
      local first_col
      first_col=$(echo "$line" | awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2}')
      # Only treat as a path if it contains a slash or dot-extension
      if [[ "$first_col" == */* || "$first_col" =~ \.[a-zA-Z]+$ ]]; then
        path="$first_col"
      fi
    fi

    # Skip if no path-like value found (filters out header rows naturally)
    [[ -z "$path" ]] && continue

    if [[ "$skip_create" == "true" ]]; then
      # Match with a here-string, not `echo | grep -q`: under pipefail an
      # early-exiting reader SIGPIPEs the echo writer and a present CREATE
      # reads as missing (YAN-1587).
      if grep -qi "CREATE" <<<"$line"; then
        echo "SKIP:$path"
        continue
      fi
    fi

    echo "$path"
  done
}

# Extract paths from list items (- path or - `path`)
_extract_list_paths() {
  local section_content="$1"

  echo "$section_content" | grep -E "^- " | sed -E 's/^- `?([^`,:]+)`?[,:]?.*/\1/' | sed 's/[[:space:]]*$//'
}

# Extract backtick-quoted paths from any content
_extract_backtick_paths() {
  local section_content="$1"

  echo "$section_content" | grep -oE '`[^`]+\.[a-zA-Z]+`' | sed 's/`//g' | grep -E '/' | sort -u
}

# Main validation function — can be called when sourced
validate_file_paths() {
  local file="$1"
  local section="${2:-Files to Change}"
  local format="${3:-table}"
  local skip_create="${4:-false}"

  local valid=0
  local invalid=0
  local skipped=0

  # Extract section content
  local section_content
  section_content=$(_extract_section "$file" "$section")

  if [[ -z "$section_content" ]]; then
    echo -e "${YELLOW}WARNING${NC}: Section '## ${section}' not found in $file"
    echo "VALID=0 INVALID=0 SKIPPED=0"
    return 0
  fi

  # Extract paths based on format
  local paths=""
  case "$format" in
    table)
      paths=$(_extract_table_paths "$section_content" "$skip_create")
      ;;
    list)
      paths=$(_extract_list_paths "$section_content")
      ;;
    backtick)
      paths=$(_extract_backtick_paths "$section_content")
      ;;
    *)
      echo -e "${RED}ERROR${NC}: Unknown format: $format (expected: table|list|backtick)"
      return 1
      ;;
  esac

  if [[ -z "$paths" ]]; then
    echo -e "${YELLOW}WARNING${NC}: No file paths found in '## ${section}'"
    echo "VALID=0 INVALID=0 SKIPPED=0"
    return 0
  fi

  while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue

    # Handle skipped CREATE paths
    if [[ "$entry" == SKIP:* ]]; then
      local skip_path="${entry#SKIP:}"
      echo -e "  ${YELLOW}SKIP${NC} $skip_path (CREATE — not expected to exist yet)"
      skipped=$((skipped + 1))
      continue
    fi

    local path="$entry"

    # Resolve path
    local full_path
    if [[ "$path" == /* ]]; then
      full_path="$path"
    else
      full_path="${VALIDATE_ROOT}/${path}"
    fi

    if [[ -f "$full_path" ]] || [[ -d "$full_path" ]]; then
      echo -e "  ${GREEN}✓${NC} $path"
      valid=$((valid + 1))
    else
      echo -e "  ${RED}✗${NC} $path (not found: $full_path)"
      invalid=$((invalid + 1))
    fi
  done <<< "$paths"

  echo ""
  echo "VALID=$valid INVALID=$invalid SKIPPED=$skipped"

  if [[ $invalid -gt 0 ]]; then
    return 1
  fi
  return 0
}

# If run as standalone script (not sourced)
if [[ "${BASH_SOURCE[0]:-}" == "${0:-}" ]]; then
  MARKDOWN_FILE=""
  SECTION="Files to Change"
  FORMAT="table"
  SKIP_CREATE="false"

  # Parse arguments
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --section)
        SECTION="$2"
        shift 2
        ;;
      --format)
        FORMAT="$2"
        shift 2
        ;;
      --skip-create)
        SKIP_CREATE="true"
        shift
        ;;
      --help|-h)
        echo "Usage: validate-file-paths.sh <markdown-file> [options]"
        echo ""
        echo "Options:"
        echo "  --section \"Name\"    Section heading (default: \"Files to Change\")"
        echo "  --format type       Path format: table|list|backtick (default: table)"
        echo "  --skip-create       Skip CREATE entries in table format"
        echo "  --help              Show this help"
        exit 0
        ;;
      -*)
        echo "Unknown option: $1"
        exit 1
        ;;
      *)
        MARKDOWN_FILE="$1"
        shift
        ;;
    esac
  done

  if [[ -z "$MARKDOWN_FILE" ]]; then
    echo "ERROR: Markdown file path required"
    echo "Usage: validate-file-paths.sh <markdown-file> [--section \"Name\"] [--format table|list|backtick] [--skip-create]"
    exit 1
  fi

  # Resolve relative paths against PLANS_ROOT
  if [[ "$MARKDOWN_FILE" != /* && -n "${PLANS_ROOT:-}" ]]; then
    MARKDOWN_FILE="${PLANS_ROOT}/${MARKDOWN_FILE}"
  fi

  if [[ ! -f "$MARKDOWN_FILE" ]]; then
    echo -e "${RED}ERROR${NC}: File not found: $MARKDOWN_FILE"
    exit 1
  fi

  echo "Checking file paths in '## ${SECTION}' of $(basename "$MARKDOWN_FILE")"
  echo ""
  validate_file_paths "$MARKDOWN_FILE" "$SECTION" "$FORMAT" "$SKIP_CREATE"
fi
