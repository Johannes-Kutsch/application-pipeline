# `/write-cv` emits a slot-map; `cv_template.tex` is the document; user-info holds raw facts

The CV/cover-letter build has three artifacts with one job each:

1. **`cv_template.tex`** (package code, `src/application_pipeline/latex/`) — the LaTeX document. Carries the moderncv preamble, the three-build switch (`cover` / `resume` / `combined`), all moderncv-API bridges (`\address{...}{...}{}`, `\firstname{...}`, photo geometry, `\title`), and `<<SLOT_NAME>>` markers for per-listing content. `\input`s `\UserDataDir/facts` and `\UserDataDir/content_pool` directly.
2. **`<app_dir>/cv.tex`** (per-listing, written by `/write-cv`) — a slot-map. Sectioned-marker syntax: `^%% SLOT: <name>$` introduces a body that runs until the next `%% SLOT:` line or EOF. Bodies are raw TeX fragments (multi-line prose with `\href`, `\textit`, umlauts, etc. — no escape rules).
3. **`<settings-dir>/skills/cv_skeleton.tex`** (package-shipped, init-seeded, refreshable per ADR-0011 amendment) — the format-by-example file the `/write-cv` skill injects into its prompt. Carries the slot list + per-slot prompt guidance (e.g. "cover_intro: 2-3 sentences, hook by ..."). Lives in the settings dir, not `src/`, so the skill's interface is the user's settings directory and never reaches into package source.

`compile-cv` reads `<app_dir>/cv.tex`, parses it into `{slot_name: body}`, substitutes each `<<NAME_UPPERCASE>>` marker in `cv_template.tex` with the body, writes the result to `<app_dir>/.build/cv.tex`, runs `pdflatex` three times (one per `\BUILD`).

## Slot list (committed)

Eleven slots, all per-listing, all written by `/write-cv`:

- `recipient_line_1`, `recipient_line_2`
- `opening`
- `cover_intro`, `cover_pivot`, `cover_fit`, `cover_closing`
- `resume_berufserfahrung`, `resume_ausbildung`, `resume_projekte` (the three resume blocks; supersedes the single `<<RESUME_BODY>>` slot per issue #453)
- `skills_block`

Everything else (name, address, phone, email, social links, photo, languages, hobbies, `\title{Lebenslauf}`) is **listing-invariant** and read directly from `user-info/facts.tex`.

## user-info refactor

`user-info/identity.tex` and `user-info/contact.tex` merge into `user-info/facts.tex`, containing only raw `\def`s — one per fact, no moderncv API calls and no display-mirror duplication. Adds `\def\Languages{...}` and `\def\Hobbies{...}` (previously not present; were a TODO under the retired `<<LANGUAGES_BLOCK>>` / `<<HOBBIES_BLOCK>>` template slots).

```
% user-info/facts.tex
\def\myFirstname{Johannes}
\def\myFamilyname{Kutsch}
\def\myStreet{Probsteierstraße 27}
\def\myZip{22049 Hamburg}
\def\myPhone{01525 3670311}
\def\myEmail{johanneskutsch@live.de}
\def\myGithub{https://github.com/Johannes-Kutsch}
\def\myLinkedin{https://www.linkedin.com/in/johannes-kutsch/}
\def\Languages{\cvitemwithcomment{Deutsch}{Muttersprache}{} ...}
\def\Hobbies{\begin{itemize}\item ...\end{itemize}}
```

All moderncv plumbing moves into `cv_template.tex`:

```
\input{\UserDataDir/facts}
\firstname{\myFirstname}\familyname{\myFamilyname}
\address{\myStreet}{\myZip}{}
\phone[mobile]{\myPhone}
\email{\myEmail}
\social[github]{\myGithub}
\social[linkedin]{\myLinkedin}
\title{Lebenslauf}
\photo[120pt][1pt]{\UserDataDir/profile}
\def\addressdisplay{\myStreet, \myZip}
\def\phonedisplay{\myPhone}
...
```

The `<<LANGUAGES_BLOCK>>` and `<<HOBBIES_BLOCK>>` markers are removed from `cv_template.tex`; the template references `\Languages` / `\Hobbies` directly.

## content_pool.tex metadata

Per-item header collapses from six fields to three, each with one job:

```
%%% ITEM: itemJobOctofoxMLE
%%% always: false
%%% group: octofox
%%% relevance: mle=high, games=medium, agents=high
\newcommand{\itemJobOctofoxMLE}{...}
```

Dropped:

- **`section:`** — derive from the `% ===== <name> =====` block header above the item.
- **`tags:`** — domain-topic tags (`mle`, `games`, `agents`) folded into `relevance:`; format tags (`freelance`, `bootcamp`, `awarded`, `solo`, etc.) were never read by anything, dropped; the `always` flag becomes its own boolean field.
- **`summary:`** — `/write-cv` reads the `\newcommand` body directly (raw TeX in the prompt; Claude handles it without a stripper).

## Why

- **Single source of truth per concept.** Per-listing prose lives in `cv.tex` only; listing-invariant facts in `facts.tex` only; LaTeX structure in `cv_template.tex` only. No mirroring between moderncv calls and display macros, no `summary:` that drifts from the `\cventry` body, no `tags:`/`relevance:` overlap.
- **Slot-map format chosen for LLM legibility, not engineering elegance.** A sectioned-marker `.tex` file has zero escape rules — German prose with `\href{...}{...}`, `---`, umlauts, brace-bearing macros all go in verbatim. The LLM emits it correctly first try; the parser is `re.split(r"^%% SLOT: (\w+)$", text, flags=re.M)` and a dict comprehension.
- **Skill interface = settings dir.** The skill reads `<settings-dir>/skills/cv_skeleton.tex` rather than reaching into `src/application_pipeline/latex/`. Clean coupling: the skill talks to the settings dir like every other consumer; the package is responsible for keeping the skeleton seeded and refreshed.
- **`cv_skeleton.tex` is refreshable** (ADR-0011 amendment) because it's structurally tied to `cv_template.tex`. Adding a slot in v1.x is a package change that flows through to the user's skeleton on the next `init --refresh` (which the cron wrapper invokes on every tick per ADR-0027). Per-slot prompt guidance customization is a future feature; trigger it if/when a user asks.
- **content_pool metadata simplified once the LLM contract is settled.** The `summary:` field existed because the agent couldn't be trusted to parse TeX; with Claude reading bodies directly, the duplicated abstract is dead weight. `tags:` did three jobs (domain, format, flag); splitting them into `relevance:` (domain), nothing (format — wasn't load-bearing), and `always:` (flag) gives each job a typed home.

