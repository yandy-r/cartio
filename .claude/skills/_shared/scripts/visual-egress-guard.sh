#!/usr/bin/env bash
#
# visual-egress-guard.sh — pre-upload guard for hosted visual-plan egress.
#
# Runs BEFORE any MDX/diff payload leaves the machine for a hosted service
# (e.g. plan.agent-native.com). It enforces three independent gates:
#   1. Secret/denylist scan of the payload (mirrors clean/validate-safety.sh).
#   2. Public-remote check — refuses when the repo's resolved remote is the
#      private Forgejo origin or any non-public host (never hardcodes a host
#      beyond the public allowlist; the remote URL comes from `git remote
#      get-url`).
#   3. Explicit consent — a consent token naming the destination host must be
#      supplied before egress is permitted.
#
# Usage:
#   visual-egress-guard.sh --payload <path> [--remote <name>] \
#       [--destination <host>] [--consent <host>]
#
# Arguments:
#   --payload <path>        File or directory to scan before upload (required).
#   --remote <name>         Remote whose URL gates publicity (default: tracked
#                           upstream's remote, else "origin").
#   --destination <host>    Hosted egress destination (default: plan.agent-native.com).
#   --consent <host>        Explicit consent token; MUST equal --destination.
#
# Exit codes:
#   0 - All gates passed; hosted egress is permitted
#   1 - Usage / missing required argument
#   2 - Secret/denylist hit found in the payload (egress aborted)
#   3 - Resolved remote is non-public (private Forgejo / non-allowlisted host)
#   4 - Missing or mismatched consent token
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Logging helpers (stdout = results, stderr = diagnostics)
# ---------------------------------------------------------------------------

_error() {
  echo "visual-egress-guard.sh: error: $*" >&2
}

_info() {
  echo "visual-egress-guard.sh: $*" >&2
}

usage() {
  cat >&2 <<'EOF'
Usage:
  visual-egress-guard.sh --payload <path> [--remote <name>] \
      [--destination <host>] [--consent <host>]

Arguments:
  --payload <path>      File or directory to scan before upload (required).
  --remote <name>       Remote whose URL gates publicity (default: upstream's
                        remote, else "origin").
  --destination <host>  Hosted egress destination (default: plan.agent-native.com).
  --consent <host>      Explicit consent token; MUST equal --destination.

Exit codes: 0 ok | 1 usage | 2 secret hit | 3 non-public remote | 4 no consent
EOF
}

# ---------------------------------------------------------------------------
# Denylist — mirrors clean/validate-safety.sh PROTECTED/SECURITY patterns.
# ---------------------------------------------------------------------------

declare -a PROTECTED=(
  ".env"
  ".env.*"
  "*.pem"
  "*.key"
  "*.p12"
  "*.pfx"
  "*.crt"
  "id_rsa"
  "id_ed25519"
  "credentials.json"
  "secrets.json"
)

# Non-public host detection. A host is treated as non-public (egress refused)
# when it is the known-private Forgejo origin, sits on a private/tailnet TLD, is
# a loopback/RFC1918 address, or is an unqualified single-label hostname. Any
# other fully-qualified public host is allowed. This deliberately avoids
# hardcoding a public vendor host: publicity is inferred, privacy is matched.
#
# The known-private host defaults to the Forgejo origin and may be overridden
# via VISUAL_EGRESS_PRIVATE_HOST for other deployments.
PRIVATE_HOST="${VISUAL_EGRESS_PRIVATE_HOST:-git.azules-celsius.ts.net}"

declare -a PRIVATE_SUFFIXES=(
  ".ts.net"
  ".local"
  ".internal"
  ".lan"
  ".home.arpa"
  ".localdomain"
)

DEFAULT_DESTINATION="plan.agent-native.com"

# ---------------------------------------------------------------------------
# Argument parsing (strict case-based flag handling)
# ---------------------------------------------------------------------------

PAYLOAD=""
REMOTE_NAME=""
DESTINATION="$DEFAULT_DESTINATION"
CONSENT=""

require_value() {
  if [[ $# -lt 2 || -z "$2" ]]; then
    _error "flag $1 requires a value"
    usage
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --payload)
      require_value "$@"
      PAYLOAD="$2"
      shift 2
      ;;
    --remote)
      require_value "$@"
      REMOTE_NAME="$2"
      shift 2
      ;;
    --destination)
      require_value "$@"
      DESTINATION="$2"
      shift 2
      ;;
    --consent)
      require_value "$@"
      CONSENT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      _error "unknown flag: $1"
      usage
      exit 1
      ;;
    *)
      _error "unexpected positional argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$PAYLOAD" ]]; then
  _error "--payload is required"
  usage
  exit 1
fi

if [[ ! -e "$PAYLOAD" ]]; then
  _error "payload not found: $PAYLOAD"
  exit 1
fi

# ---------------------------------------------------------------------------
# Gate 1: secret / denylist scan (exit 2 on hit)
# ---------------------------------------------------------------------------

