# Single package source for Agent Skills

Agent Skill package templates use one canonical package-owned source tree for workflow bodies and support docs, then `init` materialises that source into both `.claude/skills/` and `.codex/skills/` as complete tool-local runtime files. This preserves ADR-0050's no-runtime-indirection rule while removing copy-paste drift between `templates/claude/skills/` and `templates/codex/skills/`; generated Claude and Codex files are byte-identical unless a future explicit tool overlay is introduced.
