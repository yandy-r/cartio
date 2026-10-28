# Conventional Commits Reference

This document provides comprehensive guidance on writing conventional commit messages following the Conventional Commits specification.

## Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

All sections except `<type>` and `<subject>` are optional.

---

## Commit Types

### feat - New Feature

A new feature for the user or a notable new ability.

**Examples:**

```
feat(auth): add JWT authentication support
feat(api): implement user profile endpoints
feat: add dark mode toggle to settings
```

**When to use:**

- Adding new user-facing functionality
- Implementing new APIs or endpoints
- Creating new components or modules

### fix - Bug Fix

A bug fix that resolves incorrect behavior.

**Examples:**

```
fix(auth): prevent null pointer in token validation
fix(ui): correct button alignment on mobile
fix: resolve memory leak in event listeners
```

**When to use:**

- Fixing bugs or errors
- Correcting incorrect behavior
- Resolving crashes or exceptions

### docs - Documentation

Documentation-only changes (no code changes).

**Examples:**

```
docs(api): update authentication endpoint examples
docs: add installation instructions to README
docs(contributing): clarify PR review process
```

**When to use:**

- Updating README, guides, or tutorials
- Adding code comments or JSDoc
- Improving inline documentation

### style - Code Style

Changes that don't affect code meaning (formatting, whitespace, etc.).

**Examples:**

```
style: format code with prettier
style(components): fix indentation in Button component
style: remove trailing whitespace
```

**When to use:**

- Formatting changes (prettier, eslint --fix)
- Whitespace adjustments
- Code organization without logic changes

### refactor - Code Refactoring

Code changes that neither fix bugs nor add features.

**Examples:**

```
refactor(auth): extract validation logic to separate module
refactor: simplify user service interface
refactor(database): move query builders to helpers
```

**When to use:**

- Restructuring code without changing behavior
- Improving code organization or readability
- Extracting functions or modules

### test - Tests

Adding or modifying tests.

**Examples:**

```
test(auth): add unit tests for JWT validation
test: increase coverage for user service
test(integration): add E2E tests for checkout flow
```

**When to use:**

- Adding new tests
- Updating existing tests
- Fixing broken tests

### chore - Maintenance

Other changes that don't modify src or test files.

**Examples:**

```
chore: update dependencies to latest versions
chore(deps): bump typescript from 4.9 to 5.0
chore: add .gitignore for IDE files
```

**When to use:**

- Dependency updates
- Build configuration changes
- Tooling updates
- Repository maintenance

### perf - Performance

Performance improvements without changing functionality.

**Examples:**

```
perf(api): add caching layer for user queries
perf: optimize image loading with lazy loading
perf(database): add indexes to frequently queried columns
```

**When to use:**

- Performance optimizations
- Reducing memory usage
- Improving execution speed

### ci - Continuous Integration

Changes to CI/CD configuration and scripts.

**Examples:**

```
ci: add GitHub Actions workflow for testing
ci(deploy): update production deployment script
ci: enable code coverage reporting
```

**When to use:**

- CI/CD pipeline changes
- GitHub Actions, Travis, CircleCI updates
- Deployment script modifications

### build - Build System

Changes to build system or external dependencies.

**Examples:**

```
build: update webpack configuration
build(npm): add postinstall script
build: configure rollup for library bundling
```

**When to use:**

- Build tool configuration (webpack, rollup, vite)
- Package.json script changes
- Build process modifications

### revert - Revert

Reverting a previous commit.

**Examples:**

```
revert: revert "feat(auth): add JWT authentication"

This reverts commit abc123def456.
```

**When to use:**

- Rolling back a previous commit
- Undoing changes that caused issues

---

## Scope

The scope is optional but recommended. It indicates the area of the codebase affected.

**Common scopes:**

- **Module/feature**: `auth`, `api`, `ui`, `database`
- **Component**: `Button`, `UserProfile`, `Header`
- **Layer**: `frontend`, `backend`, `middleware`
- **Package**: `server`, `client`, `shared`

**Examples:**

```
feat(auth): add password reset flow
fix(database): resolve connection pool leak
docs(api): update REST endpoint documentation
```

**When to omit scope:**

- Change affects entire project
- No clear single scope applies
- Change is very small or obvious

---

## Subject Line

The subject line is the brief description of the change.

**Rules:**

1. **Imperative mood**: Use "add" not "added" or "adds"
2. **No period**: Don't end with a period
3. **Lowercase**: Start with lowercase letter (after colon)
4. **≤50 characters**: Keep it concise
5. **Clear and specific**: Describe what changed

**Good examples:**

```
feat(auth): implement OAuth2 login flow
fix(api): prevent race condition in user creation
docs: add contributing guidelines
```

**Bad examples:**

```
feat(auth): Implemented OAuth2 login flow.  ❌ (capitalized, has period)
fix: fixed bug  ❌ (not specific enough)
docs: Updated the documentation for the API endpoints and added examples  ❌ (too long)
```

---

## Body

The body provides detailed explanation of the change. It's optional but recommended for non-trivial changes.

**Guidelines:**

