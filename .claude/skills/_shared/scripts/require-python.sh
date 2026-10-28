#!/usr/bin/env bash
# require-python.sh — sourced guard: fail loudly when python3 is below the ycc
# minimum (3.11: tomllib + datetime.UTC). Source it before any python3 usage so
# unsupported interpreters abort with a readable message and no partial output.
# Prints nothing on success. Safe under `set -euo pipefail`, bash 3.2+.
set -euo pipefail
_require_python_found="$(python3 -V 2>/dev/null || true)"
if [[ -z "$_require_python_found" ]]; then
  echo "require-python: python3 not found on PATH (ycc needs python3 3.11+)" >&2
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  echo "require-python: python3 3.11+ required, found: ${_require_python_found}" >&2
  echo "require-python: install a newer python3 (or point 'python3' at one) and retry" >&2
  exit 1
fi
unset _require_python_found
