# Classifier reduced to single gate check; triage profile split by consumer

Classifier: one check — domain scope + hard exclusions from `{GATE_CRITERIA}`. Candidate profile and ranking signals removed from classifier entirely — stretch/experience judgment deferred to **Match Judge**.

**Triage Profile** files by consumer:

- `gate-criteria.md` — domain-in/out + hard exclusions. Classifier only (`{GATE_CRITERIA}`).
- `candidate-profile.md` — who-the-candidate-is + ranking preferences. Judge only (`{CANDIDATE_PROFILE}`).
- `skills.md` — relocated from `search-terms/` to `triage-profile/`. Judge only (`{SKILLS}`).

## Why

- Three-check classifier collapsed "stretch" tier into reject — ~17 false negatives in 745 listings including explicitly in-domain positions. Root cause: information leakage. Constraining classifier input to gate-only criteria is more robust than teaching it to ignore visible information.

## Consequences

- `SearchTerms` drops `skills` field — two-field struct. Slot renames: `{SELF_DESCRIPTION}` → `{CANDIDATE_PROFILE}`, `{MATCH_CRITERIA}` → `{GATE_CRITERIA}`.
- Classifier receives `{GATE_CRITERIA}` only; judge receives `{CANDIDATE_PROFILE}` + `{SKILLS}`.
