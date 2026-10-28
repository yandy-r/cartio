#!/usr/bin/env bash
# release-state-lib.sh — parse, render and rewrite the ycc release-state block
# in a repo-root RELEASING.md. Sourced by release-state.sh and
# release-state-update.sh; not meant to be executed directly.
#
# The block and table format is specified in
# .claude/skills/_shared/references/branching-model.md#state-block.
#
# Parsed values are stored in plain globals (RS_MODEL, RS_TRUNK, ...) so the
# library works on bash 3.2 (macOS) as well as bash 5.
#
# Backticks in single-quoted printf formats are literal Markdown code spans.
# shellcheck disable=SC2016

RS_BLOCK_BEGIN='<!-- ycc-release-state'
RS_TABLE_BEGIN='<!-- ycc-release-state:table:begin -->'
RS_TABLE_END='<!-- ycc-release-state:table:end -->'
RS_KNOWN_KEYS=" model trunk maintenance frozen support backport_label tracker tracker_ref "
RS_BRANCH_RE='^release/[0-9]+\.[0-9]+$'

# Slash-command hints stay in single quotes: the Codex generator rewrites
# "/release-model" to "$release-model", which bash would expand (and fail
# on under set -u) inside double quotes.
RS_HINT_CREATE='/release-model'
RS_HINT_AUDIT='/release-model --audit'
RS_ERROR=""
# Read by the scripts that source this library.
export RS_HINT_CREATE RS_HINT_AUDIT RS_ERROR

# rs_reset — clear all parsed state and apply defaults.
rs_reset() {
  RS_MODEL=""
  RS_TRUNK=""
  RS_MAINTENANCE=""
  RS_FROZEN=""
  RS_SUPPORT="latest-minor"
  RS_BACKPORT_LABEL="backport:{X.Y}"
  RS_TRACKER="none"
  RS_TRACKER_REF=""
  RS_ERROR=""
}

# rs_fail <reason> — record a parse error and return 2.
rs_fail() {
  RS_ERROR="$1"
  return 2
}

# rs_trim <string> — print the string without leading/trailing whitespace.
rs_trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# rs_normalize_list <value> — "a , b,c" -> "a,b,c" (empty stays empty).
rs_normalize_list() {
  local raw="$1" item out=""
  local IFS=','
  for item in $raw; do
    item="$(rs_trim "$item")"
    [[ -z "$item" ]] && continue
    out="${out:+$out,}$item"
  done
  printf '%s' "$out"
}

# rs_extract_block <file> — print the raw lines inside the state block.
# Returns 3 when the file has no block.
rs_extract_block() {
  LC_ALL=C awk '
    NR == 1 { sub(/^\357\273\277/, "") }
    { sub(/\r$/, "") }
    /^<!-- ycc-release-state[[:space:]]*$/ { inblock = 1; found = 1; next }
    inblock && /^-->[[:space:]]*$/ { inblock = 0; closed = 1; exit }
    inblock { print }
    END {
      if (!found) exit 3
      if (!closed) exit 4
    }
  ' "$1"
}

# rs_set_key <key> <value> — assign one parsed key, rejecting duplicates.
rs_set_key() {
  local key="$1" value="$2" var seen_var
  var="RS_$(printf '%s' "$key" | tr '[:lower:]' '[:upper:]')"
  seen_var="RS_SEEN_${key}"
  if [[ -n "${!seen_var:-}" ]]; then
    rs_fail "duplicate key '$key' in the state block"
    return 2
  fi
  printf -v "$seen_var" '%s' 1
  printf -v "$var" '%s' "$value"
}

# rs_validate_branch_list <key> <list> — every entry must be release/X.Y.
rs_validate_branch_list() {
  local key="$1" list="$2" item
  local IFS=','
  for item in $list; do
    if [[ ! "$item" =~ $RS_BRANCH_RE ]]; then
      rs_fail "'$key' entry '$item' is not of the form release/X.Y"
      return 2
    fi
  done
}

