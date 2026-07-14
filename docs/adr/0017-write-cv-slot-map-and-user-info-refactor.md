# `/write-cv` emits a slot-map; `cv_template.tex` is the document

Three artifacts:

1. **`cv_template.tex`** (package code) — LaTeX document with moderncv preamble, three-build switch, `<<SLOT_NAME>>` markers. `\input`s `\CvDataDir/facts` and `\CvDataDir/content_pool`.
2. **`<app_dir>/cv.tex`** (per-listing slot-map) — `^%% SLOT: <name>$` markers, raw TeX bodies.
3. **`<settings-dir>/cv-template/cv_skeleton.tex`** (ADR-0026) — format-by-example for `/write-cv`. Refreshable.

Thirteen slots: `recipient_company/name/street/zip_city`, `opening`, `cover_intro/pivot/fit/closing`, `resume_berufserfahrung/ausbildung/projekte`, `skills_block` (mechanically assembled from **Skill Group** pool per ADR-0019).

Listing-invariant content in **Facts** at `user-info/cv/facts.tex`.

## Why

- Single source of truth per concept. Slot-map format has zero escape rules.

## Consequences

- `compile_cv_cmd.py` reads slot-map, substitutes into `cv_template.tex`, writes to `.build/`, runs `pdflatex`.
