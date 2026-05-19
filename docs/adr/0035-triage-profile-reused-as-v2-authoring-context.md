# Triage Profile reused as v2 authoring context

The v2 application-authoring workflow (PRD #16 v2.1: `/analyse-listing`, `/write-cv`, `/iterate-cv`) reads the existing **Triage Profile** in `synched/user-info/` as its sole applicant-data source. No separate **CV Profile** is built, no `/ingest-profile` step exists, and there are no `profile_de.md` / `profile_en.md` files. A new sibling `synched/user-info/writing-style.md` carries voice rules and is updated by the same skills. The three existing **Triage Profile** files are reformatted from German prose to bullets/keywords so they stay LLM-readable as they grow through grilling.

Companion ADRs: ADR-0019 (hardcoded prompts with externalized user-info), ADR-0030 (Daily Results File).

## Why

- **Single source of applicant data.** The original PRD #16 v1 proposed a parallel **CV Profile** (`profile_de.md` / `profile_en.md`) built by `/ingest-profile` from arbitrary **Reference Material**. That doubles the surface area where applicant facts live, opens the door to drift between "what the classifier/judge believes about me" and "what the CV writer believes about me", and adds a ceremony step (`/ingest-profile`) before the user can write anything. Reusing the **Triage Profile** removes the parallel artifact and the ceremony.
- **Continuous learning across both surfaces.** Every grilling session in `/analyse-listing` updates the **Triage Profile**. Because the same files feed v1's **Relevance Classifier** and **Match Judge**, every grilling pass *also* sharpens the next day's shortlist. The two pipelines no longer learn separately.
- **No parser depends on the prose shape.** ADR-0019 establishes that the **Triage Profile** files are concatenated freeform into the `{USER_INFO}` slot of the hardcoded prompts. Switching from prose to bullets is an LLM-readability change, not an interface change. v1 classify/judge behaviour is preserved.
- **Bullets scale better through grilling.** Prose entries silently merge with each other under append-style updates; bullets are atomic units that can be added or removed without touching neighbours. Conservative-deletion semantics (PRD #16 v2.1 Q11 / Story 10) require the per-entry granularity bullets give.
- **`writing-style.md` is a sibling, not a child of the CV Profile.** Voice rules are not applicant facts; conflating them in one file mixes two different update cadences (voice rules churn per iteration; applicant facts churn per grilling). Separate files, same directory, same "Be extremely concise" style convention.

## Considered alternatives

- **Keep the original v1-of-#16 shape: build a separate CV Profile via `/ingest-profile`.** Rejected: doubles the applicant-fact surface, opens a drift seam between v1 LLM context and v2 authoring context, and front-loads a ceremony step before any value is delivered. The **Reference Material** the ingester would consume is the same Overleaf-exported CV + past letters that now feed `writing-style.md` directly; no synthesis step is needed.
- **Append to the existing prose Triage Profile rather than reformat to bullets.** Rejected: append-only prose drifts into self-contradiction as grilling accumulates entries, and conservative deletion (Story 10) is impossible to apply cleanly to a prose paragraph. Bullets give the per-entry granularity the deletion rule needs.
- **Put writing-style rules inside `self-description.md`.** Rejected: voice rules are not applicant facts; they change at a different cadence and apply to a different audience (the CV writer, not the **Relevance Classifier** or **Match Judge**). The v1 prompts don't need voice rules; injecting them would just inflate the `{USER_INFO}` payload.
- **Build a structured (JSON / YAML) applicant profile instead of bullet markdown.** Rejected: the consumers are LLMs, not parsers. Markdown with bullets is the right format for both human review and LLM prompting; structured data would add a serialization step for no consumer's benefit.

## Consequences

- **`synched/user-info/` contains four files**: `self-description.md`, `domain-fit.md`, `match-criteria.md`, `writing-style.md`. All four are bullet/keyword format in "Be extremely concise. Sacrifice grammar for the sake of concision." style.
- **The v1 **Prompt Loader** is unchanged.** It continues to concatenate the relevant files for each call site per ADR-0019. `writing-style.md` is NOT concatenated into the v1 `{USER_INFO}` slot — it is read only by the v2 authoring skills.
- **The CONTEXT.md path lag (`data/user-info/` vs actual `synched/user-info/`) is corrected** in the same docs slice as this ADR.
- **`/ingest-profile`, `profile_de.md`, `profile_en.md`, and the `CV Profile` term are retired before they were ever built.** No code, no glossary entry, no follow-up. The PRD #16 v1 vocabulary is dead.
- **The v2 authoring skills (`/analyse-listing`, `/write-cv`, `/iterate-cv`) write to the **Triage Profile** silently** (no per-turn diff approval, per PRD #16 v2.1 Q10 / Story 9). Conservative-deletion (explicit user contradiction only) and conservative-promotion (generalisable signals only) keep the surface from drifting.
- **`writing-style.md` updates are in-place rewrites, never appends** (PRD #16 v2.1 Story 23). The latest guidance always wins; the file stays scannable.
- **Single language: German.** Aligned with ADR-0019. No `lang` parameter on any v2 skill.