# rs_validate — check the parsed values against the format rules.
rs_validate() {
  case "$RS_MODEL" in
    trunk-only | release-branches) ;;
    "") rs_fail "missing required key 'model'" || return 2 ;;
    *) rs_fail "invalid model '$RS_MODEL' (expected trunk-only or release-branches)" || return 2 ;;
  esac
  if [[ -z "$RS_TRUNK" ]]; then
    rs_fail "missing required key 'trunk'" || return 2
  fi
  if [[ "$RS_TRUNK" =~ [[:space:]] ]]; then
    rs_fail "trunk '$RS_TRUNK' contains whitespace" || return 2
  fi
  rs_validate_branch_list maintenance "$RS_MAINTENANCE" || return 2
  rs_validate_branch_list frozen "$RS_FROZEN" || return 2
  if [[ "$RS_MODEL" == "trunk-only" && -n "$RS_MAINTENANCE" ]]; then
    rs_fail "model trunk-only cannot have maintenance branches" || return 2
  fi
  case "$RS_TRACKER" in
    none | github-milestones | github-labels | linear-labels) ;;
    *) rs_fail "invalid tracker '$RS_TRACKER'" || return 2 ;;
  esac
  if [[ "$RS_BACKPORT_LABEL" != *"{X.Y}"* ]]; then
    rs_fail "backport_label '$RS_BACKPORT_LABEL' must contain {X.Y}" || return 2
  fi
  if [[ -z "$RS_SUPPORT" ]]; then
    rs_fail "support must not be empty" || return 2
  fi
}

# rs_parse_file <file> — parse and validate the state block. Returns 0 on
# success, 2 on any format error (reason in RS_ERROR).
rs_parse_file() {
  local file="$1" block rc=0 line key value k
  rs_reset
  for k in $RS_KNOWN_KEYS; do
    unset "RS_SEEN_${k}"
  done
  block="$(rs_extract_block "$file")" || rc=$?
  case "$rc" in
    0) ;;
    3) rs_fail "RELEASING.md has no ycc state block" || return 2 ;;
    4) rs_fail "the state block is not closed with '-->'" || return 2 ;;
    *) rs_fail "could not read $file" || return 2 ;;
  esac
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="$(rs_trim "$line")"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ ! "$line" =~ ^([a-z_]+):(.*)$ ]]; then
      rs_fail "malformed line '$line' (expected key: value)" || return 2
    fi
    key="${BASH_REMATCH[1]}"
    value="$(rs_trim "${BASH_REMATCH[2]}")"
    if [[ "$RS_KNOWN_KEYS" != *" $key "* ]]; then
      rs_fail "unknown key '$key'" || return 2
    fi
    case "$key" in
      maintenance | frozen) value="$(rs_normalize_list "$value")" ;;
    esac
    if [[ -z "$value" && ("$key" == "support" || "$key" == "backport_label" || "$key" == "tracker") ]]; then
      continue
    fi
    rs_set_key "$key" "$value" || return 2
  done <<<"$block"
  rs_validate
}

# rs_first <list> — first entry of a comma list.
rs_first() {
  printf '%s' "${1%%,*}"
}

# rs_label_for <release/X.Y> — the backport label for one maintenance branch.
rs_label_for() {
  local version="${1#release/}"
  printf '%s' "${RS_BACKPORT_LABEL//\{X.Y\}/$version}"
}

# rs_format_block — print the canonical state block for the current globals.
rs_format_block() {
  printf '%s\n' "$RS_BLOCK_BEGIN"
  printf 'model: %s\n' "$RS_MODEL"
  printf 'trunk: %s\n' "$RS_TRUNK"
  [[ -n "$RS_MAINTENANCE" ]] && printf 'maintenance: %s\n' "${RS_MAINTENANCE//,/, }"
  [[ -n "$RS_FROZEN" ]] && printf 'frozen: %s\n' "${RS_FROZEN//,/, }"
  printf 'support: %s\n' "$RS_SUPPORT"
  printf 'backport_label: %s\n' "$RS_BACKPORT_LABEL"
  printf 'tracker: %s\n' "$RS_TRACKER"
  [[ -n "$RS_TRACKER_REF" ]] && printf 'tracker_ref: %s\n' "$RS_TRACKER_REF"
  printf '%s\n' '-->'
}

# rs_table_rows — print "role<TAB>branch<TAB>notes" rows for the table.
rs_table_rows() {
  local item
  local IFS=','
  if [[ "$RS_MODEL" == "trunk-only" ]]; then
    printf 'Trunk\t`%s`\tEvery release is tagged here\n' "$RS_TRUNK"
  else
    printf 'Trunk\t`%s`\tNext minor or major release\n' "$RS_TRUNK"
  fi
  for item in $RS_MAINTENANCE; do
    printf 'Maintenance\t`%s`\tPatches via `%s`\n' "$item" "$(rs_label_for "$item")"
  done
  for item in $RS_FROZEN; do
    printf 'Frozen\t`%s`\tNo further patches\n' "$item"
  done
}

