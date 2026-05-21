# CV layout migration from cv_7.tex and `\PersonalInfo` refactor

`cv_template.tex` and `facts.tex` are restructured to match the layout of the user's pre-pipeline `cv_7.tex` and to put per-row identity content under user control. Supersedes the relevant parts of ADR-0030.

## Context

The pipeline's `cv_template.tex` had drifted visually from the user's known-good `application-pipeline/CV/cv_7.tex`:

- Resume sections were ordered Berufserfahrung → Ausbildung → Projekte; cv_7 puts Ausbildung → Projekte → Berufserfahrung (education-forward, suited to a mid-bootcamp AI Engineering candidate).
- The Persönliche Informationen section was hardcoded to six fixed rows (Name / Adresse / Telefon / E-Mail / GitHub / LinkedIn). cv_7 had a different set including Geboren and lacked the social rows.
- The resume had no signed/dated closing block; cv_7 ended with `\vspace*{\fill}\\Hamburg, \today`.
- The cover-letter closing showed only the signature image and typed name; cv_7's `Ort, \today` convention was absent.
- The Anschriftenfeld/Anrede gap was too tight by ~2em vs cv_7's hand-modified casual.sty.
- The stock moderncv 1.2.0 `\makeletterfooter` rendered the bold name line with a trailing `\\` that orphaned (empty `\ifthenelse` branches) — visible as "There's no line here to end" when the smoke fixture used the shipped facts.tex. cv_7's hand-modified casual.sty had commented this line out; ADR-0034 mandates the .sty stays verbatim.

Independently, ADR-0030's facts.tex schema duplicated each identity field as a raw `\def` (`\myStreet`, `\myZip`, …) plus a display-mirror `\def` (`\addressdisplay`, …), with `cv_template.tex` wiring them into a fixed list of `\cvitem` rows. Users could not add a row (e.g. Geboren) or drop a row (e.g. LinkedIn) without editing `cv_template.tex`.

## Decision

**Layout** (`cv_template.tex`):

- Resume section order: Persönliche → Ausbildung → Projekte → Berufserfahrung → Kenntnisse → Sprachen → Hobbies.
- Resume ends with a closing block (`\vfill\noindent\includegraphics{signature}\\\myFirstname\\\myCity, \today`) inside the resume guard, mirroring the cover-letter close.
- `\makeletterclosing` override appends `\\\myCity{}, \today` so the cover closing matches the resume closing (signature, typed name, place + date).
- `\xpatchcmd{\makelettertitle}{\@date\\[2em]}{\@date\\[2em]\\[2em]}` adds the 2em gap cv_7 had between the date row and the opening line.

**`\PersonalInfo` schema** (`facts.tex`):

- `facts.tex` now defines six things: `\myFirstname`, `\myFamilyname`, `\myCity` (raw scalars referenced template-wide), and three presentation macros — `\PersonalInfo`, `\Languages`, `\Hobbies` — each a block of `\cvitem` / `\cvlistitem` calls.
- `cv_template.tex` emits `\cvitem{Name}{\myFirstname{} \myFamilyname}` (always present, name is template-controlled) and then `\PersonalInfo`. Which rows appear (Adresse / Telefon / E-Mail / GitHub / Geboren / LinkedIn / …) is the user's choice, edited in their facts.tex.
- Retired: per-field raw defs `\myStreet`, `\myZip`, `\myPhone`, `\myEmail`, `\myGithub`, `\myLinkedin`, and the display-mirror defs `\addressdisplay`, `\phonedisplay`, `\emaildisplay`, `\githubdisplay`, `\linkedindisplay` (all folded into `\PersonalInfo`).

## Why

- **Layout migration is the user's stated CV reference.** cv_7.tex is the artefact the user actually applied with; the pipeline template was meant to reproduce it, not redesign it. Every visual delta here is a port from cv_7.
- **`\PersonalInfo` follows existing precedent.** `\Languages` and `\Hobbies` were already presentation macros in facts.tex — block of `\cvitem` / `\cvlistitem` calls. Extending the same pattern to Persönliche Informationen makes the three blocks symmetric. Three macros, three sections, one rule: presentation macros live in facts, the template just calls them.
- **User controls which identity rows appear.** Some applicants want Geboren, some don't. Some want LinkedIn, some don't. With `\PersonalInfo` this is a one-line edit in facts.tex, not a fork of the template.
- **`\myCity` is a scalar because two places need it.** Both the resume closing block and the cover-letter closing render `\myCity{}, \today`. A scalar avoids duplicating the city string.

## Considered alternatives

- **Keep individual raw defs (`\myStreet` etc.) alongside `\PersonalInfo`.** Rejected per user preference: no current macro references them outside the (now-deleted) Persönliche rows, and keeping them as zombie scalars invites future drift.
- **Move `\section{Persönliche Informationen}` into `\PersonalInfo`.** Rejected: breaks symmetry with `\Languages` / `\Hobbies`, both of which are bodies under template-owned section headings.
- **Sign the resume but not the cover; or vice versa.** Rejected: combined builds put both halves in one PDF and DE HR convention signs both. Two signatures in one document is the convention, not a duplication bug.
