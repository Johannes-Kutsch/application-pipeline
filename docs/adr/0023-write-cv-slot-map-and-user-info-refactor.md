# `/write-cv` emits a slot-map; `cv_template.tex` is the document; user-info holds raw facts

Three artifacts:

1. **`cv_template.tex`** (package code) — LaTeX document with moderncv preamble, three-build switch, `<<SLOT_NAME>>` markers. `\input`s `\CvDataDir/facts` and `\CvDataDir/content_pool`.
2. **`<app_dir>/cv.tex`** (per-listing slot-map by `/write-cv`) — `^%% SLOT: <name>$` markers, raw TeX bodies. No escape rules.
3. **`<settings-dir>/cv-template/cv_skeleton.tex`** (ADR-0035) — format-by-example for the `/write-cv` prompt. Refreshable by `init --refresh`.

Thirteen slots: `recipient_company/name/street/zip_city`, `opening`, `cover_intro/pivot/fit/closing`, `resume_berufserfahrung/ausbildung/projekte`, `skills_block` (mechanically assembled from **Skill Group** pool per ADR-0025).

Everything listing-invariant (name/address/photo/languages/hobbies) lives in **Facts** at `user-info/cv/facts.tex`.

## Why

- Single source of truth per concept. Slot-map format has zero escape rules — German prose with `\href`, umlauts go in verbatim.
- Skill interface = settings dir. Skill reads from settings dir, never from `src/`.
- content_pool metadata collapsed from six to three fields (`always`, `group`, `relevance`) — dropped `section:` (derived from block header), `tags:` (overlapped with relevance), `summary:` (Claude reads bodies directly).

## Consequences

- `compile_cv_cmd.py` reads slot-map, substitutes into `cv_template.tex`, writes to `.build/`, runs `pdflatex`. Windows backslash bug fixed via `as_posix()` paths.
- Resume blocks split: `<<RESUME_BERUFSERFAHRUNG>>`, `<<RESUME_AUSBILDUNG>>`, `<<RESUME_PROJEKTE>>`.
