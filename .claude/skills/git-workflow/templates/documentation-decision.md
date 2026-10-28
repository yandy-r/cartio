# Documentation Decision Tree

This document provides comprehensive guidance on when and what to document after code changes.

## Overview Decision Flow

```
Code changes made
    │
    ▼
Does this change affect user-facing behavior or APIs?
    │
    ├─ YES ──> Consider Feature Documentation (see below)
    │
    └─ NO ──> Is this a critical architectural change?
        │
        ├─ YES ──> Consider Architecture Docs (see below)
        │
        └─ NO ──> Skip documentation (internal change only)
```

---

## Feature Documentation (docs/features/)

### When to Create/Update Feature Documentation

**✅ DO document when:**

1. **New user-facing features**
   - New screens, pages, or UI components
   - New user workflows or capabilities
   - Features users will directly interact with

2. **Significant API changes**
   - New public endpoints
   - Changed endpoint behavior
   - Modified request/response formats
   - Breaking changes to APIs

3. **Complex data flows**
   - Multi-step processes spanning multiple services
   - Data transformations users should understand
   - Integration points between systems

4. **Breaking changes**
   - Changes that require user action
   - Incompatible modifications to existing features
   - Migrations users must perform

5. **New integrations**
   - Third-party service integrations
   - External API connections
   - Webhook implementations

**❌ DON'T document when:**

1. **Internal refactoring**
   - Code reorganization without behavior changes
   - Performance improvements (unless dramatic)
   - Internal API changes not exposed to users

2. **Bug fixes**
   - Unless the bug fix changes expected behavior
   - Fixes that restore intended functionality
   - Security patches (mention in release notes instead)

3. **Style/formatting changes**
   - Prettier, linting fixes
   - Code formatting standardization
   - Whitespace adjustments

4. **Test changes**
   - Adding or updating tests
   - Test infrastructure improvements
   - Test data updates

5. **Minor improvements**
   - Small UX tweaks
   - Performance optimizations (< 20% improvement)
   - Logging additions

### Feature Documentation Template Location

**Template**: built-in feature doc structure from the docs-git-committer agent (or a project template if one exists)

**Output location**: `docs/features/[feature-name].doc.md`

### Feature Documentation Contents

Feature docs should include:

1. **Overview**: What the feature does (user perspective)
2. **User Flow**: How users interact with it
3. **Data Flow**: How data moves through the system
4. **Implementation Files**: Key files implementing the feature
5. **Configuration**: Any settings or environment variables
6. **Limitations**: Known constraints or edge cases

**Keep it concise** - Focus on what users/developers need to know, not implementation details.

---

## CLAUDE.md Updates (RARELY NEEDED)

### Critical Understanding

**CLAUDE.md updates are RARELY NEEDED**. Most code changes do NOT warrant CLAUDE.md updates.

**NEVER update the root CLAUDE.md** - only update CLAUDE.md files in specific directories.

### When to Update CLAUDE.md

**✅ DO update when (ALL conditions must be true):**

1. **New critical pattern** introduced
   - AND it's specific to that directory
   - AND it's not obvious from the code
   - AND it will affect future development in that directory

2. **Security boundary changes**
   - AND it's within a specific directory
   - AND it creates new security considerations
   - AND developers must be aware to avoid vulnerabilities

3. **Major architectural decisions**
   - AND it fundamentally changes how that directory works
   - AND it establishes new conventions for that directory
   - AND it's not better suited for architecture docs

**❌ DON'T update when:**

1. **Root CLAUDE.md**
   - **NEVER** update the root CLAUDE.md
   - Root file is for project-wide conventions only
   - Managed separately from feature development

2. **Feature-specific details**
   - Implementation details belong in feature docs
   - User-facing changes go in feature docs
   - API documentation goes in API docs

3. **Verbose explanations**
   - CLAUDE.md should be under 50 lines per file
   - Keep it to critical patterns only
   - Link to other docs for details

4. **Obvious patterns**
   - Standard practices (REST conventions, etc.)
   - Common design patterns
   - Industry-standard approaches

5. **Most changes**
   - 95% of changes don't need CLAUDE.md updates
   - When in doubt, skip it
   - Over-documenting in CLAUDE.md dilutes critical info

### CLAUDE.md Content Guidelines

If updating CLAUDE.md:

- **Be concise**: Under 50 lines total
- **Be specific**: Only critical patterns for that directory
- **Be actionable**: Clear guidance for developers
- **Link out**: Reference detailed docs elsewhere

**Example of WHEN to update:**

