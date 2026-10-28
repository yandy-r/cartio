# Agent Prompt Templates

This document provides standardized prompt templates for deploying `docs-git-committer` agents during the git workflow.

---

## Standard Agent Prompt Structure

All docs-git-committer agents should receive prompts following this structure:

````
You are handling the git commit and documentation for: [FEATURE SCOPE]

## Standard: Changed Files in Your Scope

[List of files related to this feature]

## Standard: Context

[Brief description of what changed and why]

## Standard: Documentation Templates

Use the built-in structure for each doc type from the docs-git-committer agent (or a project template if one exists):
- Feature docs: built-in feature doc structure
- CLAUDE.md: built-in CLAUDE.md structure
- Architecture docs: built-in architecture structure
- API docs: built-in API structure

## Standard: Your Tasks

1. Review the changes in your assigned files
2. Determine if documentation updates are needed (use documentation-decision.md)
3. Update or create documentation if appropriate:
   - Feature docs: docs/features/[name].doc.md
   - CLAUDE.md: Only if critically needed in specific directory (RARELY)
   - Architecture docs: docs/architecture/ if system-wide changes
   - API docs: docs/api/ if public API changes
4. Stage all files (source + docs) and commit with conventional message
5. Return summary of what you committed

## Standard: Important Constraints

- **ALWAYS use conventional commit format** — this is mandatory, not optional
- Read the reference BEFORE writing your commit message: .claude/skills/git-workflow/templates/commit-types.md
- Validate your commit message BEFORE committing:
  ```bash
  .claude/skills/git-workflow/scripts/validate-commit.sh "<your-commit-message>"
````

If validation fails, revise the message and re-validate until it passes.

- Combine source + docs in ONE commit
- Use git add + commit in single command (avoid race conditions)
- Use only git CLI commands for version control (git add, git commit)
- GitHub operations (PRs, issues) are NOT your responsibility — the main skill handles those
- Do NOT push (main skill handles that)
- Focus ONLY on your assigned scope
- Resolve the documentation structure BEFORE creating documentation (dispatch template, project template if present, then built-in structure)

## Documentation Decision Guidance

Refer to: .claude/skills/git-workflow/templates/documentation-decision.md

Quick reminders:

- Feature docs: For new user-facing features or significant API changes
- CLAUDE.md: RARELY needed, only for critical directory-specific patterns
- Architecture docs: For system-wide architectural changes
- Skip documentation: For refactoring, minor bug fixes, or trivial changes

## Expected Output

Return a summary including:

- Commit hash and message
- Files committed (source + docs)
- Documentation created/updated (if any)
- Reasoning for documentation decisions

```

---

## Template Variations by Change Type

### New Feature Implementation

```

You are handling the git commit and documentation for: [FEATURE NAME]

## New Feature: Changed Files in Your Scope

[List of implementation files]

## New Feature: Context

This is a NEW FEATURE that [description of what it does].

Key changes:

- [File 1]: [what was added/changed]
- [File 2]: [what was added/changed]
- [File 3]: [what was added/changed]

## New Feature: Documentation Templates

Read BEFORE documenting:

- Feature docs: use the built-in feature doc structure from the docs-git-committer agent (or a project template if one exists)

## New Feature: Your Tasks

1. Review the implementation files
2. **Create feature documentation** at docs/features/[feature-name].doc.md
   - Explain what the feature does (user perspective)
   - Describe the data flow
   - List key implementation files
   - Note any configuration requirements
3. Stage implementation files + feature docs
4. Commit with message format: feat([scope]): [description]
5. Return summary

## New Feature: Important

- This is a new feature, so feature docs ARE NEEDED
- Keep docs concise and user-focused
- Use the built-in feature doc structure from the docs-git-committer agent (or a project template if one exists)
- Commit message should use "feat" type

```

### Bug Fix

```

You are handling the git commit and documentation for: [BUG DESCRIPTION]

## Bug Fix: Changed Files in Your Scope

[List of fixed files]

## Bug Fix: Context

This fixes a bug where [description of the problem and solution].

## Bug Fix: Your Tasks

1. Review the bug fix
2. **Skip documentation** - bug fixes don't need docs unless behavior changes
3. Stage the fixed files
4. Commit with message format: fix([scope]): [description]
5. Return summary

## Bug Fix: Important

- Bug fixes typically DON'T need documentation
- Use "fix" type in commit message
- Explain the fix in commit body if complex
- Do NOT create feature docs for simple bug fixes

```

