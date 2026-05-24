# Classifier becomes three-check gatekeeper; `domain-fit.md` merges into `match-criteria.md`

Classifier verdict field renamed `in_domain` → `matches`. Three sequential checks before emitting Header + Summary: (1) domain fit, (2) skill/experience floor, (3) preference fit. Any "no" short-circuits to `{"matches": false}`.

**Triage Profile** collapses from three files to two: `self-description.md` + `match-criteria.md`. Retired `domain-fit.md` content merges into `match-criteria.md`. Both call sites now see the same profile, emitted as three named slots — `{SELF_DESCRIPTION}`, `{MATCH_CRITERIA}`, `{SKILLS}` — that each prompt template places with its own heading. Supersedes the former per-call-site routing.

## Why

- Tighter classifier bar shrinks Pool size at source — addressing Judge-at-scale concern (issue #524).
- Classifier needed match-criteria content to drop "pure management" or "consulting" roles.
- `domain-fit.md` and `match-criteria.md` were the same content type in two files.
- `matches` is the honest name for a three-check verdict.

## Amendment (post-#535): named profile slots

Loader exposes `{SELF_DESCRIPTION}`, `{MATCH_CRITERIA}`, `{SKILLS}` — each template places them independently. `{SKILLS}` baked into judge at `load_prompts` time (judge only per ADR-0013). Classifier uses flat H1; judge keeps H2 sub-headers.

`load_prompts(config)` becomes `load_prompts(config, search_terms)`.

## Amendment: `in_domain` dedup status renamed to `matched`

Issue #558 renamed dedup status `in_domain` → `matched` to restore symmetry with verdict field.

## Consequences

- `RelevanceVerdict.matches` field. Legacy `in_domain` shape fails with `ExtractorMalformedJSONError`.
- `PromptError` raised if legacy `domain-fit.md` still present.
- `DeduplicationStore.mark_matched` (was `mark_in_domain`). Four narrow methods: `mark_out_of_domain`, `mark_matched`, `mark_selected_by_judge`, `mark_expired`.
