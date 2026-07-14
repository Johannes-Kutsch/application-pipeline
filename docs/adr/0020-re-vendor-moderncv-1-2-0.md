# Re-vendor moderncv 1.2.0 for visual fidelity

`compile-cv` vendors complete moderncv 1.2.0 inside the package (`src/application_pipeline/latex/`) and isolates `pdflatex` from host TEXMF via `TEXINPUTS`.

## Why

- moderncv 1.2.0 frozen since 2014 — bounded, one-time vendor. Prior partial vendor (4 files) fell through to host v2.x, producing visual regressions the applicant rejected.

## Consequences

- Complete v1.2.0 release under `src/application_pipeline/latex/`.
- `compile_cv_cmd.py` copies into `.build/` with `env=` kwarg for `TEXINPUTS`.
- `cv_template.tex` uses v1.2.0 API.
