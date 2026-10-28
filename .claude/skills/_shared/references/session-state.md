# Session State Contract

## Contents

- Store
- Filename
- Discovery (resume)
- Interpretation Rules
- File Format

Single source of truth for the session-file store, filename, discovery rules, and Markdown format shared by `/save-session` (writer) and `/resume-session` (reader).

## Store

- Canonical store: `~/.claude/session-data/` — global (user home), not project-local.
- Legacy fallback (read-only): `~/.claude/sessions/`.

## Filename

`~/.claude/session-data/YYYY-MM-DD-<short-id>-session.tmp`

- Date: today's actual date (`date +%Y-%m-%d`).
- `<short-id>` characters: letters `a-z` / `A-Z`, digits `0-9`, hyphens `-`, underscores `_`.
- Recommended: lowercase letters + digits + hyphens, 8+ characters to avoid collisions (recommended, not mandatory).
- Valid examples: `abc123de`, `a1b2c3d4`, `frontend-worktree-1`, `ChezMoi_2`
- Avoid for new files: `A`, `test_id1`, `ABC123de`
- Full valid filename example: `2024-01-15-abc123de-session.tmp`
- Legacy filename `YYYY-MM-DD-session.tmp` is still valid and readable, but new session files should prefer the short-id form to avoid same-day collisions.
- Each session gets its own file — never append to a previous session's file.

## Discovery (resume)

- **No argument:** the most recently modified `*-session.tmp` file in `~/.claude/session-data/` only. If the folder does not exist or has no matching files, report that none were found and stop.
- **Date argument (`YYYY-MM-DD`):** search `~/.claude/session-data/` first, then the legacy `~/.claude/sessions/`, for `YYYY-MM-DD-session.tmp` (legacy format) or `YYYY-MM-DD-<shortid>-session.tmp` (current format). Load the most recently modified matching variant for that date, regardless of format. This is not a global newest-across-both-stores search.
- **File path argument:** read that file directly (e.g., forwarded from a teammate). The format is the same regardless of source.
- If not found, report clearly and stop.

## Interpretation Rules

- Every section is written honestly; do not skip sections — write "Nothing yet" or "N/A" if a section genuinely has no content. An incomplete file is worse than an honest empty section.
- "What WORKED" lists only confirmed-working items, each with evidence of WHY it is known to work (test passed, ran in browser, Postman returned 200, etc.). Without evidence, the item belongs in "What Has NOT Been Tried Yet".
- "What Did NOT Work (and why)" is the most important section. List every failed approach with the EXACT reason ("threw X error because Y" is useful; "didn't work" is not) so the next session does not retry it.
- "Exact Next Step": when not known, write the fallback text shown in the template; readers treat it as "no next step defined".
- "Environment & Setup Notes" is the only optional section: omit it entirely when there is nothing to record. All other sections are mandatory.
- If saving mid-session, save what is known so far and mark in-progress items clearly.

## File Format

```markdown
# Session: YYYY-MM-DD

**Started:** [approximate time if known]
**Last Updated:** [current time]
**Project:** [project name or path]
**Topic:** [one-line summary of what this session was about]

---

## What We Are Building

[1-3 paragraphs describing the feature, bug fix, or task. Include enough
context that someone with zero memory of this session can understand the goal.
Include: what it does, why it's needed, how it fits into the larger system.]

---

## What WORKED (with evidence)

[List only things that are confirmed working. For each item include WHY you
know it works — test passed, ran in browser, Postman returned 200, etc.
Without evidence, move it to "Not Tried Yet" instead.]

- **[thing that works]** — confirmed by: [specific evidence]
- **[thing that works]** — confirmed by: [specific evidence]

If nothing is confirmed working yet: "Nothing confirmed working yet — all approaches still in progress or untested."

---

## What Did NOT Work (and why)

[This is the most important section. List every approach tried that failed.
For each failure write the EXACT reason so the next session doesn't retry it.
Be specific: "threw X error because Y" is useful. "didn't work" is not.]

- **[approach tried]** — failed because: [exact reason / error message]
- **[approach tried]** — failed because: [exact reason / error message]

If nothing failed: "No failed approaches yet."

---

## What Has NOT Been Tried Yet

[Approaches that seem promising but haven't been attempted. Ideas from the
conversation. Alternative solutions worth exploring. Be specific enough that
the next session knows exactly what to try.]

- [approach / idea]
- [approach / idea]

If nothing is queued: "No specific untried approaches identified."

---

## Current State of Files

[Every file touched this session. Be precise about what state each file is in.]

| File              | Status         | Notes                      |
| ----------------- | -------------- | -------------------------- |
| `path/to/file.ts` | PASS: Complete | [what it does]             |
| `path/to/file.ts` | In Progress    | [what's done, what's left] |
| `path/to/file.ts` | FAIL: Broken   | [what's wrong]             |
| `path/to/file.ts` | Not Started    | [planned but not touched]  |

If no files were touched: "No files modified this session."

---

## Decisions Made

[Architecture choices, tradeoffs accepted, approaches chosen and why.
These prevent the next session from relitigating settled decisions.]

- **[decision]** — reason: [why this was chosen over alternatives]

If no significant decisions: "No major decisions made this session."

---

## Blockers & Open Questions

[Anything unresolved that the next session needs to address or investigate.
Questions that came up but weren't answered. External dependencies waiting on.]

- [blocker / open question]

If none: "No active blockers."

---

## Exact Next Step

[If known: The single most important thing to do when resuming. Be precise
enough that resuming requires zero thinking about where to start.]

[If not known: "Next step not determined — review 'What Has NOT Been Tried Yet'
and 'Blockers' sections to decide on direction before starting."]

---

## Environment & Setup Notes

[Only fill this if relevant — commands needed to run the project, env vars
required, services that need to be running, etc. Skip if standard setup.]

[If none: omit this section entirely.]
```
