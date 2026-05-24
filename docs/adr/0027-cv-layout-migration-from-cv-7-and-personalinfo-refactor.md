# CV layout migration from cv_7.tex and `\PersonalInfo` refactor

`cv_template.tex` and `facts.tex` restructured to match the user's pre-pipeline `cv_7.tex`. Supersedes relevant parts of ADR-0023.

**Layout** changes: resume section order Ausbildung → Projekte → Berufserfahrung (education-forward); resume closing block with signature + city + date; cover-letter closing gains city + date; 2em Anschriftenfeld/Anrede gap.

**`\PersonalInfo` schema**: `facts.tex` defines `\myFirstname`, `\myFamilyname`, `\myCity` (raw scalars) plus three presentation macros — `\PersonalInfo`, `\Languages`, `\Hobbies` — each a block of `\cvitem` / `\cvlistitem` calls. User controls which identity rows appear by editing `facts.tex`, not forking the template. Retired: per-field raw defs (`\myStreet` etc.) and display-mirror defs — all folded into `\PersonalInfo`.

## Why

- cv_7.tex is the artefact the user applied with. Every delta is a port from it.
- `\PersonalInfo` follows existing `\Languages` / `\Hobbies` precedent — symmetric pattern.
- `\myCity` is a scalar because two places need it (resume + cover closings).
