# Inline tool SKILLs with tool-local shared support docs; single package source

Tool-consumed agent workflows seeded as complete content directly inside `.claude/skills/<workflow>/SKILL.md` and `.codex/skills/<workflow>/SKILL.md`. Shared support docs under tool-local `_shared/` dirs, linked via `../_shared/...`. `application-pipeline/agent-skills/` is a retired legacy location.

Package templates use one canonical source tree for workflow bodies and support docs. `init` materialises into both tool-local roots. Generated Claude and Codex files are byte-identical unless a future tool overlay is introduced.

## Why

- Indirection through `application-pipeline/agent-skills/` caused LLM confusion at runtime. Inlined bodies make the tool-visible file the direct source. Single package source removes copy-paste drift between template trees.

## Consequences

- `init` seeds missing package-owned skill and `_shared` files. `init --refresh` overwrites them. User-added dirs preserved. Legacy `application-pipeline/agent-skills/` not auto-deleted.
