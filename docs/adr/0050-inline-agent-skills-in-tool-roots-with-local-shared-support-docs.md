# Inline tool SKILLs with tool-local shared support docs

Supersedes ADR-0048.

Tool-consumed agent workflows are now seeded as complete workflow content directly inside
`.claude/skills/<workflow>/SKILL.md` and `.codex/skills/<workflow>/SKILL.md`.
Shared support docs remain separate, but live beside those skills in tool-local shared
folders such as `.claude/skills/_shared/...` and `.codex/skills/_shared/...`.
The package no longer materialises `application-pipeline/agent-skills/` during `init`
or `init --refresh`.

## Decision

- Inline the full workflow body into each tool's package-owned `SKILL.md`.
- Keep shared references as package-owned files under each tool root's `_shared/`
  directory, with intra-tool links such as `../_shared/...`.
- Treat `application-pipeline/agent-skills/` as a retired legacy location, not an
  active bootstrap destination.

## Why

- In this project setup, indirection through `application-pipeline/agent-skills/`
  caused LLM confusion at runtime.
- Inlined workflow bodies remove the extra path hop and make the tool-visible file the
  direct source of instructions.
- Tool-local `_shared` keeps support docs close to the tool-owned skills that consume
  them and removes hidden coupling through another root.
- Existing `init` semantics remain intact: normal init seeds missing package artefacts;
  refresh rewrites known package-owned artefacts without destructive cleanup of unknown
  operator content.

## Bootstrap and refresh semantics

- `init` seeds package-owned skill files only where missing in `.claude/skills/`,
  `.codex/skills/`, and their tool-local `_shared/` folders.
- `init --refresh` deterministically overwrites package-owned inline `SKILL.md` files
  and package-owned tool-local `_shared` docs from package templates.
- Unknown or user-added files and directories under those tool roots are preserved.
- `application-pipeline/agent-skills/` is not created by default and is not refreshed.
- `application-pipeline/skills/` remains a separate retired legacy path.

## Migration notes

- Existing legacy `application-pipeline/agent-skills/` content is not auto-deleted.
- Operators may manually compare, archive, or delete that legacy directory after
  confirming the tool-root inline skills and tool-local `_shared` docs are in place.
- Legacy tool-local `_shared` cleanup remains manual for any older pre-ADR-0050
  layouts.

## Consequences

- `src/application_pipeline/templates/application-pipeline/agent-skills/...` becomes a
  retired bootstrap path rather than a materialised runtime destination.
- Tool skill templates under `src/application_pipeline/templates/claude/skills/...` and
  `src/application_pipeline/templates/codex/skills/...` now carry the complete workflow
  body.
- Tool-local `_shared` folders are first-class package-owned template destinations.
- ADR-0048 remains historical context only for the earlier shared-body indirection
  strategy.
