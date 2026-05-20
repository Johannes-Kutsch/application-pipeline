# Triage Profile reused as v2 authoring context

The v2 application-authoring workflow (PRD #16 v2.1: `/analyse-listing`, `/write-cv`, `/iterate-cv`) reads the existing **Triage Profile** in `synched/user-info/` as its sole applicant-data source. No separate **CV Profile** is built, no `/ingest-profile` step exists, and there are no `profile_de.md` / `profile_en.md` files. A new sibling `synched/user-info/writing-style.md` carries voice rules and is updated by the same skills. The three existing **Triage Profile** files are reformatted from German prose to bullets/keywords so they stay LLM-readable as they grow through grilling.

Companions: ADR-0016 (hardcoded prompts + externalized user-info), ADR-0021 (Daily Results File).

## Why

- **Single source of applicant data.** A parallel **CV Profile** doubles the surface where applicant facts live and opens a drift seam between "what the classifier/judge believes about me" and "what the CV writer believes about me". Reusing the **Triage Profile** removes the parallel artifact and the ingest ceremony.
- **Continuous learning across both surfaces.** Every grilling session in `/analyse-listing` updates the **Triage Profile**. Because the same files feed v1's classifier and judge, every grilling pass also sharpens the next day's shortlist.
- **No parser depends on the prose shape.** ADR-0016 already establishes that Triage Profile files are concatenated freeform into `{USER_INFO}`. Bullets vs prose is an LLM-readability change, not an interface change.
- **Bullets scale better through grilling.** Prose entries silently merge under append-style updates; bullets are atomic units. Conservative-deletion (PRD #16 v2.1) requires per-entry granularity.
- **`writing-style.md` is a sibling, not a child.** Voice rules are not applicant facts; conflating them mixes two update cadences.

## Considered alternatives

- **Keep the v1-of-#16 shape: build a separate CV Profile via `/ingest-profile`.** Rejected: doubles applicant-fact surface; opens drift; front-loads ceremony before any value.
- **Append to existing prose Triage Profile.** Rejected: prose drifts into self-contradiction; conservative deletion is impossible.
- **Put writing-style rules inside `self-description.md`.** Rejected: different cadence, different audience.
- **Structured (JSON/YAML) applicant profile.** Rejected: consumers are LLMs, not parsers; markdown bullets are the right format.

## Consequences

- **`synched/user-info/` contains four files**: `self-description.md`, `domain-fit.md`, `match-criteria.md`, `writing-style.md`. Bullet/keyword format, "Be extremely concise. Sacrifice grammar for the sake of concision."
- **v1 Prompt Loader unchanged.** Continues to concatenate the relevant three files per call site per ADR-0016. `writing-style.md` is NOT concatenated into the v1 `{USER_INFO}` slot — it is read only by v2 authoring skills.
- **`/ingest-profile`, `profile_de.md`, `profile_en.md`, the `CV Profile` term — all retired before they were built.** No code, no glossary entry, no follow-up.
- **v2 skills write to the Triage Profile silently** (no per-turn diff approval). Conservative-deletion (explicit user contradiction only) and conservative-promotion (generalisable signals only) keep the surface from drifting.
- **`writing-style.md` updates are in-place rewrites**, never appends — latest guidance always wins.
- **Single language: German.** Aligned with ADR-0016. No `lang` parameter on any v2 skill.