````markdown
# auth/ - CLAUDE.md

## Critical Security Pattern

All auth endpoints MUST use rate limiting middleware:

```typescript
router.post('/login', rateLimitMiddleware(5, '15m'), loginHandler);
```
````

Failure to do so creates brute force vulnerability.

See docs/security/rate-limiting.md for details.

````

**Example of what NOT to add:**

```markdown
❌ DON'T ADD THIS:

## Authentication System

This directory contains the authentication system. It handles
user login, registration, and session management. The auth
service connects to the database and validates credentials...

(This belongs in feature docs, not CLAUDE.md)
````

### CLAUDE.md Template Location

**Template**: built-in CLAUDE.md structure from the docs-git-committer agent (or a project template if one exists)

**Output location**: Only in specific directories with changes, never root

---

## Architecture Documentation (docs/architecture/)

### When to Create/Update Architecture Docs

**✅ DO document when:**

1. **System-wide architectural changes**
   - New service layers
   - Changed communication patterns
   - Modified deployment architecture

2. **New service introductions**
   - Microservices added
   - Background workers created
   - Queue systems implemented

3. **Major technology decisions**
   - Database changes (SQL → NoSQL)
   - Framework changes
   - Infrastructure shifts

4. **Cross-cutting concerns**
   - Authentication/authorization patterns
   - Logging and monitoring strategies
   - Error handling approaches

**❌ DON'T document when:**

1. **Feature-specific architecture**
   - Belongs in feature docs instead
   - Single-component changes
   - Isolated modifications

2. **Internal module changes**
   - Refactoring within a service
   - Local optimizations
   - Component reorganization

### Architecture Documentation Template Location

**Template**: built-in architecture structure from the docs-git-committer agent (or a project template if one exists)

**Output location**: `docs/architecture/[topic].md`

### Architecture Documentation Contents

Architecture docs should include:

1. **System Overview**: High-level architecture diagram
2. **Components**: Key services and their responsibilities
3. **Communication**: How components interact
4. **Data Flow**: How data moves through the system
5. **Technology Stack**: Key technologies and why chosen
6. **Scalability**: How the system scales
7. **Trade-offs**: Decisions made and alternatives considered

---

## API Documentation (docs/api/)

### When to Create/Update API Docs

**✅ DO document when:**

1. **New public endpoints**
   - REST endpoints
   - GraphQL queries/mutations
   - WebSocket events

2. **Modified endpoint behavior**
   - Request format changes
   - Response format changes
   - Authentication requirements changed

3. **Breaking changes**
   - Deprecated endpoints
   - Removed fields
   - Changed validation rules

**❌ DON'T document when:**

1. **Internal APIs**
   - Not exposed to external consumers
   - Private function signatures
   - Internal helper methods

2. **No behavior change**
   - Refactoring with same interface
   - Performance improvements
   - Internal implementation changes

### API Documentation Template Location

**Template**: built-in API structure from the docs-git-committer agent (or a project template if one exists)

**Output location**: `docs/api/[endpoint-or-group].md`

### API Documentation Contents

API docs should include:

1. **Endpoint**: Method and path
2. **Description**: What it does
3. **Authentication**: Required auth/permissions
4. **Request**: Parameters, body, headers
5. **Response**: Status codes, body format
6. **Examples**: Request/response examples
7. **Errors**: Possible error responses

---

## Setup/Installation Documentation (docs/setup.md)

### When to Update Setup Docs

**✅ DO update when:**

1. **New dependencies added**
   - Required libraries or services
   - Database requirements
   - External service dependencies

2. **Environment variables changed**
   - New configuration required
   - Changed variable names
   - New services to configure

3. **Installation steps changed**
   - New setup procedures
   - Modified deployment process
   - Additional configuration needed

**❌ DON'T update when:**

1. **Development dependencies only**
   - Dev-only package changes
   - Test library updates
   - Linting tool changes (unless affecting setup)

2. **No user-facing changes**
   - Internal refactoring
   - Performance improvements
   - Bug fixes

### Setup Documentation Template Location

**Template**: built-in setup structure from the docs-git-committer agent (or a project template if one exists)

**Output location**: `docs/setup.md` (or contribution section)

---

## Decision Framework

Use this framework to decide what to document:

### Question 1: Who needs to know?

- **End users** → Feature docs
- **API consumers** → API docs
- **New developers** → Architecture docs or setup docs
- **Current developers** → CLAUDE.md (rarely) or feature docs
- **Nobody** → Don't document

### Question 2: Is this substantial?

- **Significant change** (> 4 files, new features) → Document
- **Minor change** (< 3 files, bug fix) → Usually skip
- **Trivial change** (formatting, typos) → Never document

### Question 3: Will this stay relevant?

- **Long-term architecture** → Document
- **Feature implementation** → Document
- **Temporary workaround** → Add code comment, not docs
- **Experimental code** → Mark as experimental, minimal docs

### Question 4: Is it critical for safety/security?

- **Security-critical** → CLAUDE.md (if directory-specific)
- **Must not be forgotten** → CLAUDE.md or architecture docs
- **Nice to know** → Feature docs or skip
- **Obvious** → Skip

---

## Documentation Priority Matrix

| Change Type         | Feature Docs | CLAUDE.md | Architecture Docs     | API Docs          |
| ------------------- | ------------ | --------- | --------------------- | ----------------- |
| New feature         | ✅ High      | ❌ No     | ❌ No                 | 🟡 If API changes |
| API change          | 🟡 Maybe     | ❌ No     | ❌ No                 | ✅ High           |
| Refactoring         | ❌ No        | ❌ No     | ❌ No                 | ❌ No             |
| Bug fix             | ❌ No        | ❌ No     | ❌ No                 | ❌ No             |
| Architecture change | 🟡 Maybe     | ❌ Rarely | ✅ High               | ❌ No             |
| Security pattern    | ❌ No        | 🟡 Maybe  | ✅ High               | 🟡 If affects API |
| Breaking change     | ✅ High      | ❌ No     | 🟡 If system-wide     | ✅ High           |
| New dependency      | ❌ No        | ❌ No     | 🟡 If major           | ❌ No             |
| Performance         | ❌ No        | ❌ No     | 🟡 If strategy change | ❌ No             |

Legend:

- ✅ High priority - Should document
- 🟡 Maybe - Document if significant
- ❌ No - Usually skip

---

## Examples

### Example 1: New Authentication Feature

**Change**: Implemented JWT-based authentication

**Documentation Decision**:

- ✅ Feature docs: `docs/features/jwt-authentication.doc.md`
  - User flow for login/logout
  - How tokens work
  - Implementation files
- ✅ API docs: `docs/api/authentication.md`
  - Login endpoint
  - Token refresh endpoint
  - Protected routes usage
- ❌ CLAUDE.md: Not needed (standard pattern)
- ❌ Architecture docs: Not system-wide change

### Example 2: Internal Refactoring

**Change**: Extracted validation logic to separate module

**Documentation Decision**:

- ❌ Feature docs: No user-facing change
- ❌ CLAUDE.md: Obvious organizational change
- ❌ Architecture docs: Module-level only
- ❌ API docs: No API changes

**Result**: No documentation needed

### Example 3: Critical Security Pattern

**Change**: Added rate limiting to prevent brute force

**Documentation Decision**:

- ❌ Feature docs: Internal security measure
- 🟡 CLAUDE.md: Maybe in `auth/` directory if critical
  - "All auth endpoints MUST use rate limiting"
  - Link to detailed docs
- ✅ Architecture docs: `docs/architecture/security.md`
  - Rate limiting strategy
  - Configuration
  - Monitoring
- ❌ API docs: Transparent to API consumers

### Example 4: Bug Fix

**Change**: Fixed null pointer in user validation

**Documentation Decision**:

- ❌ Feature docs: Just a fix, no behavior change
- ❌ CLAUDE.md: Not a pattern
- ❌ Architecture docs: No architecture change
- ❌ API docs: No API change

**Result**: No documentation needed (commit message sufficient)

### Example 5: Breaking API Change

**Change**: Changed user endpoint response format

**Documentation Decision**:

- ✅ Feature docs: `docs/features/user-api-v2.doc.md`
  - What changed and why
  - Migration guide
  - New format
- ❌ CLAUDE.md: Not a pattern
- ❌ Architecture docs: Not system-wide
- ✅ API docs: `docs/api/users.md`
  - New endpoint format
  - Breaking change notice
  - Examples

---

## Summary Checklist

Before documenting, ask:

- [ ] Does this affect user-facing behavior? → Feature docs
- [ ] Does this change public APIs? → API docs
- [ ] Is this a system-wide architectural change? → Architecture docs
- [ ] Is this a critical directory-specific pattern? → CLAUDE.md (rarely)
- [ ] Is this just refactoring/bug fix? → Skip documentation

**When in doubt**: Skip documentation for minor changes, document substantial changes.

**Remember**: CLAUDE.md updates are RARELY needed. Most changes go in feature docs or are too minor to document at all.
