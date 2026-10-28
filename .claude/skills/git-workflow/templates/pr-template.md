# Pull Request Template and Guidelines

This document provides comprehensive guidance for creating effective pull requests with clear descriptions, testing instructions, and proper formatting.

---

## PR Title Guidelines

### Format

PR titles should follow the same format as commit messages:

```
<type>(<scope>): <description>
```

### Title Strategies by Commit Count

**Single Commit PR**:

- Use the commit subject line as PR title
- Example: `feat(auth): implement JWT authentication`

**Multiple Related Commits** (same feature):

- Create comprehensive title covering all changes
- Example: `feat(payment): add Stripe integration with webhook support`

**Multiple Unrelated Commits** (should be rare):

- Use the primary change as title
- List other changes in description
- Example: `feat(api): add user endpoints` (+ docs updates, config changes)

### Title Best Practices

- **Be specific**: "Add feature" → "Add user authentication with JWT"
- **Use imperative mood**: "Add" not "Added" or "Adds"
- **Include scope**: Helps reviewers understand impact area
- **Keep concise**: Ideally ≤60 characters for GitHub display
- **Match commit convention**: Same types as conventional commits

**Good titles**:

```
feat(auth): implement OAuth2 login flow
fix(api): resolve race condition in user creation
docs: update API documentation with examples
refactor(database): migrate to new query builder
```

**Bad titles**:

```
Update stuff                    ❌ (vague)
Added new authentication        ❌ (past tense, no scope)
Fix bugs                        ❌ (not specific)
Implement user auth + fix API   ❌ (multiple unrelated changes)
```

---

## PR Description Structure

### Core Sections

Every PR description should include these sections:

```markdown
## Summary

[2-4 sentence overview of what this PR does and why]

## Changes

- [Key change 1 with context]
- [Key change 2 with context]
- [Key change 3 with context]

## Testing

[Testing instructions - see detailed section below]

## Related Issues

[Issue references - see detailed section below]

## Breaking Changes

[Breaking changes if any - see detailed section below]

## Checklist

- [ ] [Quality checklist items]
```

### 1. Summary Section

**Purpose**: Provide high-level context in 2-4 sentences

**Include**:

- What this PR does
- Why it's needed
- High-level approach taken
- Key architectural decisions

**Example**:

```markdown
## Summary

This PR implements JWT-based authentication to replace the existing session-based
auth system. The new approach enables stateless API authentication and supports
mobile app integration. The implementation includes token generation, validation
middleware, and a refresh mechanism for long-lived sessions.
```

**Guidelines**:

- Start with "This PR..."
- Explain the "why" not just the "what"
- Mention any important trade-offs
- Keep it information-dense, not fluffy

### 2. Changes Section

**Purpose**: List key changes with enough context for reviewers

**Format**:

```markdown
## Changes

- **[Area/File]**: [What changed and why]
- **[Area/File]**: [What changed and why]
```

**Example**:

```markdown
## Changes

- **Authentication Service**: Added JWT generation and validation using RS256 algorithm
- **Auth Middleware**: Created middleware for protected routes with token verification
- **User Model**: Added refresh token storage and rotation logic
- **Login Endpoint**: Updated to return JWT tokens instead of creating sessions
- **Tests**: Added comprehensive unit and integration tests for auth flow
```

**Guidelines**:

- Group related changes together
- Explain impact of each change
- Mention deleted code if significant
- Highlight any refactoring

### 3. Documentation Section (If Applicable)

**Purpose**: Link to new or updated documentation

**Format**:

```markdown
## Documentation

### Created

- Feature docs: docs/features/jwt-authentication.doc.md
- API docs: docs/api/authentication.md

### Updated

- Setup guide: docs/setup.md (added JWT config instructions)
- Architecture docs: docs/architecture/security.md (JWT strategy)

[If no docs]

### No Documentation Changes

This change is internal refactoring with no user-facing impact.
```

**Include documentation for**:

- New features
- API changes
- Configuration requirements
- Breaking changes
- Migration steps

### 4. Testing Section

