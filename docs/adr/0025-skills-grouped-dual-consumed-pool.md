# Skills as a grouped, dual-consumed pool

`skills.md` is single source of truth, dual-consumed: pipeline harvests flat bullet bodies for judge prompt's `{SKILLS}` slot; `/write-cv` reads full structure (H2 **Skill Groups** + `{...}` attributes) to assemble the CV's `skills_block` slot.

Groups carry `always` and per-jobtype `relevance`; items carry `always` only (within-group floor, not group-promoting). Pipeline loader ignores headings + attributes — backward-compatible with ADR-0024's flat-bullet parse.

Primary motivation: factual consistency — bounding what Claude can claim about the applicant.

## Why

- `skills_block` was the last surface where the LLM could invent items. Relevance-weighted selection falls out of the same shape.
- Atomic skills with group composition express variation without authoring N whole-row variants.
- Single file removes duplicate-list maintenance between pipeline and CV.

## Consequences

- `skills_block` slot semantics shift from free-authored to mechanically assembled by `/write-cv`.
- Pre-existing flat `skills.md` (zero groups) parses fine for pipeline, degenerate for `/write-cv`.
