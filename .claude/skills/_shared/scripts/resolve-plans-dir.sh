#!/usr/bin/env bash
# Resolve the plans directory for the current project
# Usage: source resolve-plans-dir.sh && echo "$PLANS_DIR"
#
# This script resolves where plan documents should be stored by:
# 1. Looking for a .plans-config file walking up the directory tree
# 2. Falling back to git root detection
# 3. Defaulting to current directory if neither found
#
# Configuration file format (.plans-config):
#   plans_dir: docs/plans    # Relative path from config file location
#   scope: local             # Optional: 'local' means use this directory as base
#                            # Without scope, continues searching up for root config
#
# Exports:
#   PLANS_DIR - Absolute path to the plans directory (e.g., /path/to/repo/docs/plans)
#   PLANS_ROOT - Absolute path to the project root (where .plans-config or .git is)

set -uo pipefail

# Colors for output (only if stderr is a terminal)
if [[ -t 2 ]]; then
  RED='\033[0;31m'
  YELLOW='\033[0;33m'
  GREEN='\033[0;32m'
  BLUE='\033[0;34m'
  NC='\033[0m'
else
  RED=''
  YELLOW=''
  GREEN=''
  BLUE=''
  NC=''
fi
# Sourcing scripts (validate-*.sh, check-*.sh) rely on these for their own output.
export RED YELLOW GREEN BLUE NC

_debug() {
  if [[ "${PLANS_DEBUG:-}" == "1" ]]; then
    echo -e "${BLUE}[resolve-plans-dir]${NC} $1" >&2
  fi
}

_warn() {
  echo -e "${YELLOW}WARNING${NC}: $1" >&2
}

_error() {
  echo -e "${RED}ERROR${NC}: $1" >&2
}

# Parse a simple YAML-like config file
# Usage: _parse_config "key" "/path/to/.plans-config"
_parse_config() {
  local key="$1"
  local config_file="$2"

  if [[ ! -f "$config_file" ]]; then
    return 1
  fi

  # Simple parsing: look for "key: value" or "key=value"
  local value
  value=$(grep -E "^${key}[[:space:]]*[:=][[:space:]]*" "$config_file" 2>/dev/null | head -1 | sed -E 's/^[^:=]+[:=][[:space:]]*//' | sed 's/#.*//' | xargs)

  if [[ -n "$value" ]]; then
    echo "$value"
    return 0
  fi
  return 1
}

# Find .plans-config walking up the directory tree
# Returns the absolute path to the config file, or empty string if not found
_find_plans_config() {
  local dir="$1"
  local max_depth="${2:-50}"  # Safety limit
  local depth=0

  while [[ "$dir" != "/" && $depth -lt $max_depth ]]; do
    if [[ -f "${dir}/.plans-config" ]]; then
      echo "${dir}/.plans-config"
      return 0
    fi
    dir=$(dirname "$dir")
    ((depth++))
  done

  return 1
}

# Find git root walking up the directory tree
_find_git_root() {
  local dir="$1"
  local max_depth="${2:-50}"
  local depth=0

  while [[ "$dir" != "/" && $depth -lt $max_depth ]]; do
    if [[ -d "${dir}/.git" ]] || [[ -f "${dir}/.git" ]]; then
      echo "$dir"
      return 0
    fi
    dir=$(dirname "$dir")
    ((depth++))
  done

  return 1
}