### API Changes

```

You are handling the git commit and documentation for: [API CHANGES]

## API Changes: Changed Files in Your Scope

[List of API-related files]

## API Changes: Context

This changes the following APIs:

- [Endpoint 1]: [what changed]
- [Endpoint 2]: [what changed]

## API Changes: Documentation Templates

Read BEFORE documenting:

- API docs: use the built-in API structure from the docs-git-committer agent (or a project template if one exists)
- Feature docs: use the built-in feature doc structure (if needed)

## API Changes: Your Tasks

1. Review the API changes
2. **Update API documentation** at docs/api/[relevant-file].md
   - Document new/changed endpoints
   - Include request/response examples
   - Note breaking changes if any
3. If breaking changes, also update feature docs
4. Stage API files + documentation
5. Commit with message format: feat([scope]): [description] or feat([scope])!: if breaking
6. Return summary

## API Changes: Important

- API changes ALWAYS need API docs updated
- Breaking changes need BREAKING CHANGE footer
- Include before/after examples for changed APIs
- Use "feat" or "fix" type depending on change nature

```

### Refactoring

```

You are handling the git commit and documentation for: [REFACTORING DESCRIPTION]

## Refactoring: Changed Files in Your Scope

[List of refactored files]

## Refactoring: Context

This refactors [what was refactored] to [why it was refactored].

No functional changes - purely organizational/quality improvements.

## Refactoring: Your Tasks

1. Review the refactoring
2. **Skip documentation** - refactoring without behavior change doesn't need docs
3. Stage the refactored files
4. Commit with message format: refactor([scope]): [description]
5. Return summary

## Refactoring: Important

- Refactoring typically does NOT need documentation
- Use "refactor" type in commit message
- Emphasize "no functional changes" in commit body
- Do NOT create feature docs or CLAUDE.md updates

```

### Security Pattern Introduction

```

You are handling the git commit and documentation for: [SECURITY FEATURE]

## Security Pattern: Changed Files in Your Scope

[List of security-related files]

## Security Pattern: Context

This introduces [security measure] to [protect against what].

This is a CRITICAL security pattern that must be followed consistently.

## Security Pattern: Documentation Templates

Read BEFORE documenting:

- CLAUDE.md: use the built-in CLAUDE.md structure from the docs-git-committer agent (or a project template if one exists)
- Architecture docs: use the built-in architecture structure from the docs-git-committer agent (or a project template if one exists)

## Security Pattern: Your Tasks

1. Review the security implementation
2. **Consider CLAUDE.md update** - IF this is a critical pattern for the specific directory
   - Update [directory]/CLAUDE.md if this pattern MUST be followed
   - Keep it under 50 lines
   - Be specific about the requirement
   - Link to detailed docs
3. **Create architecture documentation** at docs/architecture/security.md
   - Document the security strategy
   - Explain configuration
   - Provide examples
4. Stage security files + documentation
5. Commit with message format: feat([scope]): [description]
6. Return summary

## Security Pattern: Important

- Security patterns MAY warrant CLAUDE.md updates (but rarely)
- Always create architecture docs for security measures
- Be very clear about requirements
- Use "feat" type for new security features

```

---

## Agent Deployment Examples

### Example 1: Single Feature

**Scenario**: User implemented authentication system

**Agent Deployment**:

```

Deploy 1 docs-git-committer agent:

Agent 1 - Authentication Feature
Scope: auth system implementation
Files: src/auth/, src/middleware/auth.ts, tests/auth.test.ts
Instructions: [Use "New Feature Implementation" template]
Expected: Feature docs + commit

```

### Example 2: Multiple Independent Features

**Scenario**: User implemented auth system + payment integration

**Agent Deployment** (in parallel):

```

Deploy 2 docs-git-committer agents IN SINGLE MESSAGE:

Agent 1 - Authentication Feature
Scope: auth system
Files: src/auth/, src/middleware/auth.ts
Instructions: [Use "New Feature Implementation" template]

Agent 2 - Payment Integration
Scope: payment processing
Files: src/payments/, src/api/payments.ts
Instructions: [Use "New Feature Implementation" template]

```

