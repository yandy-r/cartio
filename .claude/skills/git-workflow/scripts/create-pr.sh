#!/usr/bin/env bash
set -euo pipefail

# create-pr.sh
# Analyzes git repository and helps create pull requests
# Usage: create-pr.sh [--analyze | --create [--draft]] [--base BRANCH]
#
# The PR base is --base when given, else the RELEASING.md base
# (release-state.sh base), else the forge default branch. With RELEASING.md,
# the sync-merge guard runs first and refuses a sync PR. Both come from
# _shared/scripts/pr-guard.sh; see _shared/references/pr-base-and-backport.md.

# Color codes for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Parse arguments
MODE="analyze"
DRAFT=false
BASE_ARG=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --analyze)
            MODE="analyze"
            shift
            ;;
        --create)
            MODE="create"
            shift
            ;;
        --draft)
            DRAFT=true
            shift
            ;;
        --base)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo -e "${RED}--base requires a branch name${NC}" >&2
                exit 1
            fi
            BASE_ARG="$2"
            shift 2
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}" >&2
            echo "Usage: $0 [--analyze | --create [--draft]] [--base BRANCH]" >&2
            exit 1
            ;;
    esac
done

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo -e "${RED}Error: Not in a git repository${NC}" >&2
    exit 1
fi

# Detect the forge provider and select the matching CLI (gh / tea / glab).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
FORGE_LIB="${SCRIPT_DIR}/../../_shared/scripts/lib/forge.sh"
[[ -f "$FORGE_LIB" ]] || FORGE_LIB=".claude/skills/_shared/scripts/lib/forge.sh"
# shellcheck source=/dev/null
source "$FORGE_LIB"
PR_GUARD="${SCRIPT_DIR}/../../_shared/scripts/pr-guard.sh"
[[ -f "$PR_GUARD" ]] || PR_GUARD=".claude/skills/_shared/scripts/pr-guard.sh"

PROVIDER="$(forge_detect_provider origin)"
FORGE_CLI="$(forge_cli "$PROVIDER")"

case "$PROVIDER" in
    github|forgejo|gitea) ;;  # supported for PR creation
    gitlab)
        echo -e "${RED}Error: GitLab PR creation is not yet supported by this script${NC}" >&2
        echo "Create the merge request with: glab mr create" >&2
        exit 1
        ;;
    *)
        echo -e "${RED}Error: Could not determine the forge provider for 'origin'${NC}" >&2
        echo "origin = $(forge_remote_url origin)" >&2
        echo "Supported: GitHub (gh), Forgejo/Gitea (tea)." >&2
        exit 1
        ;;
esac

# Check the matching CLI is installed
if ! command -v "$FORGE_CLI" &> /dev/null; then
    echo -e "${RED}Error: ${FORGE_CLI} CLI (for ${PROVIDER}) is not installed${NC}" >&2
    echo "" >&2
    case "$PROVIDER" in
        github) echo "Install gh:  https://github.com/cli/cli#installation" >&2 ;;
        *)      echo "Install tea: https://gitea.com/gitea/tea#installation" >&2 ;;
    esac
    exit 1
fi

# Check authentication for the detected provider
if ! forge_auth_ok "$PROVIDER"; then
    echo -e "${RED}Error: Not authenticated with ${FORGE_CLI} (${PROVIDER})${NC}" >&2
    echo "" >&2
    case "$PROVIDER" in
        github) echo "Authenticate with: gh auth login" >&2 ;;
        *)      echo "Authenticate with: tea login add" >&2 ;;
    esac
    exit 1
fi

echo -e "${BLUE}Forge provider:${NC} ${PROVIDER} (using ${FORGE_CLI})" >&2

# Get current branch
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

if [ "$CURRENT_BRANCH" = "HEAD" ]; then
    echo -e "${RED}Error: Not on a branch (detached HEAD)${NC}" >&2
    exit 1
fi

# Resolve the PR base: --base, else RELEASING.md, else the forge default branch.
if [[ -n "$BASE_ARG" ]]; then
    BASE_CMD=(bash "$PR_GUARD" base --base "$BASE_ARG")
else
    BASE_CMD=(bash "$PR_GUARD" base)
