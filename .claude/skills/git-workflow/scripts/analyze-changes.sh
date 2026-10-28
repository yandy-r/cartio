#!/usr/bin/env bash
set -euo pipefail

# analyze-changes.sh
# Analyzes git changes and provides structured output for decision-making
# Usage: analyze-changes.sh

# Color codes for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo -e "${RED}Error: Not in a git repository${NC}" >&2
    exit 1
fi

# Get git status information
echo -e "${BLUE}=== Git Change Analysis ===${NC}\n"

# Check for staged changes
STAGED_FILES=$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
UNSTAGED_FILES=$(git diff --name-only 2>/dev/null | wc -l | tr -d ' ')
UNTRACKED_FILES=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')
TOTAL_FILES=$((STAGED_FILES + UNSTAGED_FILES + UNTRACKED_FILES))

# Display file counts
echo -e "${GREEN}Files Summary:${NC}"
echo "  Staged files:   $STAGED_FILES"
echo "  Unstaged files: $UNSTAGED_FILES"
echo "  Untracked files: $UNTRACKED_FILES"
echo "  Total files:    $TOTAL_FILES"
echo ""

# Exit early if no changes
if [ "$TOTAL_FILES" -eq 0 ]; then
    echo -e "${YELLOW}No changes detected${NC}"
    echo ""
    echo "Nothing to commit. Working tree is clean."
    exit 0
fi

# Analyze staged and unstaged changes
if [ "$STAGED_FILES" -gt 0 ] || [ "$UNSTAGED_FILES" -gt 0 ]; then
    # Get statistics
    STAGED_STATS=$(git diff --cached --stat 2>/dev/null | tail -n1 || echo "")
    UNSTAGED_STATS=$(git diff --stat 2>/dev/null | tail -n1 || echo "")

    # Parse insertions and deletions
    INSERTIONS=0
    DELETIONS=0

    if [ -n "$STAGED_STATS" ]; then
        STAGED_INS=$(echo "$STAGED_STATS" | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+' || echo "0")
        STAGED_DEL=$(echo "$STAGED_STATS" | grep -oE '[0-9]+ deletion' | grep -oE '[0-9]+' || echo "0")
        INSERTIONS=$((INSERTIONS + STAGED_INS))
        DELETIONS=$((DELETIONS + STAGED_DEL))
    fi

    if [ -n "$UNSTAGED_STATS" ]; then
        UNSTAGED_INS=$(echo "$UNSTAGED_STATS" | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+' || echo "0")
        UNSTAGED_DEL=$(echo "$UNSTAGED_STATS" | grep -oE '[0-9]+ deletion' | grep -oE '[0-9]+' || echo "0")
        INSERTIONS=$((INSERTIONS + UNSTAGED_INS))
        DELETIONS=$((DELETIONS + UNSTAGED_DEL))
    fi

    echo -e "${GREEN}Change Statistics:${NC}"
    echo "  Insertions: +$INSERTIONS"
    echo "  Deletions:  -$DELETIONS"
    echo ""
fi

# Categorize files by type
SOURCE_FILES=0
TEST_FILES=0
DOC_FILES=0
CONFIG_FILES=0

categorize_file() {
    local file="$1"

    # Test files
    if grep -qE '(test|spec|__tests__|\.test\.|\.spec\.)' <<<"$file"; then
        return 1
    fi

    # Documentation files
    if grep -qE '\.(md|txt|rst|adoc)$|^docs/' <<<"$file"; then
        return 2
    fi

    # Config files
    if grep -qE '\.(json|yaml|yml|toml|ini|conf|config)$|^\.|package\.json|tsconfig|eslint|prettier' <<<"$file"; then
        return 3
    fi

    # Source files (default)
    return 0
}

# Get all changed files
ALL_CHANGED_FILES=$(
    (git diff --cached --name-only 2>/dev/null || true;
     git diff --name-only 2>/dev/null || true;
     git ls-files --others --exclude-standard 2>/dev/null || true) | sort -u
)

# Categorize each file
while IFS= read -r file; do
    if [ -n "$file" ]; then
        rc=0
        categorize_file "$file" || rc=$?
        case "$rc" in
            0) SOURCE_FILES=$((SOURCE_FILES + 1)) ;;
            1) TEST_FILES=$((TEST_FILES + 1)) ;;
            2) DOC_FILES=$((DOC_FILES + 1)) ;;
            3) CONFIG_FILES=$((CONFIG_FILES + 1)) ;;
        esac
    fi
done <<< "$ALL_CHANGED_FILES"

echo -e "${GREEN}File Categories:${NC}"
echo "  Source files:  $SOURCE_FILES"
echo "  Test files:    $TEST_FILES"
echo "  Doc files:     $DOC_FILES"
echo "  Config files:  $CONFIG_FILES"
echo ""

# Determine recommended strategy
STRATEGY="direct"
REASON=""

if [ "$TOTAL_FILES" -ge 4 ]; then
    if [ "$SOURCE_FILES" -ge 3 ]; then
        STRATEGY="agents"
        REASON="Multiple source files changed (${SOURCE_FILES}), suggesting multiple features or substantial changes"
    fi
elif [ "$TOTAL_FILES" -le 3 ] && [ "$SOURCE_FILES" -le 2 ]; then
    STRATEGY="direct"
    REASON="Small change (${TOTAL_FILES} files), can be handled directly"
fi

