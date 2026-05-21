# LaTeX CV + Cover Letter

## Compiling

Via the CLI (recommended):

```
application-pipeline compile-cv <DIR>
```

`<DIR>` is the path to your settings directory. The CLI compiles all three jobnames in sequence.

Raw `pdflatex` invocations — `-jobname` sets the output PDF filename (`cover.pdf`, `resume.pdf`, `combined.pdf`):

```
pdflatex -jobname=cover    "\def\CvDataDir{/abs/path/to/user-info/cv}\def\BUILD{cover}\input{cv_template}"
pdflatex -jobname=resume   "\def\CvDataDir{/abs/path/to/user-info/cv}\def\BUILD{resume}\input{cv_template}"
pdflatex -jobname=combined "\def\CvDataDir{/abs/path/to/user-info/cv}\def\BUILD{combined}\input{cv_template}"
```

`\CvDataDir` defaults to `../user-info/cv` when omitted. `\BUILD` controls which output is produced: `cover` (1 page), `resume` (2 pages), `combined` (3 pages).

## User data files

The template reads the following files from `<settings-dir>/user-info/cv/`. All four must exist before the first compile.

| File | Purpose |
|---|---|
| `facts.tex` | Raw `\def`s for name, address, phone, email, social links, languages, hobbies (per ADR-0030) |
| `content_pool.tex` | Career items selected per application |
| `profile.png` | Headshot (passport-style) |
| `signature.png` | Handwritten signature scan |

`application-pipeline init <DIR>` seeds `user-info/cv/` with editable stubs for the `.tex` files and placeholder images.

## MiKTeX setup (Windows)

### Install

1. Download the installer from <https://miktex.org/download> (the "Basic MiKTeX Installer" is fine).
2. Run the installer with the defaults. When prompted about installing missing packages on the fly, pick **Yes**.
3. Open *MiKTeX Console* once after install and click **Check for updates**, then **Update now**.

Verify `pdflatex` is on `PATH`:

```
pdflatex --version
```

### Required packages

All available on CTAN; MiKTeX's on-the-fly install fetches them on the first compile:

- `babel` (with the `ngerman` language)
- `inputenc` (`utf8x` option) — via the `ucs` package
- `enumitem`
- `xpatch`
- `ragged2e`
- `setspace`
- `geometry`
- `etoolbox`
- `graphicx`

`moderncv` is **not** required from the host distro — the package vendors moderncv 1.2.0 under `src/application_pipeline/latex/` and isolates `.build/` from the host TEXMF via `TEXINPUTS` (per ADR-0034, supersedes ADR-0031). The host's moderncv version (typically v2.x on modern TeX Live / MiKTeX) is invisible to `compile-cv`.

If "install on the fly" is disabled, install the host-side packages explicitly from MiKTeX Console → *Packages*.
