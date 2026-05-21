# LaTeX CV + Cover Letter

## Compiling

Via the CLI (recommended):

```
application-pipeline compile-cv <DIR>
```

`<DIR>` is the path to your settings directory. The CLI compiles all three jobnames in sequence.

Raw `pdflatex` invocations — `-jobname` sets the output PDF filename (`cover.pdf`, `resume.pdf`, `combined.pdf`):

```
pdflatex -jobname=cover    "\def\UserDataDir{/abs/path/to/user-info}\def\BUILD{cover}\input{cv_template}"
pdflatex -jobname=resume   "\def\UserDataDir{/abs/path/to/user-info}\def\BUILD{resume}\input{cv_template}"
pdflatex -jobname=combined "\def\UserDataDir{/abs/path/to/user-info}\def\BUILD{combined}\input{cv_template}"
```

`\UserDataDir` defaults to `../user-info` when omitted. `\BUILD` controls which output is produced: `cover` (1 page), `resume` (2 pages), `combined` (3 pages).

## User data files

The template reads the following files from `<settings-dir>/user-info/`. All four must exist before the first compile.

| File | Purpose |
|---|---|
| `facts.tex` | Raw `\def`s for name, address, phone, email, social links, languages, hobbies (per ADR-0030) |
| `content_pool.tex` | Career items selected per application |
| `profile.png` | Headshot (passport-style) |
| `signature.png` | Handwritten signature scan |

`application-pipeline init <DIR>` seeds `user-info/` with editable stubs for the `.tex` files and placeholder images.

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

- `moderncv` (**≥ 2.0.0**) — the CV/cover-letter class. The package no longer vendors this; the host distro must provide it (per ADR-0031).
- `babel` (with the `ngerman` language)
- `inputenc` (`utf8x` option) — via the `ucs` package
- `enumitem`
- `xpatch`
- `ragged2e`
- `setspace`
- `geometry`
- `etoolbox`
- `graphicx`

If "install on the fly" is disabled, install them explicitly from MiKTeX Console → *Packages*.

`cv_template.tex` carries a `\@ifclasslater` guard that errors with a human-readable "install moderncv ≥ 2.0.0" message if the host class is too old.
