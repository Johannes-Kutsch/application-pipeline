# Triage Profile reused as v2 authoring context

v2 authoring workflow (`/analyse-listing`, `/write-cv`, `/iterate-cv`) reads the existing **Triage Profile** as sole applicant-data source. No separate **CV Profile**, no `/ingest-profile`. A sibling `writing-style.md` carries voice rules (CV-authoring only, not injected into v1 prompts). Three existing Triage Profile files reformatted from prose to bullets/keywords. Companions: ADR-0012 (log convention), ADR-0015 (daily file).

## Why

- Single source of applicant data — a parallel CV Profile doubles the surface and opens a drift seam.
- Every grilling session updates the Triage Profile, sharpening the next day's shortlist too.
- Bullets scale better through grilling than prose — atomic units support conservative deletion.

## Consequences

- `user-info/triage-profile/` contains three files (post ADR-0034 merger): `self-description.md`, `match-criteria.md`, `writing-style.md`. Bullets/keywords, German, "extremely concise."
- v1 Prompt Loader unchanged — `writing-style.md` NOT injected into v1 prompts.
- CV Profile term retired before being built.
