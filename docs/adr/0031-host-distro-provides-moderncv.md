# compile-cv depends on host TeX distro for moderncv ≥ 2.0.0; stop vendoring

`compile-cv` now relies on the host TeX distribution (MiKTeX on Win, TeX Live on Linux) to provide the moderncv class and its style files. The `src/application_pipeline/latex/{moderncv.cls, moderncvstylecasual.sty, moderncvcolorblue.sty, tweaklist.sty}` files added in ADR-0011 / issue #401 are removed; `compile_cv_cmd.py` no longer copies any `.cls`/`.sty` into `<app_dir>/.build/`.

`cv_template.tex` migrates from the v1.2.0 API to the v2.x API and carries a `\@ifclasslater{moderncv}{2015/01/01}{}{\PackageError{...}}` guard that errors with a human-readable "install moderncv ≥ 2.0.0" message when the host class is too old. The required-package list in `docs/latex.md` adds `moderncv` (≥ 2.0.0) alongside the existing `babel` / `xpatch` / `geometry` / etc. entries that have always relied on the host distro.

## Context

Issue #474. The package shipped a partial v1.2.0 moderncv set (four files: class + casual style + blue colour + tweaklist) on the assumption that pdflatex would resolve everything from the local copy. In practice, the host's moderncv v2.x (MiKTeX rolling, TeX Live ≥ 2020) ships transitive style files the v1.2.0 set never included (e.g. the v2.x head-style machinery — `\@initializecommand`, `\moderncvhead`). pdflatex resolves the class file from `.build/` (v1.2.0) and some `.sty` files from the host TEXMF (v2.x), producing a version split that fails with the symptom triple:

- `\firstname already defined` — v1.2.0 class redefines what the v2.x head style already declared.
- `\@initializecommand undefined` — v1.2.0 class does not know v2.x's internal API.
- `\moderncvhead undefined` — same.

The bug surfaces on every `compile-cv` invocation against a real application directory. The `2026-05-20-softfair_AI-Engineer` application was the trigger.

## Decision

Stop vendoring. Depend on the host TeX distribution for moderncv. Pin the minimum version at v2.0.0 (2015) — that is when the phone-collection API and `\@initializecommand` head-style machinery landed; anything ≥ 2.0.0 has the v2.x API, anything below it is v1.2.x.

Enforce the floor at the LaTeX level inside `cv_template.tex`, not at the Python level — Python cannot meaningfully validate a TeX dependency, and a loud LaTeX-level error is the user-facing failure mode that actually matters.

## Why

- **The vendored set was incomplete.** Completing the v1.2.0 vendor would require tracking every transitive `.sty` moderncv's `casual` style loads and re-shipping on every upstream release. moderncv's upstream is dormant but not dead; this is open-ended maintenance for a non-load-bearing reason.
- **Host distros already satisfy ≥ 2.0.0 trivially.** TeX Live 2020 ships moderncv 2.0.2; MiKTeX rolling ships 2.0.x; TL 2024 still ships 2.0.2 (upstream has not moved). Practically every modern install satisfies the floor.
- **The package already depends on host-distro packages.** `babel`, `xpatch`, `geometry`, `enumitem`, `setspace`, `ragged2e`, `etoolbox`, `graphicx`, `inputenc/ucs` all come from the host. moderncv joining that list is consistent, not novel.
- **Vendoring was the failure mode.** #474 exists *because* the vendored partial set drifted out of sync with the host's transitive style files. Removing the vendoring removes the failure mode.
- **A loud version guard beats a cryptic stack.** `\PackageError{cv_template}{moderncv >= 2.0.0 required}` is one line a user can act on. The current `\@initializecommand undefined` cascade is a 30-line dead end.

## Considered alternatives

