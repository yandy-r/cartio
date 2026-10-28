#!/usr/bin/env bash
set -euo pipefail

# validate-commit.sh
# Validates commit messages against conventional commit format
# Usage: validate-commit.sh "<commit-message>"

# Color codes for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly NC='\033[0m' # No Color

# Valid commit types
readonly VALID_TYPES=(
    "feat"
    "fix"
    "docs"
    "style"
    "refactor"
    "test"
    "chore"
    "perf"
    "ci"
    "build"
    "revert"
)

# Check if commit message is provided
if [ $# -eq 0 ]; then
    echo -e "${RED}Error: No commit message provided${NC}" >&2
    echo "Usage: $0 \"<commit-message>\"" >&2
    exit 1
fi

COMMIT_MSG="$1"

# Track validation errors
ERRORS=0
WARNINGS=0

echo -e "${GREEN}=== Commit Message Validation ===${NC}\n"
echo "Validating: $COMMIT_MSG"
echo ""

# Match variables with here-strings, not `echo | grep -q`: under pipefail an
# early-exiting reader SIGPIPEs the echo writer and a present pattern reads
# as missing (YAN-1587).

# Extract the subject line (first line). Parameter expansion keeps the whole
# message in memory — no echo writer for an early-exiting head to SIGPIPE.
SUBJECT="${COMMIT_MSG%%$'\n'*}"

# Check if message is empty
if [ -z "$SUBJECT" ]; then
    echo -e "${RED}✗ Error: Commit message is empty${NC}"
    ERRORS=$((ERRORS + 1))
    exit 1
fi

# Validate format: <type>(<scope>): <subject> or <type>: <subject>
if ! grep -qE '^[a-z]+(\([a-z0-9-]+\))?!?: .+' <<<"$SUBJECT"; then
    echo -e "${RED}✗ Error: Subject line doesn't match conventional commit format${NC}"
    echo "  Expected: <type>(<scope>): <subject> or <type>: <subject>"
    echo "  Got: $SUBJECT"
    ERRORS=$((ERRORS + 1))
else
    echo -e "${GREEN}✓ Format matches conventional commits${NC}"
fi

# Extract type from subject
TYPE=$(echo "$SUBJECT" | grep -oE '^[a-z]+' || echo "")

if [ -n "$TYPE" ]; then
    # Validate type
    VALID_TYPE=false
    for valid in "${VALID_TYPES[@]}"; do
        if [ "$TYPE" = "$valid" ]; then
            VALID_TYPE=true
            break
        fi
    done

    if [ "$VALID_TYPE" = true ]; then
        echo -e "${GREEN}✓ Type '$TYPE' is valid${NC}"
    else
        echo -e "${RED}✗ Error: Invalid type '$TYPE'${NC}"
        echo "  Valid types: ${VALID_TYPES[*]}"
        ERRORS=$((ERRORS + 1))
    fi
fi

# Extract scope (if present)
if grep -qE '\([a-z0-9-]+\)' <<<"$SUBJECT"; then
    SCOPE=$(echo "$SUBJECT" | grep -oE '\([a-z0-9-]+\)' | tr -d '()')
    echo -e "${GREEN}✓ Scope '$SCOPE' is present${NC}"

    # Check scope format (lowercase, alphanumeric + hyphens)
    if ! grep -qE '^[a-z0-9-]+$' <<<"$SCOPE"; then
        echo -e "${YELLOW}⚠ Warning: Scope should be lowercase alphanumeric with hyphens${NC}"
        WARNINGS=$((WARNINGS + 1))
    fi
fi

# Check for breaking change indicator (!)
if grep -qE '!:' <<<"$SUBJECT"; then
    echo -e "${YELLOW}⚠ Breaking change indicator detected (!)${NC}"

    # Check if BREAKING CHANGE footer is present in full message
    if ! grep -qE 'BREAKING CHANGE:' <<<"$COMMIT_MSG"; then
        echo -e "${YELLOW}⚠ Warning: Breaking change indicator used but no BREAKING CHANGE footer found${NC}"
        echo "  Consider adding: BREAKING CHANGE: <description>"
        WARNINGS=$((WARNINGS + 1))
    fi
fi

# Extract subject (after colon)
SUBJECT_TEXT=$(echo "$SUBJECT" | sed 's/^[a-z]*(\?[a-z0-9-]*)\?!*: //')

if [ -n "$SUBJECT_TEXT" ]; then
    # Check subject length (should be ≤50 chars ideally, ≤72 max)
    SUBJECT_LEN=${#SUBJECT_TEXT}

    if [ "$SUBJECT_LEN" -le 50 ]; then
        echo -e "${GREEN}✓ Subject length is good ($SUBJECT_LEN chars)${NC}"
    elif [ "$SUBJECT_LEN" -le 72 ]; then
        echo -e "${YELLOW}⚠ Warning: Subject is acceptable but long ($SUBJECT_LEN chars, prefer ≤50)${NC}"
        WARNINGS=$((WARNINGS + 1))
    else
        echo -e "${RED}✗ Error: Subject is too long ($SUBJECT_LEN chars, max 72)${NC}"
        ERRORS=$((ERRORS + 1))
    fi

    # Check if subject starts with uppercase (should be lowercase)
    FIRST_CHAR=$(echo "$SUBJECT_TEXT" | cut -c1)
    if grep -qE '[A-Z]' <<<"$FIRST_CHAR"; then
        # Portable lowercase conversion using bash parameter expansion
        REST="${SUBJECT_TEXT:1}"
        LOWERCASE_FIRST="${FIRST_CHAR,,}"
        PREFERRED="${LOWERCASE_FIRST}${REST}"

        echo -e "${YELLOW}⚠ Warning: Subject should start with lowercase letter${NC}"
        echo "  Got: '$SUBJECT_TEXT'"
        echo "  Prefer: '$PREFERRED'"
        WARNINGS=$((WARNINGS + 1))
    else
        echo -e "${GREEN}✓ Subject starts with lowercase${NC}"
    fi

    # Check if subject ends with period (should not)
    if grep -qE '\.$' <<<"$SUBJECT_TEXT"; then
        echo -e "${YELLOW}⚠ Warning: Subject should not end with a period${NC}"
        WARNINGS=$((WARNINGS + 1))
    else
        echo -e "${GREEN}✓ Subject doesn't end with period${NC}"
    fi

    # Check if imperative mood (basic heuristic - starts with verb)
    FIRST_WORD=$(echo "$SUBJECT_TEXT" | awk '{print $1}')

    # Common non-imperative patterns
    if grep -qE '(added|fixed|updated|changed|removed|deleted)$' <<<"$FIRST_WORD"; then
        echo -e "${YELLOW}⚠ Warning: Use imperative mood (add, fix, update, not added, fixed, updated)${NC}"
        echo "  Got: '$FIRST_WORD'"
        echo "  Prefer: '$(echo "$FIRST_WORD" | sed 's/ed$//' | sed 's/d$//')'"
        WARNINGS=$((WARNINGS + 1))
    else
        echo -e "${GREEN}✓ Subject appears to use imperative mood${NC}"
    fi
fi

# Check for body (optional but recommended for complex changes)
# grep -c prints the count itself; exit 1 only means zero matches. Any other
# non-zero status is a real failure.
BODY_LINES_STATUS=0
BODY_LINES=$(echo "$COMMIT_MSG" | tail -n +3 | grep -c '[[:alnum:]]') || BODY_LINES_STATUS=$?
if [ "$BODY_LINES_STATUS" -gt 1 ]; then
    echo "Error: Failed to count body lines" >&2
    exit "$BODY_LINES_STATUS"
fi

if [ "$BODY_LINES" -gt 0 ]; then
    echo -e "${GREEN}✓ Body is present ($BODY_LINES lines)${NC}"

    # Check body line length (should wrap at 72 chars)
    LONG_LINES=0
    while IFS= read -r line; do
        if [ ${#line} -gt 72 ] && [ -n "$line" ]; then
            LONG_LINES=$((LONG_LINES + 1))
        fi
    done <<< "$(echo "$COMMIT_MSG" | tail -n +3)"

    if [ "$LONG_LINES" -gt 0 ]; then
        echo -e "${YELLOW}⚠ Warning: $LONG_LINES line(s) in body exceed 72 characters${NC}"
        echo "  Consider wrapping body text at 72 chars"
        WARNINGS=$((WARNINGS + 1))
    fi
fi

# Check for BREAKING CHANGE footer
if grep -qE 'BREAKING CHANGE:' <<<"$COMMIT_MSG"; then
    echo -e "${YELLOW}⚠ Breaking change footer detected${NC}"

    # Verify format
    if grep -qE '^BREAKING CHANGE: .+' <<<"$COMMIT_MSG"; then
        echo -e "${GREEN}✓ BREAKING CHANGE footer is properly formatted${NC}"
    else
        echo -e "${RED}✗ Error: BREAKING CHANGE footer format incorrect${NC}"
        echo "  Expected: BREAKING CHANGE: <description>"
        ERRORS=$((ERRORS + 1))
    fi
fi

# Check for issue references
if grep -qE '(Fixes|Closes|Resolves|Related to) #[0-9]+' <<<"$COMMIT_MSG"; then
    echo -e "${GREEN}✓ Issue reference found${NC}"
fi

# Summary
echo ""
echo -e "${GREEN}=== Validation Summary ===${NC}\n"

if [ "$ERRORS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
    echo -e "${GREEN}✓ Commit message is valid!${NC}"
    echo ""
    exit 0
elif [ "$ERRORS" -eq 0 ]; then
    echo -e "${YELLOW}⚠ Commit message is acceptable with $WARNINGS warning(s)${NC}"
    echo ""
    echo "Consider addressing the warnings for better commit quality."
    exit 0
else
    echo -e "${RED}✗ Commit message has $ERRORS error(s) and $WARNINGS warning(s)${NC}"
    echo ""
    echo "Please fix the errors before committing."
    echo ""
    echo "Quick reference:"
    echo "  Format:  <type>(<scope>): <subject>"
    echo "  Types:   feat, fix, docs, style, refactor, test, chore, perf, ci, build, revert"
    echo "  Subject: Imperative mood, lowercase, no period, ≤50 chars"
    echo ""
    echo "Examples:"
    echo "  feat(auth): add JWT authentication"
    echo "  fix(api): prevent null pointer in validation"
    echo "  docs: update installation guide"
    exit 1
fi
