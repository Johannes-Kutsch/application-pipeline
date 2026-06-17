# Inline tool skill bodies with tool-local shared helpers

Tool-consumed agent workflows are now seeded as complete workflow content directly inside
`.claude/skills/<workflow>/SKILL.md` and `.codex/skills/<workflow>/SKILL.md`.
Shared helper docs stay separate, but move to tool-local shared folders:
`.claude/skills/_shared/...` and `.codex/skills/_shared/...`.
Wrappers still carry canonical frontmatter metadata (name/description) but no longer
reference `application-pipeline/agent-skills`.

On `init --refresh`, tool-owned skill files and tool-local shared helper files are
deterministically overwritten from package templates; unknown, user-added files are
preserved.

`application-pipeline/agent-skills/` is no longer materialized by init.
Existing legacy `application-pipeline/agent-skills` content is not auto-deleted;
migration cleanup remains operator-owned.

## Why

- In this project setup, indirection through `application-pipeline/agent-skills` caused LLM confusion.
- Inlined workflow bodies remove runtime dependency on another path.
- Tool-local `_shared` keeps support references near tool-owned skills and avoids hidden repository-level coupling.
- Existing normal+refresh init semantics stay stable: first-run writes missing package files; refresh overwrites owned files.
- Workflow text remains unchanged in behavior and intent; only path and link mechanics move.

## Consequences

- `src/application_pipeline/templates/application-pipeline/agent-skills/...` becomes a retired bootstrap path.
- Tool skill templates under `src/application_pipeline/templates/claude/skills/...` and
  `src/application_pipeline/templates/codex/skills/...` include full workflow text.
- New seeded trees include `.claude/skills/_shared/...` and `.codex/skills/_shared/...`.
- `init --refresh` no longer deletes `application-pipeline/agent-skills` automatically; cleanup is explicit/manual.
- ADR-0048 is superseded for the indirection strategy described there and remains valid only for historical context.
- `application-pipeline/skills/` remains retired legacy path.
