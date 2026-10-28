#!/usr/bin/env bash
# Read-only selection of existing release-pointer files. Usage: pointer-targets.sh --repo DIR
# Prints repo-relative paths; exits 1 for invalid input or a dangling AGENTS.md import.
#
# Thin wrapper: the canonical AGENTS.md/CLAUDE.md detection lives in the shared
# ycc rules-pointer helper (skills/_shared/scripts/rules-pointer.sh -> the
# pointer rule's single source of truth, rules_pointer.py). This skill only
# reads — it never auto-migrates a layout and never creates missing files.
set -euo pipefail
if [[ "$#" -ne 2 || "$1" != "--repo" || ! -d "$2" ]]; then
  printf 'Usage: pointer-targets.sh --repo DIR\n' >&2
  exit 1
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SHARED_SEG="/_shared/scripts"; _SHARED_SEG_FLAT="/shared/scripts"; _SHARED_SEG_AGENTS="/ycc-shared/scripts"
POINTER_HELPER=""
for _cand in "${SCRIPT_DIR}/../..${_SHARED_SEG}/rules-pointer.sh" \
             "${SCRIPT_DIR}/../../..${_SHARED_SEG_FLAT}/rules-pointer.sh" \
             "${SCRIPT_DIR}/../../..${_SHARED_SEG_AGENTS}/rules-pointer.sh"; do
  if [[ -f "$_cand" ]]; then
    POINTER_HELPER="$_cand"
    break
  fi
done
if [[ -z "$POINTER_HELPER" ]]; then
  echo "pointer-targets: shared rules-pointer.sh not found (broken ycc install)" >&2
  exit 1
fi
exec "$POINTER_HELPER" targets --repo "$2"
