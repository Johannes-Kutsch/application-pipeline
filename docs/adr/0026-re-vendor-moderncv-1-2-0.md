# Re-vendor moderncv 1.2.0 for visual fidelity

`compile-cv` vendors the complete moderncv 1.2.0 release inside the package (`src/application_pipeline/latex/`) and isolates `pdflatex` from the host TEXMF via `TEXINPUTS`. Supersedes the prior host-distro-provides-moderncv decision.

## Why

- **Frozen upstream.** moderncv 1.2.0 is from 2014 and frozen — no patch stream to track. Vendoring a frozen release is bounded, one-time work.
- **Earlier vendor was incomplete, not unmaintainable.** Partial set (4 files) fell through to host v2.x copies, producing a version split. Complete vendor eliminates this.
- **Visual fidelity.** Stock v2.x output was rejected — head shape, sidebar, section headings differ. These come from moderncv's `.sty` files, not user code. No v2.x port satisfies the applicant.
- **TEXINPUTS isolation is platform-portable from Python** via `subprocess.run(env=...)`.

## Consequences

- Complete v1.2.0 release (~30-50 files) committed under `src/application_pipeline/latex/`.
- `compile_cv_cmd.py` restores file-copy loop into `.build/` and gains `env=` kwarg for `TEXINPUTS`.
- `cv_template.tex` reverts to v1.2.0 API. No version guard needed.