**Purpose**: Provide clear instructions for testing the changes

**Format**:

```markdown
## Testing

### Prerequisites

[Any setup needed before testing]

### Feature 1: [Name]

**How to test:**

1. Step one with specific details
2. Step two with expected results
3. Step three with verification

**Expected results:**

- [Specific outcome 1]
- [Specific outcome 2]

**Edge cases to verify:**

- [Edge case 1]: [How to test] → [Expected behavior]
- [Edge case 2]: [How to test] → [Expected behavior]

### Feature 2: [Name]

[Repeat structure above]
```

**Example**:

```markdown
## Testing

### Prerequisites

- Start the server: `npm run dev`
- Ensure database is migrated: `npm run db:migrate`

### JWT Authentication

**How to test:**

1. POST to `/api/auth/login` with valid credentials
2. Verify response includes `accessToken` and `refreshToken`
3. Use `accessToken` in `Authorization: Bearer <token>` header
4. Access protected route `/api/users/me`
5. Verify user profile is returned

**Expected results:**

- Login returns 200 with tokens
- Protected route returns 200 with user data
- Invalid token returns 401 Unauthorized

**Edge cases to verify:**

- Expired token: Returns 401 with "Token expired" message
- Invalid signature: Returns 401 with "Invalid token" message
- Missing token: Returns 401 with "No token provided" message
- Refresh token rotation: Old refresh token becomes invalid after use
```

**Guidelines**:

- Be specific with steps (exact commands, endpoints, data)
- Include expected results for each step
- Test both happy path and edge cases
- Mention any test data or fixtures needed
- Note any environment-specific considerations

### 5. Related Issues Section

**Purpose**: Link PR to issue tracker

**Format**:

```markdown
## Related Issues

Closes #123
Fixes #456, #457
Resolves #789
Related to #234, #567
```

**Keywords**:

- **Closes/Fixes/Resolves**: Automatically closes issues when PR merges
- **Related to**: References issues without closing them

**Guidelines**:

- Use "Closes" for feature implementations
- Use "Fixes" for bug fixes
- Use "Related to" for partial implementations
- Link to any design documents or RFCs

### 6. Breaking Changes Section

**Purpose**: Clearly document any breaking changes

**Format**:

````markdown
## Breaking Changes

⚠️ **BREAKING CHANGE**: [Description of what breaks]

**What changed:**

- [Specific change 1]
- [Specific change 2]

**Migration steps:**

1. [Step to update code]
2. [Step to update config]
3. [Step to test migration]

**Who is affected:**

- [Group of users 1]
- [Group of users 2]

**Example migration:**

```diff
- Old code here
+ New code here
```
````

````

**If no breaking changes**:

```markdown
## Breaking Changes

- No breaking changes in this PR
````

### 7. Checklist Section

**Purpose**: Ensure quality standards are met

**Standard checklist**:

```markdown
## Checklist

- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Comments added for complex logic
- [ ] Documentation updated (if needed)
- [ ] Tests added/updated
- [ ] All tests passing locally
- [ ] No new linter warnings
- [ ] Breaking changes documented
- [ ] Related issues linked
```

**Customize based on project**:

- Add project-specific requirements
- Include deployment considerations
- Note any manual testing needed
- Mention database migrations if applicable

---

## PR Types and When to Use Them

### Regular PR (Ready for Review)

**Use when**:

- Work is complete and tested
- All tests are passing
- Documentation is complete
- Ready for immediate merge after approval

**How to create**: Default `gh pr create` (no `--draft` flag)

### Draft PR

**Use when**:

- Work in progress
- Seeking early feedback on approach
- Tests not yet passing
- Documentation incomplete
- Need discussion before proceeding
- Breaking changes need team agreement

**How to create**: `gh pr create --draft`

**Mark ready later**: `gh pr ready [PR-number]`

---

## PR Size Guidelines

### Small PR (Preferred)

**Characteristics**:

- ≤ 300 lines changed
- Single focused change
- Easy to review in 15-30 minutes
- Clear scope and impact

**Benefits**:

- Faster reviews
- Fewer conflicts
- Easier to revert if needed
- Reduces review fatigue

### Medium PR (Acceptable)

**Characteristics**:

- 300-800 lines changed
- Related changes grouped together
- May take 30-60 minutes to review
- Well-organized with clear sections

**Guidelines**:

- Break into commits by logical area
- Provide detailed description
- Consider splitting if possible

### Large PR (Avoid When Possible)

**Characteristics**:

- > 800 lines changed
- Multiple features or significant refactoring
- Takes > 1 hour to review thoroughly

**Guidelines**:

- Only when absolutely necessary
- Break into smaller PRs if possible
- Provide extensive documentation
- Consider RFC or design doc first
- Schedule synchronous review session

---

## PR Description Examples

### Example 1: New Feature

```markdown
## Summary