fi
if ! BASE_BRANCH=$("${BASE_CMD[@]}"); then
    echo -e "${RED}Error: could not resolve the PR base (see the RELEASING.md error above)${NC}" >&2
    echo 'Fix the state block with /release-model --audit, or pass --base BRANCH.' >&2
    exit 1
fi

# Check if branch exists on remote
if ! git ls-remote --exit-code --heads origin "$CURRENT_BRANCH" &> /dev/null; then
    echo -e "${YELLOW}⚠ Branch not pushed to remote${NC}" >&2
    echo "Push with: git push -u origin $CURRENT_BRANCH" >&2
    exit 1
fi

echo -e "${BLUE}=== Pull Request Analysis ===${NC}\n"

# Display branch information
echo -e "${GREEN}Branch Information:${NC}"
echo "  Current branch:  $CURRENT_BRANCH"
echo "  Base branch:     $BASE_BRANCH"
echo ""

# Sync-merge guard (skipped without RELEASING.md).
set +e
GUARD_OUT=$(bash "$PR_GUARD" sync-check --base "$BASE_BRANCH" 2>&1)
GUARD_RC=$?
set -e
if [ "$GUARD_RC" -eq 3 ]; then
    echo -e "${RED}Error: refusing to open this PR into ${BASE_BRANCH} (sync merge)${NC}" >&2
    printf '%s\n' "$GUARD_OUT" | sed 's/^/  /' >&2
    exit 1
elif [ "$GUARD_RC" -ne 0 ]; then
    echo -e "${RED}Error: the sync-merge guard could not run${NC}" >&2
    printf '%s\n' "$GUARD_OUT" | sed 's/^/  /' >&2
    exit 1
fi

# Get commits since divergence from base
MERGE_BASE=$(git merge-base HEAD "origin/$BASE_BRANCH" 2>/dev/null || echo "")

if [ -z "$MERGE_BASE" ]; then
    echo -e "${RED}Error: Cannot find common ancestor with $BASE_BRANCH${NC}" >&2
    exit 1
fi

# Count commits
COMMIT_COUNT=$(git rev-list --count "$MERGE_BASE..HEAD")

echo -e "${GREEN}Commits in this Branch:${NC}"
echo "  Total commits: $COMMIT_COUNT"
echo ""

if [ "$COMMIT_COUNT" -eq 0 ]; then
    echo -e "${YELLOW}No new commits on this branch${NC}"
    echo "Nothing to create PR for."
    exit 0
fi

# Display commit history
echo -e "${GREEN}Commit History:${NC}"
git log --oneline --no-merges "$MERGE_BASE..HEAD" | while IFS= read -r line; do
    echo "  - $line"
done
echo ""

# Analyze changed files
CHANGED_FILES=$(git diff --name-only "$MERGE_BASE..HEAD" | wc -l | tr -d ' ')
# grep -c exits 1 on zero matches; `|| true` keeps pipefail from aborting.
SOURCE_FILES=$(git diff --name-only "$MERGE_BASE..HEAD" | grep -cvE '(test|spec|__tests__|\.test\.|\.spec\.|\.md$|^docs/)' || true)
TEST_FILES=$(git diff --name-only "$MERGE_BASE..HEAD" | grep -cE '(test|spec|__tests__|\.test\.|\.spec\.)' || true)
DOC_FILES=$(git diff --name-only "$MERGE_BASE..HEAD" | grep -cE '(\.md$|^docs/)' || true)

echo -e "${GREEN}Files Changed:${NC}"
echo "  Total files:   $CHANGED_FILES"
echo "  Source files:  $SOURCE_FILES"
echo "  Test files:    $TEST_FILES"
echo "  Doc files:     $DOC_FILES"
echo ""

# Get change statistics
STATS=$(git diff --stat "$MERGE_BASE..HEAD" | tail -n1)
echo -e "${GREEN}Change Statistics:${NC}"
echo "  $STATS"
echo ""

# Suggest PR title based on commits
echo -e "${GREEN}Suggested PR Title:${NC}"

if [ "$COMMIT_COUNT" -eq 1 ]; then
    # Single commit - use commit message
    SUGGESTED_TITLE=$(git log --format=%s -n1 HEAD)
    echo "  $SUGGESTED_TITLE"
    echo "  (from single commit)"
