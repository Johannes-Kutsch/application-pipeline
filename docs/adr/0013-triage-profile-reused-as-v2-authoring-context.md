# Triage Profile reused as v2 authoring context

v2 authoring (`/analyse-listing`, `/write-cv`) reads **Triage Profile** as sole applicant-data source. No separate CV Profile. Voice/paragraph-pattern decisions live in `/write-cv` flow. Three files reformatted from prose to bullets/keywords.

## Why

- Single source of applicant data eliminates drift seam. Bullets scale better than prose for structured matching against listing requirements (ADR-0041).

## Consequences

- `user-info/triage-profile/` contains three files (ADR-0034 split): `gate-criteria.md`, `candidate-profile.md`, `skills.md`.
- CV Profile term retired before being built.
