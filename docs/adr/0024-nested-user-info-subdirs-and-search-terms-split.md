# Nested `user-info/` sub-dirs; search-terms split per section

`user-info/` reshapes from flat into three sub-dirs: `search-terms/` (keywords.md, skills.md, negative-keywords.md), `triage-profile/` (gate-criteria.md, candidate-profile.md, skills.md), `cv/` (facts.tex, content_pool.tex, cover-patterns.md, profile.png, signature.png). No files at root.

Search-terms split: single `search-terms.md` → three files. Filename *is* the section — no `##` header inside each file. `keywords.md` missing or empty → `SearchTermsError`; `skills.md` and `negative-keywords.md` optional.

`\UserDataDir` renamed `\CvDataDir` — repointed to `user-info/cv/`. Hard cutover migration.

## Why

- Flat 9-item directory spanned four concerns. Sub-dirs match downstream consumers.
- Filename-as-section avoids "what if they disagree" failure mode.
- `\CvDataDir` reflects its only consumer (CV compilation).

## Consequences

- Supersedes in part ADR-0021 (single-file SearchTerms → three-file split).
- Amends ADR-0008 path conventions: `user-info/*.md` now under named sub-dirs.
- Amends ADR-0023: Facts at `user-info/cv/facts.tex`; `\CvDataDir` replaces `\UserDataDir`.