else
    # Multiple commits - analyze for common theme
    # Try to extract type and scope from first commit
    FIRST_COMMIT=$(git log --format=%s -n1 HEAD)

    if grep -qE '^[a-z]+(\([a-z0-9-]+\))?:' <<<"$FIRST_COMMIT"; then
        TYPE=$(echo "$FIRST_COMMIT" | grep -oE '^[a-z]+')
        SCOPE=$(echo "$FIRST_COMMIT" | grep -oE '\([a-z0-9-]+\)' | tr -d '()' || echo "")

        if [ -n "$SCOPE" ]; then
            SUGGESTED_TITLE="${TYPE}(${SCOPE}): [describe overall changes]"
            echo "  $SUGGESTED_TITLE"
        else
            SUGGESTED_TITLE="${TYPE}: [describe overall changes]"
            echo "  $SUGGESTED_TITLE"
        fi
        echo "  (based on commit pattern)"
    else
        # No conventional commit pattern detected
        SUGGESTED_TITLE=""
        echo "  [type](scope): [describe changes]"
        echo "  (no clear pattern detected)"
    fi
fi
echo ""

# Check for documentation
echo -e "${GREEN}Documentation Status:${NC}"
if [ "$DOC_FILES" -gt 0 ]; then
    echo "  ✓ Documentation changes detected ($DOC_FILES files)"
    git diff --name-only "$MERGE_BASE..HEAD" | grep -E '(\.md$|^docs/)' | while IFS= read -r file; do
        echo "    - $file"
    done
else
    echo "  ⚠ No documentation files changed"
    if [ "$SOURCE_FILES" -ge 3 ]; then
        echo "    Consider if documentation is needed for these changes"
    fi
fi
echo ""

# Check for breaking changes in commits. Capture git log output first and
# match with a here-string, not `git log | grep -q`: under pipefail the
# early-exiting grep SIGPIPEs git once the log exceeds the pipe buffer and a
# present BREAKING CHANGE reads as absent (YAN-1587).
COMMIT_MESSAGES=$(git log --format=%B "$MERGE_BASE..HEAD")
echo -e "${GREEN}Breaking Changes:${NC}"
if grep -qE 'BREAKING CHANGE:|^[a-z]+(\([a-z0-9-]+\))?!:' <<<"$COMMIT_MESSAGES"; then
    echo "  ⚠ Breaking changes detected in commits"
    { git log --format=%s "$MERGE_BASE..HEAD" | grep -E '^[a-z]+(\([a-z0-9-]+\))?!:' || true; } | while IFS= read -r line; do
        echo "    - $line"
    done
    { git log --format=%B "$MERGE_BASE..HEAD" | grep -A2 'BREAKING CHANGE:' || true; } | while IFS= read -r line; do
        if [ -n "$line" ]; then echo "    $line"; fi
    done
else
    echo "  ✓ No breaking changes detected"
fi
echo ""

# Recommend PR type
echo -e "${YELLOW}Recommended PR Type:${NC}"
if [ "$TEST_FILES" -eq 0 ] || [ "$SOURCE_FILES" -ge 10 ]; then
    echo "  Draft PR (use --draft flag)"
    echo "  Reasoning:"
    [ "$TEST_FILES" -eq 0 ] && echo "    - No test files changed (tests may be incomplete)"
    [ "$SOURCE_FILES" -ge 10 ] && echo "    - Large change ($SOURCE_FILES source files)"
else
    echo "  Regular PR (ready for review)"
    echo "  Reasoning:"
    echo "    - Tests included ($TEST_FILES test files)"
    echo "    - Reasonable size ($SOURCE_FILES source files)"
fi
echo ""

# If analyze mode, stop here
if [ "$MODE" = "analyze" ]; then
    echo -e "${BLUE}=== Analysis Complete ===${NC}\n"
    echo "To create PR:"
    if [ "$PROVIDER" = "github" ]; then
        echo "  Regular: gh pr create --base $BASE_BRANCH --web"
        echo "  Draft:   gh pr create --base $BASE_BRANCH --draft --web"
    else
        echo "  Regular: tea pull create --head $CURRENT_BRANCH --base $BASE_BRANCH"
        echo "  (Forgejo/Gitea: 'tea' has no --draft or --web; open the PR in the web UI to mark it draft)"
    fi
    exit 0