### Example 3: Feature + Tests + Docs

**Scenario**: Feature implementation with separate test and doc changes

**Agent Deployment** (in parallel):

```

Deploy 3 docs-git-committer agents IN SINGLE MESSAGE:

Agent 1 - Core Feature
Scope: user profile feature
Files: src/components/Profile.tsx, src/hooks/useProfile.ts
Instructions: [Use "New Feature Implementation" template]
Expected: Feature docs + commit

Agent 2 - Tests
Scope: user profile tests
Files: tests/profile.test.tsx, tests/integration/profile.e2e.ts
Instructions: [Use "Bug Fix" template modified for tests]
Expected: Commit only (no docs needed for tests)

Agent 3 - API Documentation
Scope: profile API docs
Files: docs/api/profile.md (existing file to update)
Instructions: [Use "API Changes" template]
Expected: API docs update + commit

```

---

## Agent Scope Guidelines

### Good Scope Definition

✅ **Clear boundaries**:

- "Authentication system (login, JWT, middleware)"
- "Payment integration (Stripe API, webhooks)"
- "User profile feature (UI components, API)"

✅ **Independent commits**:

- Each agent can commit without waiting for others
- No file overlap between agents
- Changes are logically grouped

✅ **Appropriate size**:

- Not too large (> 15 files)
- Not too small (< 2 files unless complex)
- Completable in single agent session

### Bad Scope Definition

❌ **Vague boundaries**:

- "Fix stuff" (not specific)
- "Update code" (too broad)
- "Various changes" (unclear scope)

❌ **Overlapping files**:

- Agent 1 and Agent 2 both touch auth.ts
- Shared files between agents
- Risk of conflicts

❌ **Wrong size**:

- Too large: "Rewrite entire backend"
- Too small: "Fix typo in one line"

---

## Coordination Guidelines

### Single Agent Deployment

Use when:

- Small to medium changes (< 10 files)
- Single logical feature
- No natural scope divisions

**Command**: Single Agent tool call with one docs-git-committer agent

### Parallel Agent Deployment

Use when:

- Large changes (10+ files)
- Multiple distinct features
- Independent scopes

**CRITICAL**: Deploy ALL agents in SINGLE message with MULTIPLE Agent tool calls

**Example**:

```

[Single message with 3 Agent tool calls]

Task 1: docs-git-committer for auth system
Task 2: docs-git-committer for payment integration
Task 3: docs-git-committer for profile feature

```

### Sequential Agent Deployment

**Avoid this** - prefer parallel deployment

Only use sequential if:

- Agent 2 depends on Agent 1's commit
- Changes are tightly coupled
- Risk of conflicts is high

---

## Quality Checklist for Agent Prompts

Each agent prompt should have:

- [ ] Clear feature scope definition
- [ ] Complete list of files in scope
- [ ] Context about what changed and why
- [ ] Reference to appropriate templates
- [ ] Specific task instructions
- [ ] Documentation decision guidance
- [ ] Constraints (no push, single scope, etc.)
- [ ] Expected output format

---

## Common Pitfalls to Avoid

### Pitfall 1: Too Many Agents

**Problem**: Deploying 10+ agents for changes that could be 2-3

**Solution**: Group related changes, combine scopes when logical

### Pitfall 2: Overlapping Scopes

**Problem**: Multiple agents touching same files

**Solution**: Define clear boundaries, ensure no file overlap

### Pitfall 3: Missing Context

**Problem**: Agent doesn't understand what changed

**Solution**: Include "Context" section explaining changes

### Pitfall 4: No Documentation Guidance

**Problem**: Agent doesn't know when to document

**Solution**: Reference documentation-decision.md in prompt

### Pitfall 5: Sequential Deployment

**Problem**: Deploying agents one at a time

**Solution**: Deploy all agents in single message with multiple Agent calls

---

## Summary

**Key Principles**:

1. Use standard prompt structure for consistency
2. Provide clear scope and file list
3. Reference appropriate templates
4. Include documentation decision guidance
5. Deploy agents in parallel (single message, multiple Agent calls)
6. Ensure non-overlapping scopes
7. Specify expected output format

**Remember**: The goal is parallel, coordinated work where each agent handles complete commit (source + docs) for their scope.
```
