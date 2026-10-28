# GitHub PR Comment Surfaces

## Contents

- 1. Review threads (primary input)
- 2. Top-level PR conversation comments (secondary input)
- 3. Review summary bodies (in-progress detection only)
- Why three queries, not one
- Suggestion blocks

GitHub exposes three places where reviewers leave feedback on a pull request. `pr-autofix` reads from the two that are actionable per-comment, and uses the third only for in-progress detection.

| Source                    | API surface                                   | File/line anchored? | Has thread state? | Resolvable? | Used by `pr-autofix`? |
| ------------------------- | --------------------------------------------- | ------------------- | ----------------- | ----------- | --------------------- |
| **Review threads**        | GraphQL `pullRequest.reviewThreads`           | Yes                 | Yes               | Yes         | Primary input         |
| **Top-level PR comments** | GraphQL `pullRequest.comments` (REST: issues) | No                  | No                | No          | Secondary input       |
| **Review summary bodies** | GraphQL `pullRequest.reviews[].body`          | No                  | No                | No          | In-progress scan only |

This file documents the queries `scripts/fetch-pr-comments.sh` issues and the mutations `scripts/resolve-thread.sh` issues, so the skill body can stay focused on workflow.

---

## 1. Review threads (primary input)

A review thread groups one or more review comments anchored to the same file and line. The first comment in the thread is the "root" issue; subsequent comments are replies/discussion. **`pr-autofix` treats one thread as one actionable unit, using the root comment as the issue text.**

### Query (paginated)

```graphql
query ($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 50, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id # GraphQL Node ID — for resolveReviewThread mutation
          isResolved
          isOutdated
          comments(first: 1) {
            # root comment only
            nodes {
              databaseId # REST comment id — for /replies endpoint
              id # GraphQL Node ID — for reactions
              body
              path # file path
              line # current line anchor
              startLine # multi-line range start
              originalLine # line at time of comment (pre-rebase)
              createdAt
              author {
                login
              }
            }
          }
        }
      }
    }
  }
}
```

### Filter rules (applied by the skill, not the script)

- `isResolved == false` unless `--include-resolved`.
- `isOutdated == false` unless `--include-outdated`.

Outdated threads point at line anchors that no longer exist after a rebase; their `path` and `line` may be misleading, so we skip them by default.

### Mutations

**Resolve** (after a successful fix or a skip-with-reason):

```graphql
mutation ($thread: ID!) {
  resolveReviewThread(input: { threadId: $thread }) {
    thread {
      id
      isResolved
    }
  }
}
```

The `$thread` argument is the GraphQL Node ID of the review thread (`thread.id`, not `comment.id`).

**Reply** (post into an existing thread):

```
POST /repos/{owner}/{repo}/pulls/{pull_number}/comments/{root_comment_id}/replies
{ "body": "..." }
```

The `root_comment_id` is the REST `databaseId` of the thread's root comment (the first comment in `thread.comments`).

**Note**: GitHub's GraphQL API also exposes `addPullRequestReviewThreadReply` for the same purpose. We use the REST endpoint for symmetry with `gh api` patterns elsewhere in the bundle and because the REST contract is more widely documented in tooling.

---

## 2. Top-level PR conversation comments (secondary input)

Comments posted on the PR's main conversation timeline, NOT anchored to any file or line. These appear as issue comments under the hood (PRs are GitHub issues with extra fields).

### Query (paginated)

```graphql
query ($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      comments(first: 50, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          databaseId # REST id — for reactions REST endpoint
          id # GraphQL Node ID — for addReaction mutation
          body
          createdAt
          author {
            login
          }
        }
      }
    }
  }
}
```

### Constraints

- **Cannot be resolved.** There is no thread to resolve; the GraphQL `resolveReviewThread` mutation does not apply.
- **Can be reacted to.** Add 👍 / 👎 / etc via `addReaction`.
- **Can be replied to.** A "reply" is just another top-level comment, posted via `POST /repos/{owner}/{repo}/issues/{N}/comments`. We `@mention` the original author in the reply body to thread it visually.

### Mutations

**Add reaction** (acknowledge on Fixed):

```graphql
mutation ($subject: ID!, $content: ReactionContent!) {
  addReaction(input: { subjectId: $subject, content: $content }) {
    reaction {
      content
    }
  }
}
```

`$subject` is the GraphQL Node ID of the comment. Valid `$content` values:
`THUMBS_UP THUMBS_DOWN LAUGH HOORAY CONFUSED HEART ROCKET EYES`.

**Reply on PR conversation** (post a new top-level comment):

```
POST /repos/{owner}/{repo}/issues/{pr_number}/comments
{ "body": "@<author> — ..." }
```

---

## 3. Review summary bodies (in-progress detection only)

When a reviewer submits a batch of comments via a "review" (Approve, Request changes, Comment), the review itself can carry a top-level body. Bots like CodeRabbit often use this body to announce status: "Reviewing your PR...", "Come back again in a few minutes".

We **do not** treat these as actionable comments. We only scan them (and top-level issue comments) for in-progress markers, mirroring `coderabbit:autofix` Step 4.

### Query

```bash
gh pr view "$PR_NUMBER" --json comments,reviews --jq '
  [
    (.comments[]? | .body // empty),
    (.reviews[]?  | .body // empty)
  ]
  | map(select(test("Come back again in a few minutes|review (in progress|is being prepared)"; "i")))
  | length
'
```

If the count is `> 0` (from a bot author), the skill exits cleanly with "review in progress; try again in a few minutes". This prevents acting on partial review state.

---

## Why three queries, not one

GitHub does NOT expose a single "every comment on this PR" query. The three surfaces have different parent objects (`reviewThreads`, `comments`, `reviews[].body`) and different mutation surfaces. Combining them client-side keeps each query small, pagination-friendly, and authoritative — and lets us assign the right closure action per surface.

---

## Suggestion blocks

GitHub supports inline suggestion blocks (` ```suggestion `) inside review comments. The REST endpoint `POST /repos/{owner}/{repo}/pulls/{N}/comments/{id}/suggestion/apply` (or the "Apply suggestion" button in the UI) commits the suggested change directly.

**`pr-autofix` does NOT use this endpoint.** Vendor suggestion blocks are part of the untrusted comment body; we always re-derive the fix locally via the `pr-comment-fixer` agent. This is a deliberate safety choice — the suggestion block can contain any text the reviewer wants committed, with no review by the fixer.
