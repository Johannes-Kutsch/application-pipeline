# CV layout migration from cv_7.tex and `\PersonalInfo` refactor

`cv_template.tex` and `facts.tex` restructured to match the user's pre-pipeline `cv_7.tex`.

**Layout:** resume section order Ausbildung → Projekte → Berufserfahrung (education-forward); resume closing with signature + city + date; cover-letter closing gains city + date; 2em Anschriftenfeld/Anrede gap.

**`\PersonalInfo` schema:** `facts.tex` defines `\myFirstname`, `\myFamilyname`, `\myCity` (raw scalars) plus three presentation macros — `\PersonalInfo`, `\Languages`, `\Hobbies`. User controls identity rows by editing `facts.tex`, not forking the template. Per-field raw defs (`\myStreet` etc.) retired, folded into `\PersonalInfo`.

## Why

- cv_7.tex is the artefact the user applied with. `\PersonalInfo` follows `\Languages`/`\Hobbies` precedent.
