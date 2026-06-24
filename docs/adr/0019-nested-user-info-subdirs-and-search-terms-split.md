# Nested `user-info/` sub-dirs; search-terms split per section

`user-info/` reshapes into three sub-dirs: `search-terms/` (keywords.md, negative-keywords.md), `triage-profile/` (gate-criteria.md, candidate-profile.md, skills.md — ADR-0036), `cv/` (facts.tex, content_pool.tex, cover-patterns.md, profile.png, signature.png).

Search-terms split: single file → per-section files. Filename *is* the section. `keywords.md` missing/empty → `SearchTermsError`; `negative-keywords.md` optional.

`\UserDataDir` renamed `\CvDataDir` — repointed to `user-info/cv/`.

## Why

- Flat 9-item directory spanned four concerns. Sub-dirs match downstream consumers. Filename-as-section avoids disagreement failure mode.

## Consequences

- Amends ADR-0016 (single-file → split). Amends ADR-0004 path conventions.