- **Vendor a complete consistent moderncv tree** (class + all `.sty` files the v2.x casual style transitively loads): rejected. Open-ended file-tracking; one upstream patch and the vendored set is wrong again.
- **Vendor v1.2.0 and force `TEXINPUTS=./;`** to isolate `.build/` from the host TEXMF: rejected. Brittle (the env-var override is shell-specific; PowerShell vs bash vs cmd diverge), and it locks the user into a 2012-era class with no benefit. The package already follows the host distro for every other LaTeX dependency.
- **Pin an exact moderncv version**: rejected. Upstream is dormant — the v2.0.2 → present diff is small, no churn worth defending against, and exact-pinning would force users to downgrade MiKTeX. A `≥ 2.0.0` floor with a guard is sufficient.
- **Migrate to a non-moderncv class entirely** (e.g. `awesome-cv`, `altacv`): rejected as out of scope. The applicant has already chosen moderncv `casual` + `blue`; class-level migration is a separate decision.

## Consequences

- **`src/application_pipeline/latex/` shrinks.** The four vendored files (`moderncv.cls`, `moderncvstylecasual.sty`, `moderncvcolorblue.sty`, `tweaklist.sty`) are removed. The v1.2.0 baseline is recoverable via `git show 47ef403:src/application_pipeline/templates/latex/<file>` if any post-migration surprise needs side-by-side comparison.
- **`compile_cv_cmd.py` simplifies.** The `for item in pkg.iterdir()` copy loop stops copying anything except `cv_template.tex`'s contents (read as a string, substituted, written to `.build/`). `_LATEX_SUFFIXES` becomes irrelevant.
- **`cv_template.tex` migrates to the v2.x API.** Two known-risk touchpoints:
  - The `\xpatchcmd{\cventry}{.\strut}{\strut}{}{}` trailing-dot strip targets v1.2.0 internals and silently fails to a warning against v2.x's rewritten `\cventry`. The patch is re-authored against the v2.x source.
  - The `\renewcommand*{\makeletterclosing}{...}` signature-injection override is audited against v2.x's stock `\makeletterclosing` body shape to preserve any non-cosmetic plumbing it currently elides.
- **Version guard at the top of `cv_template.tex`.** A `\@ifclasslater`-style check errors loudly if the host moderncv is < 2.0.0. Pre-empts the `\@initializecommand undefined` cascade for users on older distros.
- **ADR-0011 superseded on the LaTeX-files-ship-in-package point.** ADR-0011's claim that `moderncv.cls` + `.sty`s ship inside the package and are copied into `.build/` is no longer accurate; `cv_template.tex` and `slot_map.py` are the only LaTeX-related package resources that remain.
- **ADR-0027 partially superseded.** The `package-data` glob in `pyproject.toml` keeps `templates/latex/*.tex` (for `cv_template.tex` and `cv_skeleton.tex`) but no longer needs `*.cls` / `*.sty` patterns.
- **No Pi/Linux changes.** `compile-cv` remains a Windows-dev-only command; the cron pipeline on the Pi never compiles CVs. The Pi has no `pdflatex` requirement and no moderncv requirement.
- **`docs/latex.md` updated.** Required-packages list adds `moderncv` (≥ 2.0.0); the "vendored inside the package" sentence is replaced. (Stale `identity.tex` / `contact.tex` rows in the user-data-files table are also corrected to `facts.tex` in the same pass — pre-existing rot from ADR-0030 not strictly caused by this decision but in the affected file.)
- **Stale `.build/` directories** in `application-pipeline/applications/*/` left by failed pre-migration compile attempts contain v1.2.0 `.cls`/`.sty` copies. `compile_cv_cmd.py` only `shutil.rmtree`s `.build/` on success, so these persist as stale state. Operator cleanup is required — see issue #477. `init --refresh` does not touch `applications/`.
- **Migration:** no automatic migration of user-side state. After the package update, the operator runs `rm -rf application-pipeline/applications/*/.build/` once, then re-invokes `compile-cv` against any application that needs PDFs regenerated.