# Check for multiple logical changes by looking at directory distribution
UNIQUE_DIRS=$(echo "$ALL_CHANGED_FILES" | grep -v '^$' | while read -r f; do dirname "$f"; done | sort -u | wc -l | tr -d ' ')
if [ "$UNIQUE_DIRS" -ge 4 ]; then
    STRATEGY="agents"
    REASON="Changes span multiple directories (${UNIQUE_DIRS}), suggesting multiple features"
fi

echo -e "${YELLOW}Recommended Strategy: ${STRATEGY}${NC}"
echo "  Reasoning: $REASON"
echo ""

# Suggest scopes based on changed directories
if [ "$UNIQUE_DIRS" -gt 0 ]; then
    echo -e "${GREEN}Changed Directories:${NC}"
    echo "$ALL_CHANGED_FILES" | grep -v '^$' | xargs -r dirname | sort -u | while IFS= read -r dir; do
        # Count by fixed-string prefix: dir comes from the filesystem and may
        # contain regex metacharacters (e.g. app/[[...slug]]), so grep would
        # either miscount or fail with exit 2. awk index() cannot fail.
        file_count=$(awk -v p="${dir}/" 'index($0, p) == 1 {n++} END {print n + 0}' <<<"$ALL_CHANGED_FILES")
        echo "  - $dir/ ($file_count files)"
    done
    echo ""
fi

# Suggest commit scope based on most changed directory
if [ "$TOTAL_FILES" -gt 0 ]; then
    SUGGESTED_SCOPE=$(echo "$ALL_CHANGED_FILES" | grep -v '^$' | xargs -r dirname | sort | uniq -c | sort -rn | head -1 | awk '{print $2}' | xargs basename)
    if [ -n "$SUGGESTED_SCOPE" ] && [ "$SUGGESTED_SCOPE" != "." ]; then
        echo -e "${GREEN}Suggested Commit Scope:${NC} $SUGGESTED_SCOPE"
        echo ""
    fi
fi

# Display changed files by category
show_files_by_category() {
    local category="$1"
    local pattern="$2"
    local files rc

    if [ "$category" = "Source" ]; then
        # Source files are those that don't match other patterns
        files=$(echo "$ALL_CHANGED_FILES" | while IFS= read -r file; do
            if [ -n "$file" ]; then
                rc=0
                categorize_file "$file" || rc=$?
                if [ "$rc" -eq 0 ]; then
                    echo "$file"
                fi
            fi
        done)
    else
        files=$(echo "$ALL_CHANGED_FILES" | grep -E "$pattern" || true)
    fi

    if [ -n "$files" ]; then
        echo -e "${GREEN}${category} Files:${NC}"
        echo "$files" | while IFS= read -r file; do
            [ -n "$file" ] && echo "  - $file"
        done
        echo ""
    fi
}

echo -e "${BLUE}=== Changed Files by Category ===${NC}\n"

# Show source files
show_files_by_category "Source" ""

# Show test files
show_files_by_category "Test" '(test|spec|__tests__|\.test\.|\.spec\.)'

# Show documentation files
show_files_by_category "Documentation" '\.(md|txt|rst|adoc)$|^docs/'

# Show config files
show_files_by_category "Configuration" '\.(json|yaml|yml|toml|ini|conf|config)$|^\.|package\.json|tsconfig|eslint|prettier'

# Final recommendation
echo -e "${BLUE}=== Recommendations ===${NC}\n"

if [ "$STRATEGY" = "direct" ]; then
    echo -e "${GREEN}Recommended Approach: Direct Commit${NC}"
    echo ""
    echo "This is a small, focused change. Handle it directly:"
    echo "  1. Review the changes carefully"
    echo "  2. Write a conventional commit message"
    echo "  3. Stage and commit in one operation"
    echo "  4. Documentation: Only if substantial feature change"
    echo ""
else
    echo -e "${YELLOW}Recommended Approach: Agent Deployment${NC}"
    echo ""
    echo "This is a larger change spanning multiple areas. Consider:"
    echo "  1. Group changes by feature or logical area"
    echo "  2. Deploy parallel docs-git-committer agents"
    echo "  3. One agent per feature scope"
    echo "  4. Each agent handles documentation + commit"
    echo ""

    # Suggest potential agent scopes
    echo "Potential agent scopes:"
    echo "$ALL_CHANGED_FILES" | grep -v '^$' | xargs -r dirname | sort | uniq -c | sort -rn | head -5 | while read -r count dir; do
        scope=$(basename "$dir")
        if [ "$scope" != "." ]; then
            echo "  - Agent for: $scope ($count files)"
        fi
    done
    echo ""
fi

# Check for common patterns that might need documentation
NEEDS_DOCS=false
# A here-string avoids SIGPIPE from an early grep match under pipefail.
if grep -qE '^(src/|lib/|app/)' <<<"$ALL_CHANGED_FILES"; then
    if [ "$SOURCE_FILES" -ge 3 ]; then
        NEEDS_DOCS=true
    fi
fi

if [ "$NEEDS_DOCS" = true ]; then
    echo -e "${YELLOW}Documentation Consideration:${NC}"
    echo ""
    echo "Based on the changes, consider if documentation is needed:"
    echo "  - New features -> Feature documentation (docs/features/)"
    echo "  - API changes -> API documentation (docs/api/)"
    echo "  - Architecture changes -> Architecture docs (docs/architecture/)"
    echo "  - CLAUDE.md updates -> Only if critical, directory-specific pattern"
    echo ""
fi

exit 0