# Main resolution logic
resolve_plans_dir() {
  local start_dir="${1:-$(pwd)}"
  start_dir=$(cd "$start_dir" 2>/dev/null && pwd)  # Get absolute path

  _debug "Starting resolution from: $start_dir"

  local plans_dir=""
  local plans_root=""
  local config_file=""

  # Step 1: Look for .plans-config
  config_file=$(_find_plans_config "$start_dir") || config_file=""

  if [[ -n "$config_file" ]]; then
    _debug "Found config: $config_file"
    local config_dir
    config_dir=$(dirname "$config_file")

    # Read configuration
    local configured_path
    configured_path=$(_parse_config "plans_dir" "$config_file") || configured_path=""

    local scope
    scope=$(_parse_config "scope" "$config_file") || scope=""

    if [[ -z "$configured_path" ]]; then
      configured_path="docs/plans"  # Default
    fi

    _debug "Configured path: $configured_path"
    _debug "Scope: ${scope:-global}"

    if [[ "$scope" == "local" ]]; then
      # Use this config file's directory as the base
      plans_root="$config_dir"
      plans_dir="${config_dir}/${configured_path}"
      _debug "Using local scope: $plans_dir"
    else
      # Continue to root (find topmost .plans-config or git root)
      local root_config
      root_config=$(_find_plans_config "$(dirname "$config_dir")") || root_config=""

      if [[ -n "$root_config" ]]; then
        # Found a higher-level config, use it
        _debug "Found higher-level config: $root_config"
        local root_dir
        root_dir=$(dirname "$root_config")
        local root_path
        root_path=$(_parse_config "plans_dir" "$root_config") || root_path=""

        if [[ -z "$root_path" ]]; then
          root_path="docs/plans"
        fi

        plans_root="$root_dir"
        plans_dir="${root_dir}/${root_path}"
      else
        # This is the topmost config
        plans_root="$config_dir"
        plans_dir="${config_dir}/${configured_path}"
      fi
    fi
  else
    _debug "No .plans-config found, trying git root"

    # Step 2: Fall back to git root
    local git_root
    git_root=$(_find_git_root "$start_dir") || git_root=""

    if [[ -n "$git_root" ]]; then
      _debug "Found git root: $git_root"
      plans_root="$git_root"
      plans_dir="${git_root}/docs/plans"
    else
      _debug "No git root found, using current directory"
      _warn "Could not find .plans-config or .git directory. Using current directory."
      plans_root="$start_dir"
      plans_dir="${start_dir}/docs/plans"
    fi
  fi

  # Normalize paths (remove trailing slashes, resolve ..)
  plans_dir=$(cd "$(dirname "$plans_dir")" 2>/dev/null && echo "$(pwd)/$(basename "$plans_dir")" || echo "$plans_dir")
  plans_root=$(cd "$plans_root" 2>/dev/null && pwd || echo "$plans_root")

  # Export variables
  export PLANS_DIR="$plans_dir"
  export PLANS_ROOT="$plans_root"

  _debug "Resolved PLANS_DIR: $PLANS_DIR"
  _debug "Resolved PLANS_ROOT: $PLANS_ROOT"

  return 0
}

# Helper function to get the full path for a feature
# Usage: get_feature_plan_dir "feature-name"
get_feature_plan_dir() {
  local feature_name="$1"

  if [[ -z "$feature_name" ]]; then
    _error "Feature name required"
    return 1
  fi

  # Ensure PLANS_DIR is set
  if [[ -z "${PLANS_DIR:-}" ]]; then
    resolve_plans_dir
  fi

  echo "${PLANS_DIR}/${feature_name}"
}

# If script is executed directly (not sourced), run resolution and print result
if [[ "${BASH_SOURCE[0]:-}" == "${0:-}" ]]; then
  # Running as script
  case "${1:-}" in
    --help|-h)
      echo "Usage: resolve-plans-dir.sh [start-dir]"
      echo ""
      echo "Resolves the plans directory for the current project."
      echo ""
      echo "Options:"
      echo "  --help, -h     Show this help message"
      echo "  --debug        Enable debug output"
      echo ""
      echo "Environment:"
      echo "  PLANS_DEBUG=1  Enable debug output"
      echo ""
      echo "Output:"
      echo "  Prints the resolved plans directory path"
      echo ""
      echo "Configuration (.plans-config):"
      echo "  plans_dir: docs/plans    # Relative path for plans"
      echo "  scope: local             # Use this directory as base"
      exit 0
      ;;
    --debug)
      export PLANS_DEBUG=1
      shift
      resolve_plans_dir "${1:-}"
      echo "$PLANS_DIR"
      ;;
    *)
      resolve_plans_dir "${1:-}"
      echo "$PLANS_DIR"
      ;;
  esac
else
  # Being sourced - automatically resolve
  resolve_plans_dir
fi
