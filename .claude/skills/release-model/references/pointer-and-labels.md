# Pointer section and labels doc

Exact text `release-model` adds outside `RELEASING.md`. Both edits are idempotent:
the skill checks for the marker before writing and never rewrites an existing section.

## Pointer section

Added to every existing pointer target that `$TARGETS --repo "$ROOT"` lists
(`scripts/pointer-targets.sh`), never to a file created for this purpose. Marker: a line
matching `^## Branching & releases`. When absent, append at the end
of the file after one blank line. When `CLAUDE.md` imports `AGENTS.md` (a live
`@AGENTS.md` line, not inside a comment, code span, or fence), the pointer belongs in
`AGENTS.md` alone; `$TARGETS` leaves `CLAUDE.md` out. A dangling import exits 1 — repair it
first. Repos without the import keep the legacy behavior (every existing file).

`pointer-targets.sh` calls `_shared/scripts/rules-pointer.sh targets`, backed by
`rules_pointer.py`, the canonical parser/shim renderer also used by init. Python
3.11+ is required. A leading UTF-8 BOM and CRLF are normalized identically in
both consumers. Release-model uses only the read-only `targets` query; the
helper's `claude-md-shim` operation renders to stdout, never writes or migrates
files. Init owns explicit creation/migration, with its existing confirmation,
backup and rollback gates. Release-model never invokes migration or creates a
missing `AGENTS.md`/`CLAUDE.md`.

When reading a release template, normalize a leading UTF-8 BOM (`utf-8-sig`);
state-block/table and workflow signal readers do the same. State rewrites retain
an existing file's BOM and CRLF.

Trunk-only:

```markdown
## Branching & releases

[`RELEASING.md`](RELEASING.md) is the source of truth for branches and releases. Branch
off `<trunk>` and PR back into it; every release is tagged from `<trunk>`. Never merge one
long-lived branch into another to sync it.
```

Release-branches:

```markdown
## Branching & releases

[`RELEASING.md`](RELEASING.md) is the source of truth for branches and releases. Features
go into `<trunk>`; fixes for a shipped minor land on `<trunk>` first and are backported to
`release/X.Y` with the `backport:X.Y` label. Never merge one long-lived branch into another
to sync it.
```

For `.github/copilot-instructions.md`, use the same text. When the file sits under
`.github/`, write the link as `../RELEASING.md`.

`--upgrade` replaces a pointer section only when it matches the trunk-only text above
byte-for-byte (the skill wrote it); an edited section is left alone and listed as
"update by hand".

## Labels doc

Release-branches only, and only when `.github/labels.md` exists and does not contain
`backport:`. Two edits:

1. In the label-family table, add one row after the last existing row, padded to the
   table's column widths:

   ```markdown
   | `backport:X.Y` | backport | Cherry-pick to release/X.Y |
   ```

2. At the end of the file, append:

   ```markdown
   ## Backport labels

   One `backport:X.Y` label exists per active maintenance branch (see
   [`RELEASING.md`](../RELEASING.md)). Create it when `release/X.Y` is cut:

   gh label create "backport:X.Y" --color "5319e7" --description "Cherry-pick to release/X.Y"
   ```

   Put the `gh label create` line in a `bash` fenced block.

If the file has no table, do only step 2. Run `prettier --check` on the file afterwards
when the project has prettier; fix only the table padding if it complains.
