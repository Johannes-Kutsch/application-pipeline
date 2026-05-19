# MiKTeX setup (Windows)

One-time setup so `src/application_pipeline/templates/latex/cv_template.tex` compiles locally.

## 1. Install MiKTeX

1. Download the installer from <https://miktex.org/download> (the "Basic MiKTeX Installer" is fine).
2. Run the installer with the defaults. When prompted about installing missing packages on the fly, pick **Yes** — this lets the first compile auto-fetch any missing CTAN packages.
3. Open *MiKTeX Console* once after install and click **Check for updates**, then **Update now**.

After install, `pdflatex` should be on `PATH`. Verify:

```
pdflatex --version
```

## 2. Required packages

The template uses the following packages (all available on CTAN; MiKTeX's on-the-fly install fetches them on the first compile):

- `babel` (with the `ngerman` language)
- `inputenc` (`utf8x` option) — via the `ucs` package
- `enumitem`
- `xpatch`
- `ragged2e`
- `setspace`
- `geometry`
- `etoolbox` — used by the `\BUILD`-flag gating (`\ifdefstring`)
- `graphicx` — used to inject the signature image

If "install on the fly" is disabled, install them explicitly from MiKTeX Console → *Packages*.

The `moderncv.cls`, `moderncvstylecasual.sty`, `moderncvcolorblue.sty`, and `tweaklist.sty` files are vendored under `src/application_pipeline/templates/latex/` and do **not** need to come from CTAN.

## 3. Compile the example

```
cd src\application_pipeline\templates\latex
pdflatex cv_template.tex
```

This should produce a 3-page `cv_template.pdf` showing literal `<<TOKEN>>` placeholders in the identity slots and `<<PHOTO>>` / `<<SIGNATURE>>` visible in the photo and signature regions.

## 4. Troubleshooting

- *"Package inputenc Error: Unicode character ... not set up for use with LaTeX"* — the template uses `utf8x` (from the `ucs` package), not `utf8`. Install `ucs` via MiKTeX Console if on-the-fly install was declined.
- *"File 'moderncv.cls' not found"* — you ran `pdflatex` outside `src/application_pipeline/templates/latex/`. The vendored class file lives next to the template; either `cd` into that directory first or pass an absolute path.
- *Signature image missing* — `cv_template.tex` reads `\UserDataDir/signature.png`. With the default `\UserDataDir=user-info.example`, the file `user-info.example/signature.png` must exist beside the template.
