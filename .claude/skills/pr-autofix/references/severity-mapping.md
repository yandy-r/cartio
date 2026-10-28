# Severity Mapping & Bot Detection

## Contents

- Severity levels
- Inference rules (apply in priority order; first match wins)
- Bot detection
- In-progress markers
- Why MEDIUM is the default
- Examples

`pr-autofix` does not require reviewers to declare severity in a structured way. Instead, it infers severity from comment-body signals using the heuristics below. When no signal is detected, severity defaults to **MEDIUM** so the comment is still surfaced by the default `--severity LOW` threshold but is sorted below clearly-tagged HIGH/CRITICAL findings.

This file documents the patterns. `scripts/fetch-pr-comments.sh` does NOT do severity inference — the skill body applies these patterns in Phase 2c after fetching.

---

## Severity levels

Internal scale, ordered most-to-least severe:

```
CRITICAL > HIGH > MEDIUM > LOW
```

This matches `/code-review` and `/review-fix` so the same `--severity` flag values work across the three skills.

---

## Inference rules (apply in priority order; first match wins)

### Rule 1 — Emoji prefixes

| Pattern (case-insensitive) | Severity |
| -------------------------- | -------- |
| `🔴` `🚨` `❌`             | CRITICAL |
| `🟠` `⚠️` `🔒` (security)  | HIGH     |
| `🟡`                       | MEDIUM   |
| `🟢` `💡` `ℹ️`             | LOW      |

These match vendor conventions (CodeRabbit, SonarCloud, Codacy, DeepSource).

### Rule 2 — Header tags

CodeRabbit-style headers like `_Issue_ | _Critical_`, `_Bug_ | _High_`, `_Nitpick_ | _Minor_`.

Regex: `_([^_]+)_\s*\|\s*_([^_]+)_` — the second capture group is the severity word. Map per Rule 4.

### Rule 3 — Explicit severity keywords (whole-word, case-insensitive)

| Keyword                                                | Severity |
| ------------------------------------------------------ | -------- |
| `critical`, `severe`, `blocker`, `vulnerability`       | CRITICAL |
| `high`, `major`, `important`, `security`               | HIGH     |
| `medium`, `moderate`, `warning`                        | MEDIUM   |
| `low`, `minor`, `info`, `suggestion`, `nit`, `nitpick` | LOW      |

The keyword must appear as a whole word (use `\b...\b` boundaries) and within the first 200 characters of the body — otherwise the match is probably incidental ("this is critical to understanding…").

### Rule 4 — Vendor severity words

Some vendors emit their own scale. Map their words to ours:

| Vendor     | Vendor word  | Internal severity |
| ---------- | ------------ | ----------------- |
| CodeRabbit | `Critical`   | CRITICAL          |
| CodeRabbit | `High`       | HIGH              |
| CodeRabbit | `Medium`     | MEDIUM            |
| CodeRabbit | `Minor`      | LOW               |
| CodeRabbit | `Nitpick`    | LOW               |
| SonarCloud | `Blocker`    | CRITICAL          |
| SonarCloud | `Critical`   | CRITICAL          |
| SonarCloud | `Major`      | HIGH              |
| SonarCloud | `Minor`      | MEDIUM            |
| SonarCloud | `Info`       | LOW               |
| Codacy     | `Error`      | HIGH              |
| Codacy     | `Warning`    | MEDIUM            |
| Codacy     | `Info`       | LOW               |
| DeepSource | `Critical`   | CRITICAL          |
| DeepSource | `Major`      | HIGH              |
| DeepSource | `Minor`      | MEDIUM            |
| DeepSource | `Suggestion` | LOW               |

### Default

If none of the above matched, severity = **MEDIUM**.

---

## Bot detection

`scripts/fetch-pr-comments.sh` sets `is_bot: true` if EITHER condition holds:

1. The login ends with `[bot]` (the standard GitHub App suffix).
2. The login matches a known bot pattern lacking the `[bot]` suffix (rare; some legacy apps).

Currently tracked bots-without-`[bot]`:

- `coderabbitai`
- `sonarcloud`
- `codacy-production`
- `deepsource-autofix`
- `sonarqubecloud`

When the user passes `--bot-only`, the skill keeps only records with `is_bot == true`. When `--human-only`, the inverse. The list above can grow over time — the cost of a missing entry is that the bot's comments will be treated as human-authored under `--bot-only`, not that they're silently dropped.

---

## In-progress markers

Some bots post status messages while their review is still being prepared. Acting on a partial review leads to spurious skips and misclassified severities. Phase 2d of the skill scans **bot-authored** comment bodies (across all sources) for any of these patterns; if any match, the skill exits cleanly with "review in progress".

| Pattern (case-insensitive)         | Vendor     |
| ---------------------------------- | ---------- |
| `come back again in a few minutes` | CodeRabbit |
| `review is being prepared`         | generic    |
| `review in progress`               | generic    |
| `analyzing your changes`           | generic    |
| `i'm reviewing this PR`            | generic    |

The skill only short-circuits on bot authors — a human saying "I'm reviewing this PR" in conversation should NOT cause an exit.

---

## Why MEDIUM is the default

If a comment has no severity signal, two outcomes are possible:

1. **Treat as LOW** — comments with no signal are likely informational and should fall below the default fix threshold.
2. **Treat as MEDIUM** — comments without a signal are still actionable; the user can filter them out with `--severity HIGH` if desired.

We choose option 2 (MEDIUM) because a fast-and-loose human reviewer who writes "this is wrong" without an emoji should not be silently ignored. The user explicitly controls the threshold via `--severity`; the default (`LOW`) keeps everything in the plan, and they can drop to `--severity HIGH` to focus on tagged issues only.

---

## Examples

| Comment body (first ~100 chars)                                | Inferred severity |
| -------------------------------------------------------------- | ----------------- |
| `🔴 Critical: SQL injection at line 42`                        | CRITICAL          |
| `_Issue_ \| _High_ — Missing null check`                       | HIGH              |
| `**Major:** This loop is O(n²) and runs on every request`      | HIGH              |
| `nit: extract this to a constant`                              | LOW               |
| `🟢 Suggestion: consider using a named tuple here`             | LOW               |
| `We probably want to handle the empty array case here.`        | MEDIUM (default)  |
| `🚨 Security: secrets logged in plaintext (severity: blocker)` | CRITICAL          |