This PR adds a user favorites feature that allows users to save and manage
their favorite products. The implementation includes backend API endpoints,
frontend UI components, and comprehensive test coverage. The feature uses a
many-to-many relationship between users and products stored in a new
`user_favorites` table.

## Changes

- **Database**: Added `user_favorites` table with user_id and product_id
- **API**: Created CRUD endpoints for managing favorites
  - GET `/api/users/:id/favorites` - List user's favorites
  - POST `/api/users/:id/favorites` - Add favorite
  - DELETE `/api/users/:id/favorites/:productId` - Remove favorite
- **Frontend**: Built favorites list and toggle button components
- **State Management**: Added favorites slice to Redux store
- **Tests**: Unit tests for API, integration tests for UI

## Documentation

### Created

- Feature docs: docs/features/user-favorites.doc.md
- API docs: docs/api/favorites.md

### Updated

- Database schema: docs/architecture/database.md

## Testing

### Prerequisites

- Ensure database is migrated: `npm run db:migrate`
- Create test user: `npm run seed:users`

### Add Favorite

**How to test:**

1. Log in as test user
2. Navigate to any product page
3. Click the heart icon to add to favorites
4. Verify heart icon fills in
5. Navigate to `/favorites`
6. Verify product appears in favorites list

**Expected results:**

- Heart icon shows filled state after click
- Product appears immediately in favorites list
- Favorites persist across page reloads

**Edge cases:**

- Maximum favorites: Limit is 100, verify error message at limit
- Duplicate favorites: Adding same product twice should not duplicate
- Deleted products: Favorites of deleted products are auto-removed

### Remove Favorite

**How to test:**

1. From favorites list, click remove button
2. Verify product is removed from list
3. Navigate back to product page
4. Verify heart icon is no longer filled

**Expected results:**

- Product removed immediately from list
- Heart icon returns to unfilled state
- Change persists across page reloads

## Related Issues

Closes #234

## Breaking Changes

- No breaking changes in this PR

## Checklist

- [x] Code follows project style guidelines
- [x] Self-review completed
- [x] Comments added for complex logic
- [x] Documentation updated
- [x] Tests added (unit + integration)
- [x] All tests passing locally
- [x] No new linter warnings
- [x] Database migration included
- [x] Related issues linked
```

### Example 2: Bug Fix

```markdown
## Summary

This PR fixes a critical race condition in the order processing system that
caused duplicate orders when users clicked submit multiple times. The fix
implements idempotency using a unique order token and database constraint.

## Changes

- **Order Controller**: Added idempotency token validation
- **Database**: Added unique constraint on `order_token` column
- **Frontend**: Disabled submit button after first click
- **Tests**: Added concurrent request tests to verify fix

## Testing

### Bug Reproduction (Before Fix)

**How to reproduce:**

1. Add items to cart
2. Go to checkout
3. Rapidly click "Place Order" button 5+ times
4. Check database: Multiple orders created with same items

### Verification (After Fix)

**How to test:**

1. Add items to cart
2. Go to checkout
3. Rapidly click "Place Order" button
4. Verify only one order is created
5. Check database: Single order entry
6. Verify user sees single confirmation

**Expected results:**

