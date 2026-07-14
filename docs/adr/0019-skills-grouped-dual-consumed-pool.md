# Skills as a grouped, dual-consumed pool

`skills.md` single source of truth, dual-consumed: pipeline harvests flat bullet bodies for judge prompt's `{SKILLS}` slot; `/write-cv` reads full structure (H2 **Skill Groups** + `{...}` attributes) for `skills_block`.

Groups carry `always` and per-jobtype `relevance`; items carry `always` only (within-group floor). Pipeline loader ignores headings + attributes — backward-compatible with flat-bullet parse.

## Why

- `skills_block` was the last surface where the LLM could invent items. Single file removes duplicate-list maintenance.

## Consequences

- `skills_block` slot: mechanically assembled by `/write-cv`, not free-authored.
- Pre-existing flat `skills.md` parses fine for pipeline, degenerate for `/write-cv`.