## Considered alternatives

- **`cv.tex` as a full standalone document** (Option A in the original bug report): rejected. Would force `/write-cv` to emit a complete LaTeX file including preamble + moderncv bridges + slot bodies, and would couple per-listing prose to package-level structural decisions. The current `cv.tex` in the application folder is a one-off written before the contract was settled — does not reflect the intended model.
- **`cv.tex` as JSON tokens**: rejected. Backslash-doubling (`"\\href{...}{...}"`) is a constant LLM-output error source; multi-line German prose in JSON strings is unreadable.
- **`cv.tex` as `\def` bodies**: rejected. Brace-balancing inside `\def` bodies is fragile for the long paragraphs with inline `\href`/`\textit` that the cover letter actually contains.
- **Skill reads `cv_template.tex` directly and regex-extracts `<<\w+>>` markers** (auto-discover slots): rejected. Loses the per-slot prompt guidance (`cover_intro` vs `cover_pivot` are wire-format identical but semantically distinct). The guidance has to live somewhere with the slot list; a skeleton file is the natural home.
- **Skeleton in `src/application_pipeline/latex/`, skill reads from there**: rejected. The skill's interface contract should be the settings dir, not the package source tree. Keeping the skeleton in `<settings-dir>/skills/` lets the skill stay agnostic of install location.
- **Keep `summary:` and `tags:`** in content_pool: rejected. Both were workarounds for "the agent can't read TeX bodies"; with Claude 4.x they're dead weight. Maintainer cost (every body edit risks a stale summary) outweighs the negligible token savings.
- **Two-file skeleton split** (package-shipped base + user-info `cv_skeleton.local.tex` overrides): rejected as premature. Solves a customization problem nobody has reported. Revisit if/when a user actually wants to tune per-slot prompt guidance.

## Consequences

- **`compile_cv_cmd.py` rewritten.** Reads `<app_dir>/cv.tex`, parses slot-map, substitutes into `cv_template.tex`, writes result to `<app_dir>/.build/cv.tex`, runs `pdflatex cv` three times. The `\def\UserDataDir{...}\def\BUILD{...}\input{cv_template}` command-line injection is replaced by an explicit `\def\UserDataDir{<posix-path>}\def\BUILD{<name>}` prepended to the substituted file. **The Windows backslash bug (`\def\UserDataDir{D:\Applcication-Pipeline\...}` interpreted as TeX control sequences) is fixed by emitting `as_posix()` paths in the `\def`.**
- **`src/application_pipeline/latex/cv_template.tex` restructured** per the slot list and the user-info refactor. The `<<RESUME_BODY>>` slot splits into three (`<<RESUME_BERUFSERFAHRUNG>>`, `<<RESUME_AUSBILDUNG>>`, `<<RESUME_PROJEKTE>>`) per issue #453; `<<LANGUAGES_BLOCK>>` / `<<HOBBIES_BLOCK>>` deleted in favor of `\Languages` / `\Hobbies`.
- **`src/application_pipeline/templates/skills/cv_skeleton.tex` added** (new package resource). `init` seeds it to `<settings-dir>/skills/cv_skeleton.tex`; `init --refresh` overwrites it.
- **`src/application_pipeline/templates/user-info/{identity,contact}.tex` merge into `facts.tex`.** Adds `Languages` and `Hobbies` `\def`s.
- **`/write-cv` skill rewritten** to read `<settings-dir>/skills/cv_skeleton.tex`, inject it into the prompt as the output-format example, and emit `<app_dir>/cv.tex` matching that shape.
- **`/iterate-cv` skill** adapts to the new shape: reads/edits `<app_dir>/cv.tex` slot bodies rather than a free-form `.tex` document.
- **content_pool.tex item headers rewritten** during the templates refresh. The user's existing `content_pool.tex` is seed-if-missing, so existing installs keep their current headers until they manually re-init; the new shape ships in the package template.
- **ADR-0011 amended:** `init --refresh` overwrites `setup/*.sh` **and** `<settings-dir>/skills/cv_skeleton.tex`. Everything else still seed-if-missing.
- **Settings-dir layout gains `skills/` subdirectory** under the existing tree (ADR-0011's canonical layout).
- **Migration:** no automatic translation for existing `<app_dir>` folders. Re-run `/write-cv` to regenerate `cv.tex` in the new shape. Existing `user-info/identity.tex` + `contact.tex` need a one-time manual merge into `facts.tex` (`init` does not overwrite user-info per ADR-0011).
