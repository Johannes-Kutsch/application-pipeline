# Nested `user-info/` sub-dirs; search-terms split per section

`user-info/` reshapes from a flat 9-item directory into three sub-dirs matching the three downstream consumers, with no files left at the `user-info/` root:

```
user-info/
├── search-terms/
│   ├── keywords.md
│   ├── skills.md
│   └── negative-keywords.md
├── triage-profile/
│   ├── self-description.md
│   ├── domain-fit.md
│   ├── match-criteria.md
│   └── writing-style.md
└── cv/
    ├── facts.tex
    ├── content_pool.tex
    ├── profile.png
    └── signature.png
```

**Search-terms split.** The single `search-terms.md` is split into three files under `search-terms/`. Filename *is* the section — the `## Keywords` / `## Skills` / `## Negative Keywords` headers are dropped. Body shape is otherwise unchanged: topical sub-labels as plain non-bullet lines, flat `-` bullet entries. Loader switches from "one file, three `##` sections" to "three files, flat bullet parse per file". Per-file semantics: `keywords.md` missing OR present-but-zero-bullets raises `SearchTermsError` (preserves ADR-0028's degenerate-pipeline guard); `skills.md` and `negative-keywords.md` are optional — missing file treated as empty list.

**CV sub-dir breaks TeX paths.** `cv_template.tex` currently `\input`s `\UserDataDir/{facts,profile,signature,content_pool}` with `\UserDataDir` injected by `compile_cv_cmd.py` to `<settings-dir>/user-info/`. Once the four CV assets move into `user-info/cv/`, the macro is repointed to `<settings-dir>/user-info/cv/` and **renamed `\CvDataDir`** — the macro now means "the cv sub-dir", not "the user-info dir", and the name reflects that. The four `\input` lines in `cv_template.tex` switch to `\CvDataDir/…`; `compile_cv_cmd.py` injects `\def\CvDataDir{<user_info_dir>/cv}`.

**Triage Profile path.** ADR-0016's Prompt Loader reads its four files from `<settings-dir>/user-info/triage-profile/` instead of `<settings-dir>/user-info/`. No shape change to concatenation behaviour or the `{USER_INFO}` injection.

**Migration is a hard cutover** matching ADR-0028 / ADR-0024 precedent. The loader looks only at new paths; existing installs error on first run after upgrade until files are relocated. `init` / `init --refresh` seeds the new sub-dir layout; the operator manually moves their authored content from old → new locations and deletes the stale root-level files. No fallback, no auto-migrate code path.

## Considered alternatives

- **Keep flat layout, just split search-terms** — rejected: addresses only motivation (a) (search-terms.md too long for clean comment-editing), not (d) (`user-info/` itself is a flat dumping ground spanning four concerns).
- **Keep `\UserDataDir = user-info/`, change every `\input` to `\UserDataDir/cv/…`** — rejected: more edit sites, and `\UserDataDir` keeps a name whose meaning ("the user-info dir") is broader than its only consumer (CV compilation).
- **Two macros, `\UserDataDir` (root) + `\CvDataDir` (sub-dir)** — rejected: nothing outside the CV path uses the root macro, so two macros is plumbing without a second consumer.
- **Auto-migrate on first run** (detect old layout, move files, split search-terms.md into three) — rejected: one-shot migration code that lives forever after a single operator's one-time relocation; the manual move is bounded and the error message can name it precisely.
- **Read-both-locations transition window** — rejected: leaves dead branches in the loader; ADR-0028 already set the hard-cutover precedent for this kind of relocation.
- **Keep `## <section>` header inside each split file** (filename + header both carry section identity) — rejected: redundant, and creates a "what if they disagree" failure mode (`keywords.md` containing `## Skills`).

## Consequences

- Supersedes in part **ADR-0028** (single-file SearchTerms → three-file split; filename-as-section).
- Amends **ADR-0011** path conventions: `user-info/*.md` and `user-info/*.tex` now live under named sub-dirs; `init --refresh`'s seed-if-missing rule applies to the new paths.
- Amends **ADR-0030**: **Facts** lives at `user-info/cv/facts.tex`; **Content Pool** at `user-info/cv/content_pool.tex`; the `\UserDataDir` references in `cv_template.tex` become `\CvDataDir`.
- CONTEXT.md updates: **SearchTerms**, **Triage Profile**, **Facts**, **Content Pool**, and **Invocation** entries all touch path/macro names.
- HITL slice required for hosts the agent cannot reach: deployed `~/application-pipeline/user-info/` directories must be manually relocated. The CV-authoring skills in `.claude/skills/{analyse-listing,iterate-cv,write-cv}/` and `.claude/skills/_shared/` are in-repo and updated as part of the implementation slice.
- Existing `~/.claude/skills/` global skills outside this repo, if any reference these paths, must be updated by hand.