fi

# Create PR mode
echo -e "${BLUE}=== Creating Pull Request ===${NC}\n"

# Check if PR already exists
if [ "$PROVIDER" = "github" ]; then
    EXISTING_PR=$(gh pr list --head "$CURRENT_BRANCH" --json number --jq '.[0].number' 2>/dev/null || echo "")
else
    # tea pull list — match the head branch column; best-effort across tea versions
    EXISTING_PR=$(tea pull list --output simple 2>/dev/null | grep -F "$CURRENT_BRANCH" | grep -oE '^#?[0-9]+' | head -n1 | tr -d '#' || echo "")
fi

if [ -n "$EXISTING_PR" ]; then
    echo -e "${YELLOW}⚠ PR already exists for this branch: #$EXISTING_PR${NC}"
    echo ""
    if [ "$PROVIDER" = "github" ]; then
        echo "View PR: gh pr view $EXISTING_PR"
        echo "Edit PR: gh pr edit $EXISTING_PR"
    else
        echo "View PR: tea pull $EXISTING_PR"
    fi
    exit 1
fi

# Generate PR description template
# Set PR_TITLE with fallback if SUGGESTED_TITLE is empty
if [ -n "$SUGGESTED_TITLE" ]; then
    PR_TITLE="$SUGGESTED_TITLE"
else
    # Fall back to most recent commit subject
    PR_TITLE=$(git log -1 --pretty=%s)
    echo -e "${YELLOW}⚠ Using most recent commit message as PR title${NC}"
    echo "  Title: $PR_TITLE"
    echo ""
fi

# Related issues: Linear branch hints and commit keys from the shared helper,
# filtered by ARTIFACT_LINEAR_TEAM, plus GitHub issues referenced in commits.
# See _shared/references/artifact-store.md "PR linking". Missing or failing
# helpers never prevent PR creation; session-aware plan links are added by callers.
LINEAR_KEYS_SCRIPT="${SCRIPT_DIR}/../../_shared/scripts/collect-linear-keys.sh"
[[ -f "$LINEAR_KEYS_SCRIPT" ]] || LINEAR_KEYS_SCRIPT=".claude/skills/_shared/scripts/collect-linear-keys.sh"
RESOLVE_STORE_SCRIPT="${SCRIPT_DIR}/../../_shared/scripts/resolve-artifact-store.sh"
[[ -f "$RESOLVE_STORE_SCRIPT" ]] || RESOLVE_STORE_SCRIPT=".claude/skills/_shared/scripts/resolve-artifact-store.sh"

LINEAR_TEAM="${ARTIFACT_LINEAR_TEAM:-}"
if [ -z "$LINEAR_TEAM" ] && [ -f "$RESOLVE_STORE_SCRIPT" ]; then
    LINEAR_TEAM=$(bash "$RESOLVE_STORE_SCRIPT" --shell 2>/dev/null | awk '
        /^ARTIFACT_LINEAR_TEAM=/ { sub(/^ARTIFACT_LINEAR_TEAM=/, ""); if ($0 != "\047\047") print }
    ' || true)
fi

RELATED_ISSUES=""
if [ -f "$LINEAR_KEYS_SCRIPT" ]; then
    LINEAR_KEYS_OUT=$(bash "$LINEAR_KEYS_SCRIPT" --base "origin/$BASE_BRANCH" --team "$LINEAR_TEAM" 2>/dev/null || true)
    if [ -n "$LINEAR_TEAM" ]; then
        while IFS=$'\t' read -r LINEAR_KEY _LINEAR_SRC LINEAR_ACTION; do
            [ -n "$LINEAR_KEY" ] || continue
            case "$LINEAR_ACTION" in
                closes) LINEAR_VERB=Closes ;;
                refs) LINEAR_VERB=Refs ;;
                *) continue ;;
            esac
            RELATED_ISSUES="${RELATED_ISSUES:+${RELATED_ISSUES}
}${LINEAR_VERB} ${LINEAR_KEY}"
        done <<<"$LINEAR_KEYS_OUT"
    elif [ -n "$LINEAR_KEYS_OUT" ]; then
        echo 'Skipping Linear issue links: no ARTIFACT_LINEAR_TEAM configured and no Linear MCP to verify keys.' >&2
    fi
