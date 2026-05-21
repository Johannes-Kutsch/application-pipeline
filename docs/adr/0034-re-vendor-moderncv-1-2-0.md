# Re-vendor moderncv 1.2.0 for visual fidelity; supersedes ADR-0031

`compile-cv` reverts to vendoring the complete moderncv 1.2.0 release inside the package (`src/application_pipeline/latex/`) and isolates `pdflatex` from the host TEXMF via `TEXINPUTS`. ADR-0031's host-distro-provides-moderncv decision is superseded.

## Context

ADR-0031 (issue #474) cut the partial vendored moderncv set and migrated `cv_template.tex` to the host-provided moderncv v2.x API. After the migration:

- The smoke test never reached a working state. Issue #487 (`\cvitem` mode error in resume page finalization) was the third v2.x-incompatibility surfacing after the initial migration patches; localising the failure to a single line failed (commenting out the `\xpatchcmd` trailing-dot patch did not change the symptom).
- Stock moderncv 2.x compiles cleanly on the host, so the v2.x toolchain itself is healthy — the gap is inside our migrated template.
- More importantly: when the stock v2.x output was reviewed against the v1.2.0-era PDFs, the v2.x layout was rejected outright — head shape, sidebar, section headings. None of those are addressable by porting user-level customisations on top of v2.x; they are produced by moderncv's own `.sty` files, which were rewritten between v1.2.0 and v2.x.

The visual delta means there is no v2.x port that satisfies the applicant. The choice collapses to: stay on the broken v2.x migration (option 1, rejected by user preference), incrementally port v1.2.0 patches onto v2.x in the hope of recovering the layout (option 2, rejected — the layout lives in style files, not user code), or revert to v1.2.0.

## Decision

Vendor the **complete** moderncv 1.2.0 distribution under `src/application_pipeline/latex/` — class, every head/body/colour/style `.sty`, `tweaklist.sty`, the icon sets. Drop the v2.x version guard from `cv_template.tex`. Restore the v1.2.0-API touchpoints (`\xpatchcmd{\cventry}{.\strut}{\strut}{}{}` against v1.2.0 internals, `\renewcommand*{\makeletterclosing}{...}` against v1.2.0's letter body shape).

`compile_cv_cmd.py` invokes `pdflatex` with `env={**os.environ, "TEXINPUTS": "./;"}` on Windows (`./:` on POSIX), so `.build/` is searched before the host TEXMF and the host's v2.x copies stay invisible.

## Why

- **Frozen upstream.** ADR-0031's principal objection to vendoring was "open-ended file-tracking; one upstream patch and the vendored set is wrong again". This assumes upstream keeps moving. moderncv 1.2.0 is from 2014 and frozen — there is no patch stream to track. Vendoring a frozen release is bounded, one-time work.
- **The earlier vendor was incomplete, not unmaintainable.** #474 happened because the package shipped only four files (class + casual + blue + tweaklist); v1.2.0's casual style transitively loads more `.sty` files that fell through to the host's v2.x copies, producing a version split. A *complete* vendor doesn't have this failure mode — every file v1.2.0 references is shipped together.
- **TEXINPUTS isolation is platform-portable from Python.** ADR-0031 rejected `TEXINPUTS=./` as "shell-specific" — true for shell invocation, but `compile_cv_cmd.py` sets the env-var via `subprocess.run(env=...)`, a Python dict. Behaviour is identical across PowerShell, bash, cmd.
- **Visual fidelity is the goal.** The applicant has already chosen moderncv 1.2.0 casual + blue and its concrete visual output. Class-level migration was explicitly rejected as out-of-scope (ADR-0031 noted this for `awesome-cv` / `altacv`); the same reasoning applies to within-class major-version drift when the major version changes the layout.

## Considered alternatives

- **Fix the broken v2.x migration in place.** Rejected: localising #487 to a single line failed, and even a successful fix lands on a visually-rejected layout. The v2.x style files cannot be coerced back into v1.2.0's appearance via user-level patches.
- **Incrementally port v1.2.0 customisations onto v2.x with a smoke test after each step.** Rejected for the same reason: the appearance the user wants back comes from v1.2.0's `.sty` files, not from user-level patches. Incremental porting would converge on a v2.x document that compiles cleanly but looks wrong.
- **Migrate to a non-moderncv class** (`awesome-cv`, `altacv`). Out of scope, same as in ADR-0031.
- **Downgrade the host moderncv to 1.2.0 site-wide.** Fragile: breaks on any MiKTeX/TeX Live update; no automation. The whole point of vendoring is to take the host's moderncv version out of the equation.
- **Curated subset of v1.2.0** (only casual + blue + transitive deps). Rejected: this is what the previous vendor was, and the curation was the failure mode. Whole-distro vendoring has a trivial invariant ("everything moderncv 1.2.0 shipped"); a curated subset requires retracing on every `\moderncvstyle` change.

## Consequences

- **`src/application_pipeline/latex/` regrows.** The complete v1.2.0 release (~30-50 files, ~250 KB) is committed verbatim. Source: CTAN historical archive snapshot of moderncv 1.2.0.
- **`cv_template.tex` reverts toward its pre-#474 shape.** The `\@ifclasslater` v2.x guard is removed; the `\xpatchcmd` trailing-dot patch returns to its v1.2.0 form (`\AtBeginDocument` wrapper unnecessary); `\renewcommand*{\makeletterclosing}{...}` returns to its v1.2.0 body shape.
- **`compile_cv_cmd.py`** restores the file-copy loop into `.build/` for all vendored `.cls` / `.sty` files, and gains a `env=` kwarg on the `pdflatex` subprocess call to inject `TEXINPUTS`.
- **`pyproject.toml`** restores `*.cls` / `*.sty` patterns in `package-data` glob (removed by ADR-0031).
- **`docs/latex.md`** required-packages list drops the `moderncv (≥ 2.0.0)` line and the "host distro must provide it" sentence; the `\@ifclasslater` paragraph is removed; the host-dependency list narrows to packages that are *not* moderncv (babel, xpatch, etoolbox, geometry, inputenc/ucs, enumitem, ragged2e, setspace, graphicx). Those continue to come from the host distro.
- **Pi/Linux unchanged.** `compile-cv` remains a Windows-dev-only command; the Pi has no LaTeX dependency. No cron-side impact.
- **Stale `.build/` directories** (issue #477) become less problematic: the v1.2.0 `.cls`/`.sty` copies left behind by failed pre-migration attempts are exactly what we now ship. Operators can leave stale `.build/` dirs alone; `compile-cv` overwrites them on the next successful compile.
- **No automatic state migration.** After the package update, operators with v2.x-era `.build/` clutter can `rm -rf application-pipeline/applications/*/.build/` once, but it is not strictly required. `init --refresh` does not touch `applications/`.
- **Supersedes ADR-0031.** The "stop vendoring; depend on host distro" decision is reversed. The "frozen upstream" reasoning that defuses ADR-0031's main objection is captured above and should be the first place a future reader looks if they consider re-removing the vendor.
