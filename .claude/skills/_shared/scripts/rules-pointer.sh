#!/usr/bin/env bash
# rules-pointer.sh — canonical AGENTS.md/CLAUDE.md detection and shim pointer.
# Single source of truth for which rules file is canonical and how the
# CLAUDE.md -> AGENTS.md shim looks. Used by init (profile/migrate/render) and
# release-model (pointer-targets.sh delegates here); release-model only reads —
# it never auto-migrates and never creates missing rules files.
#
# Usage:
#   rules-pointer.sh imports-agents --repo DIR   print imports_agents=true|false
#   rules-pointer.sh targets --repo DIR          print existing pointer files
#   rules-pointer.sh claude-md-shim [--notes T|-] print the canonical CLAUDE.md shim
#
# Requires python3 3.11+ (guarded first, readable error, no partial output).
# Exit codes: 0 ok, 1 usage error or dangling @AGENTS.md import.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=require-python.sh
. "${SCRIPT_DIR}/require-python.sh"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "${SCRIPT_DIR}/rules_pointer.py" "$@"