# rs_render_table — print the table section (markers included).
rs_render_table() {
  local rows
  rows="$(rs_table_rows)"
  printf '%s\n\n' "$RS_TABLE_BEGIN"
  printf 'Model: **%s**. Support window: %s.\n\n' "$RS_MODEL" "$RS_SUPPORT"
  printf '%s\n' "$rows" | awk -F'\t' '
    BEGIN { w1 = length("Role"); w2 = length("Branch"); w3 = length("Notes") }
    {
      r1[NR] = $1; r2[NR] = $2; r3[NR] = $3
      if (length($1) > w1) w1 = length($1)
      if (length($2) > w2) w2 = length($2)
      if (length($3) > w3) w3 = length($3)
    }
    function pad(s, w) { return s sprintf("%" (w - length(s)) "s", "") }
    function dash(w,   out, i) { out = ""; for (i = 0; i < w; i++) out = out "-"; return out }
    END {
      printf "| %s | %s | %s |\n", pad("Role", w1), pad("Branch", w2), pad("Notes", w3)
      printf "| %s | %s | %s |\n", dash(w1), dash(w2), dash(w3)
      for (i = 1; i <= NR; i++) printf "| %s | %s | %s |\n", pad(r1[i], w1), pad(r2[i], w2), pad(r3[i], w3)
    }
  '
  printf '\n%s\n' "$RS_TABLE_END"
}

# rs_extract_table <file> — print the existing table section (markers
# included, CR stripped). Returns 3 when the markers are missing.
rs_extract_table() {
  LC_ALL=C awk -v begin="$RS_TABLE_BEGIN" -v end="$RS_TABLE_END" '
    NR == 1 { sub(/^\357\273\277/, "") }
    { sub(/\r$/, "") }
    $0 == begin { intable = 1; found = 1 }
    intable { print }
    intable && $0 == end { intable = 0; closed = 1; exit }
    END { if (!found || !closed) exit 3 }
  ' "$1"
}

# rs_write_file <file> <out> — write <file> with the block and table replaced
# from the current globals into <out>. Lines outside the block and table are
# copied byte-for-byte. A missing table is inserted right after the block.
# CRLF files keep CRLF on the rewritten lines.
rs_write_file() {
  local file="$1" out="$2" block_tmp table_tmp
  block_tmp="$(mktemp)"
  table_tmp="$(mktemp)"
  rs_format_block >"$block_tmp"
  rs_render_table >"$table_tmp"
  LC_ALL=C awk -v blockf="$block_tmp" -v tablef="$table_tmp" \
    -v begin="$RS_TABLE_BEGIN" -v end="$RS_TABLE_END" '
    function emit(f,   l) {
      while ((getline l < f) > 0) printf "%s%s\n", l, eol
      close(f)
    }
    NR == 1 { eol = ($0 ~ /\r$/) ? "\r" : ""; bom = ($0 ~ /^\357\273\277/) ? "\357\273\277" : "" }
    {
      raw = $0
      line = raw
      sub(/\r$/, "", line)
      if (NR == 1) sub(/^\357\273\277/, "", line)
    }
    skip_block {
      if (line ~ /^-->[[:space:]]*$/) {
        skip_block = 0
        if (!has_table) { printf "%s", eol "\n"; emit(tablef); inserted = 1 }
      }
      next
    }
    skip_table { if (line == end) skip_table = 0; next }
    line ~ /^<!-- ycc-release-state[[:space:]]*$/ { if (NR == 1 && bom != "") printf "%s", bom; emit(blockf); skip_block = 1; next }
    line == begin { emit(tablef); skip_table = 1; next }
    { print raw }
  ' has_table="$(rs_extract_table "$file" >/dev/null 2>&1 && echo 1 || echo 0)" "$file" >"$out"
  rm -f "$block_tmp" "$table_tmp"
}

# rs_repo_root [dir] — print the git top-level for dir (default: cwd), or dir
# itself when it is not inside a git repository.
rs_repo_root() {
  local dir="${1:-.}"
  git -C "$dir" rev-parse --show-toplevel 2>/dev/null || (cd "$dir" && pwd)
}