# Convert a denylist glob into a path-anchored extended regex.
glob_to_regex() {
  local g="$1"
  g="${g//./\\.}"        # escape literal dots
  g="${g//\*/[^/ ]*}"    # '*' matches a run of non-slash, non-space chars
  printf '(^|[ /])%s($|[ /])' "$g"
}

SECRET_HITS=0
for pattern in "${PROTECTED[@]}"; do
  regex="$(glob_to_regex "$pattern")"

  # (a) Protected path referenced in payload content (e.g. diff headers).
  if grep -rInE -- "$regex" "$PAYLOAD" >/dev/null 2>&1; then
    _error "denylist hit (content): pattern '$pattern' present in payload"
    SECRET_HITS=$((SECRET_HITS + 1))
  fi

  # (b) An actual protected file inside the payload tree.
  if [[ -d "$PAYLOAD" ]]; then
    if find "$PAYLOAD" -type f -name "$pattern" -print -quit 2>/dev/null | grep -q .; then
      _error "denylist hit (file): '$pattern' file exists under payload"
      SECRET_HITS=$((SECRET_HITS + 1))
    fi
  else
    # shellcheck disable=SC2053
    if [[ "$(basename "$PAYLOAD")" == $pattern ]]; then
      _error "denylist hit (file): payload basename matches '$pattern'"
      SECRET_HITS=$((SECRET_HITS + 1))
    fi
  fi
done

if [[ "$SECRET_HITS" -gt 0 ]]; then
  _error "aborting: ${SECRET_HITS} secret/denylist hit(s) — payload is unsafe to upload"
  exit 2
fi
_info "secret scan clean"

# ---------------------------------------------------------------------------
# Gate 2: public-remote check (exit 3 when non-public)
# ---------------------------------------------------------------------------

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  _error "not inside a git repository; cannot verify remote publicity"
  exit 3
fi

# Default the remote name to the tracked upstream's remote, else "origin".
if [[ -z "$REMOTE_NAME" ]]; then
  UPSTREAM="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
  if [[ -n "$UPSTREAM" && "$UPSTREAM" == */* ]]; then
    REMOTE_NAME="${UPSTREAM%%/*}"
  else
    REMOTE_NAME="origin"
  fi
fi

REMOTE_URL="$(git remote get-url "$REMOTE_NAME" 2>/dev/null || true)"
if [[ -z "$REMOTE_URL" ]]; then
  _error "could not resolve URL for remote '$REMOTE_NAME'"
  exit 3
fi

# Extract the host from any common git URL form without hardcoding a host.
remote_host() {
  local url="$1"
  url="${url#*://}"   # drop scheme:// if present
  url="${url#*@}"     # drop user@ if present
  url="${url%%[:/]*}" # host is up to the first ':' or '/'
  printf '%s' "$url"
}

HOST="$(remote_host "$REMOTE_URL")"
HOST="$(printf '%s' "$HOST" | tr '[:upper:]' '[:lower:]')"

# Decide whether a host is public. Privacy is matched explicitly; anything not
# matched as private but shaped like a public FQDN is treated as public.
is_public_host() {
  local host="$1"

  # Known-private origin.
  [[ "$host" == "$PRIVATE_HOST" ]] && return 1

  # Loopback / RFC1918 literals.
  case "$host" in
    localhost|127.*|10.*|192.168.*) return 1 ;;
    172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 1 ;;
  esac

  # Private / internal / tailnet suffixes.
  local suffix
  for suffix in "${PRIVATE_SUFFIXES[@]}"; do
    [[ "$host" == *"$suffix" ]] && return 1
  done

  # Unqualified single-label hostnames are not public.
  [[ "$host" != *.* ]] && return 1

  return 0
}

if ! is_public_host "$HOST"; then
  _error "refusing hosted egress: remote '$REMOTE_NAME' host '$HOST' is not public"
  _error "private/internal remotes (e.g. the Forgejo origin) must not upload to ${DESTINATION}"
  exit 3
fi
_info "remote '$REMOTE_NAME' host '$HOST' is public"

# ---------------------------------------------------------------------------
# Gate 3: explicit consent (exit 4 when missing/mismatched)
# ---------------------------------------------------------------------------

if [[ -z "$CONSENT" ]]; then
  _error "missing consent: pass --consent ${DESTINATION} to authorize hosted egress"
  exit 4
fi

if [[ "$CONSENT" != "$DESTINATION" ]]; then
  _error "consent mismatch: --consent '$CONSENT' does not name destination '${DESTINATION}'"
  exit 4
fi
_info "consent confirmed for destination '${DESTINATION}'"

# ---------------------------------------------------------------------------
# All gates passed -> result on stdout
# ---------------------------------------------------------------------------

echo "egress-permitted destination=${DESTINATION} remote=${REMOTE_NAME} host=${HOST}"
exit 0