fi

# GitHub issues referenced in commit messages (Closes/Fixes/Resolves #n).
GH_REFS=$(grep -oiE '(closes|fixes|resolves) #[0-9]+' <<<"$COMMIT_MESSAGES" 2>/dev/null | grep -oE '#[0-9]+' | awk '!seen[$0]++' || true)
while IFS= read -r GH_REF; do
    [ -n "$GH_REF" ] || continue
    RELATED_ISSUES="${RELATED_ISSUES:+${RELATED_ISSUES}
}Closes ${GH_REF}"
done <<<"$GH_REFS"

# No linked issues: keep the bare placeholder for the author to fill in.
[ -n "$RELATED_ISSUES" ] || RELATED_ISSUES="Closes #"

# Create description based on commits
PR_DESCRIPTION="## Summary

[Describe what this PR does and why]

## Changes

"

# Add commits to changes section
while IFS= read -r line; do
    PR_DESCRIPTION="${PR_DESCRIPTION}${line}\n"
done < <(git log --format="- %s" --no-merges "$MERGE_BASE..HEAD")

PR_DESCRIPTION="${PR_DESCRIPTION}
## Documentation

"

if [ "$DOC_FILES" -gt 0 ]; then
    PR_DESCRIPTION="${PR_DESCRIPTION}### Updated
"
    while IFS= read -r file; do
        PR_DESCRIPTION="${PR_DESCRIPTION}- ${file}\n"
    done < <(git diff --name-only "$MERGE_BASE..HEAD" | grep -E '(\.md$|^docs/)')
else
    PR_DESCRIPTION="${PR_DESCRIPTION}- No documentation changes
"
fi

PR_DESCRIPTION="${PR_DESCRIPTION}
## Testing

### How to test:
1. [Step one]
2. [Step two]
3. [Expected result]

## Related Issues

${RELATED_ISSUES}

## Breaking Changes

- No breaking changes

## Checklist

- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] All tests passing
"

# Create the PR
echo "Creating PR..."
echo ""

if [ "$PROVIDER" = "github" ]; then
    if [ "$DRAFT" = true ]; then
        set +e
        gh pr create \
            --title "$PR_TITLE" \
            --body "$PR_DESCRIPTION" \
            --base "$BASE_BRANCH" \
            --draft \
            --web
        rc=$?
        set -e

        if [ $rc -eq 0 ]; then
            echo ""
            echo -e "${GREEN}✓ Draft PR created successfully${NC}"
            echo ""
            echo "Mark ready when complete: gh pr ready"
        else
            echo ""
            echo -e "${RED}✗ Failed to create PR${NC}"
            exit 1
        fi
    else
        set +e
        gh pr create \
            --title "$PR_TITLE" \
            --body "$PR_DESCRIPTION" \
            --base "$BASE_BRANCH" \
            --web
        rc=$?
        set -e

        if [ $rc -eq 0 ]; then
            echo ""
            echo -e "${GREEN}✓ PR created successfully${NC}"
        else
            echo ""
            echo -e "${RED}✗ Failed to create PR${NC}"
            exit 1
        fi
    fi
else
    # Forgejo / Gitea via tea. No --draft or --web support; create directly.
    if [ "$DRAFT" = true ]; then
        echo -e "${YELLOW}⚠ 'tea' does not support draft PRs; creating a normal PR.${NC}" >&2
        echo "  Mark it draft in the ${PROVIDER} web UI if needed." >&2
        echo ""
    fi
    set +e
    tea pull create \
        --title "$PR_TITLE" \
        --description "$PR_DESCRIPTION" \
        --head "$CURRENT_BRANCH" \
        --base "$BASE_BRANCH"
    rc=$?
    set -e

    if [ $rc -eq 0 ]; then
        echo ""
        echo -e "${GREEN}✓ PR created successfully${NC}"
    else
        echo ""
        echo -e "${RED}✗ Failed to create PR${NC}"
        exit 1
    fi
fi

exit 0