- Wrap at 72 characters per line
- Explain **what** and **why**, not **how**
- Use bullet points for multiple items
- Include motivation for the change
- Describe alternatives considered

**Example:**

```
feat(auth): implement JWT-based authentication

Replace session-based authentication with JWT tokens to support
stateless API authentication and enable mobile app integration.

- Add JWT generation and validation utilities
- Create authentication middleware for protected routes
- Update user login endpoint to return tokens
- Add token refresh mechanism

This approach was chosen over OAuth2 to reduce external dependencies
and maintain simpler deployment architecture.
```

---

## Footer

The footer contains metadata about the commit.

### Breaking Changes

Breaking changes must be indicated in the footer with `BREAKING CHANGE:` followed by a description.

**Example:**

```
feat(api): change user endpoint response format

BREAKING CHANGE: User API now returns `userId` instead of `id`.
All clients must update to use the new field name.
```

**Alternative syntax** (with `!` in type/scope):

```
feat(api)!: change user endpoint response format

User API now returns `userId` instead of `id`.
```

### Issue References

Reference issues, pull requests, or tickets in the footer.

**Example:**

```
fix(auth): resolve login redirect loop

Fixes #123
Closes #456
Related to #789
```

**Common keywords:**

- `Fixes #123` - Closes the issue
- `Closes #123` - Closes the issue
- `Resolves #123` - Closes the issue
- `Related to #123` - References without closing

---

## Complete Examples

### Example 1: Simple Feature

```
feat(dashboard): add user activity widget
```

### Example 2: Bug Fix with Context

```
fix(auth): prevent token refresh race condition

Token refresh was being called multiple times simultaneously,
causing some requests to use expired tokens. Now properly
serializes refresh requests with a mutex.

Fixes #234
```

### Example 3: Breaking Change

```
feat(api)!: restructure user endpoints

BREAKING CHANGE: User endpoints have been reorganized:
- `/users/:id` is now `/api/v2/users/:id`
- Response format changed from snake_case to camelCase
- `created_at` field renamed to `createdAt`

Migration guide: docs/migrations/v2-api.md

Closes #456
```

### Example 4: Refactoring with Explanation

```
refactor(database): extract query builders to separate module

Move all query building logic from service layer to dedicated
query builder module for better separation of concerns and
improved testability.

- Create new `query-builders/` directory
- Move user, post, and comment queries
- Add comprehensive unit tests
- Update services to use new builders

No functional changes - purely organizational.
```

### Example 5: Performance Improvement

```
perf(api): add Redis caching for user lookups

Implement Redis caching layer for frequently accessed user data,
reducing database load by ~80% for read operations.

- Cache user profiles for 5 minutes
- Invalidate on user updates
- Add cache hit/miss metrics

Related to #789
```

### Example 6: Documentation Update

```
docs(api): add OpenAPI specification

Create comprehensive OpenAPI 3.0 specification for all REST
endpoints, including request/response schemas and examples.

- Add swagger.yaml with full API spec
- Document all authentication requirements
- Include example requests and responses
```

### Example 7: Dependency Update

```
chore(deps): upgrade react from 17.0 to 18.2

Update React and related packages to latest stable version.

- Update react and react-dom to 18.2
- Migrate from ReactDOM.render to createRoot
- Update type definitions
- Verify all components work with concurrent features

Tested across all browsers. No breaking changes detected.
```

---

## Validation Checklist

Before committing, verify your message meets these criteria:

- [ ] Type is one of: feat, fix, docs, style, refactor, test, chore, perf, ci, build, revert
- [ ] Scope is appropriate and lowercase (if present)
- [ ] Subject line uses imperative mood
- [ ] Subject line is lowercase (first letter after colon)
- [ ] Subject line has no period at the end
- [ ] Subject line is ≤50 characters
- [ ] Body is wrapped at 72 characters (if present)
- [ ] Body explains what and why (if present)
- [ ] Breaking changes use `BREAKING CHANGE:` or `!` (if applicable)
- [ ] Issue references use proper keywords (if applicable)

---

## Quick Reference

```
Type       When to Use                           Example
────────   ──────────────────────────────────   ─────────────────────────────────
feat       New feature                          feat(auth): add OAuth support
fix        Bug fix                              fix(api): handle null response
docs       Documentation only                   docs: update README
style      Formatting, no code change           style: fix indentation
refactor   Code restructuring                   refactor: extract helper function
test       Adding/updating tests                test: add user service tests
chore      Maintenance tasks                    chore: update dependencies
perf       Performance improvement              perf: add query caching
ci         CI/CD changes                        ci: add test workflow
build      Build system changes                 build: update webpack config
revert     Revert previous commit               revert: revert "feat: add feature"
```

---

## Tips

1. **Keep it atomic**: One logical change per commit
2. **Be specific**: "fix button" → "fix submit button alignment"
3. **Think of the reader**: Future you should understand the change
4. **Use present tense**: "add feature" not "added feature"
5. **Reference issues**: Link commits to issue tracker
6. **Explain why**: The code shows what, the message explains why
7. **Breaking changes**: Always document in footer
8. **Multiple changes**: Use body with bullet points

---

This reference should be consulted when crafting commit messages to ensure consistency and clarity across the project.