- Only one order created regardless of clicks
- Duplicate requests return 409 Conflict
- User sees single order confirmation
- Submit button becomes disabled after first click

## Related Issues

Fixes #567

## Breaking Changes

- No breaking changes in this PR

## Checklist

- [x] Code follows project style guidelines
- [x] Self-review completed
- [x] Bug reproduction steps documented
- [x] Fix verified with concurrent tests
- [x] Database migration included
- [x] All tests passing locally
```

### Example 3: Refactoring

````markdown
## Summary

This PR refactors the authentication middleware to improve testability and
reduce duplication. The changes extract token validation logic into separate
functions and add comprehensive unit tests. No functional changes - all
existing tests continue to pass.

## Changes

- **Auth Middleware**: Extracted token validation to separate module
- **Token Utils**: Created new utility module for JWT operations
- **Tests**: Added unit tests for token validation logic (95% coverage)
- **Documentation**: Updated code comments and examples

## Testing

### Verification

**Automated tests:**

```bash
npm test -- auth.middleware.test.ts
npm test -- token.utils.test.ts
```
````

All existing integration tests pass:

```bash
npm test -- auth.integration.test.ts
```

**Manual verification:**

1. Start server: `npm run dev`
2. Test protected endpoints with valid token → Success
3. Test protected endpoints with invalid token → 401 Unauthorized
4. Test protected endpoints without token → 401 Unauthorized

**Expected results:**

- All existing auth flows work identically
- No changes to API behavior
- Improved test coverage (75% → 95%)

## Related Issues

Related to #345 (improved test coverage initiative)

## Breaking Changes

- No breaking changes (internal refactoring only)

## Checklist

- [x] Code follows project style guidelines
- [x] Self-review completed
- [x] No functional changes (refactoring only)
- [x] Comprehensive tests added
- [x] All existing tests passing
- [x] Test coverage improved
- [x] Code comments updated

```

---

## Quality Checklist

Before creating a PR, ensure:

### Code Quality

- [ ] Follows project style guide and conventions
- [ ] No unnecessary console.logs or commented code
- [ ] Complex logic has explanatory comments
- [ ] Functions are appropriately sized
- [ ] No TODO comments without issue links
- [ ] Error handling is comprehensive

### Testing

- [ ] New code has test coverage
- [ ] All tests pass locally
- [ ] Edge cases are tested
- [ ] Integration tests added for new features
- [ ] No flaky tests introduced

### Documentation

- [ ] Feature documentation created (if new feature)
- [ ] API documentation updated (if API changes)
- [ ] Code comments added for complex logic
- [ ] README updated (if setup changes)
- [ ] Migration guide provided (if breaking changes)

### PR Description

- [ ] Clear, descriptive title
- [ ] Comprehensive summary
- [ ] Testing instructions included
- [ ] Edge cases documented
- [ ] Issues linked
- [ ] Breaking changes noted

---

## Common Mistakes to Avoid

### 1. Vague Descriptions

❌ **Bad**: "Updated the API"

✅ **Good**: "Added rate limiting to user login endpoint to prevent brute force attacks"

### 2. Missing Testing Instructions

❌ **Bad**: "Tested locally, works fine"

✅ **Good**: Step-by-step testing instructions with expected results

### 3. Too Large

❌ **Bad**: 2000 lines changed across 50 files

✅ **Good**: Break into smaller, focused PRs

### 4. Missing Context

❌ **Bad**: Lists file changes without explaining why

✅ **Good**: Explains what changed and why for each area

### 5. No Edge Cases

❌ **Bad**: Only tests happy path

✅ **Good**: Documents and tests error cases and edge conditions

---

## Summary

**Great PRs have**:

- Clear, specific titles following conventional format
- Comprehensive descriptions with context
- Detailed testing instructions with edge cases
- Proper issue references
- Complete quality checklist
- Appropriate size (prefer smaller)

**Remember**: PRs are documentation of your changes. Future developers (including you) will read them to understand decisions and implementation details. Invest time in writing clear, thorough PR descriptions.
```
