# Classifier reduced to single gate check; triage profile split by consumer

Classifier drops from three checks (domain fit, skill/experience floor, preference fit) to one: domain scope + hard exclusions. Candidate profile and ranking signals removed from classifier input entirely — stretch/experience judgment deferred to the **Match Judge**.

**Triage Profile** files reorganised by consumer:

- `gate-criteria.md` — flat domain-in/out list + hard exclusions. Classifier only (`{GATE_CRITERIA}`).
- `candidate-profile.md` — who-the-candidate-is (former `self-description.md`) + ranking preferences (former match-strength / pull-factors / soft-modifiers from `match-criteria.md`). Judge only (`{CANDIDATE_PROFILE}`).
- `skills.md` — relocated from `search-terms/` to `triage-profile/`. Judge only (`{SKILLS}`). Attributes stripped inline by `prompts.py`.
- Cover-writing style and paragraph-pattern decisions move into the `/write-cv` flow; they are no longer injected as separate Triage Profile files.

## Why

The three-check classifier collapsed the "stretch / schwach" tier into reject. `match-criteria.md` defined three tiers (in-domain → pass, stretch → pass but deprioritise, Ausschluss → reject), but the classifier prompt only knew pass/reject. Result: ~17 false negatives in 745 listings — including Bosch Corporate Research positions explicitly called out as in-domain.

Root cause was not just prompt wording but information leakage: the classifier received candidate background and ranking nuance it couldn't act on correctly. Constraining the classifier's input to gate-only criteria is more robust than teaching it to ignore information it can see.

Relocating `skills.md` out of `search-terms/` acknowledges it was never a search term — it's a judge-facing ranking signal that ended up in the wrong directory.

## Consequences

- `SearchTerms` drops `skills` field — becomes two-field struct (`keywords`, `negative_keywords`).
- `prompts.py` loads `skills.md` directly from `triage-profile/`, strips `{...}` attributes inline.
- Slot names: `{SELF_DESCRIPTION}` → `{CANDIDATE_PROFILE}`, `{MATCH_CRITERIA}` → `{GATE_CRITERIA}`.
- Classifier prompt: single check, receives `{GATE_CRITERIA}` only.
- Judge prompt: receives `{CANDIDATE_PROFILE}` + `{SKILLS}`, no gate criteria.
- `init --refresh` must keep the package-owned skill bodies aligned with the current `analyse-listing` / `write-cv` split.
- Claude skills (`analyse-listing`, `write-cv`, `_shared/`) updated to the current filenames; `init --refresh` propagates.
- Supersedes the three-check portion of ADR-0034.
