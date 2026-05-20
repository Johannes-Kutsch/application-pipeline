# LaTeX CV + Cover Letter Template

Single committed LaTeX source (`cv_template.tex`) that compiles into a cover letter, a resume, or both, with all per-applicant data externalised to a `user-info/` directory outside the repo.

## File layout

- `cv_template.tex` — the only template; do not fork it per application.
- `moderncv.cls`, `moderncvstylecasual.sty`, `moderncvcolorblue.sty`, `tweaklist.sty` — vendored vanilla moderncv v1.2.0 (the only style/color files the template uses).
- Real per-applicant data lives in `../user-info/` (seeded by `application_pipeline init`) and is not committed with the template.

## Compiling

Standalone (uses `../user-info/`):

```
pdflatex cv_template.tex
```

Three named outputs via the `\BUILD` flag:

```
pdflatex -jobname=cover_letter "\def\BUILD{cover}\input{cv_template}"
pdflatex -jobname=resume       "\def\BUILD{resume}\input{cv_template}"
pdflatex -jobname=combined     "\def\BUILD{combined}\input{cv_template}"
```

Valid `\BUILD` values: `cover` (1 page), `resume` (2 pages), `combined` (3 pages — default).

## Pointing at real data: `\UserDataDir`

`\UserDataDir` defaults to `../user-info`. A build orchestrator (the future `/write-cv` skill) overrides it on the command line:

```
pdflatex "\def\UserDataDir{/abs/path/to/user-info}\def\BUILD{combined}\input{cv_template}"
```

The template reads `\UserDataDir/identity.tex`, `\UserDataDir/contact.tex`, `\UserDataDir/content_pool.tex`, `\UserDataDir/profile.png`, `\UserDataDir/signature.png` — nothing else is read from that directory.

## Placeholder convention

Every per-Position value is a literal `<<TOKEN>>` string. The orchestrator fills tokens by string replacement before invoking pdflatex.

Identity slots (in `../user-info/identity.tex` + `contact.tex`):
`<<FIRST_NAME>>`, `<<LAST_NAME>>`, `<<ADDRESS_STREET>>`, `<<ADDRESS_CITY>>`, `<<PHONE>>`, `<<EMAIL>>`, `<<GITHUB_URL>>`, `<<LINKEDIN_URL>>`.

Cover-letter slots (in `cv_template.tex`):
`<<RECIPIENT_LINE_1>>`, `<<RECIPIENT_LINE_2>>`, `<<OPENING>>`, `<<COVER_INTRO>>`, `<<COVER_PIVOT>>`, `<<COVER_FIT>>`, `<<COVER_CLOSING>>`.

Resume slots (in `cv_template.tex`):
`<<RESUME_BODY>>`, `<<SKILLS_BLOCK>>`, `<<LANGUAGES_BLOCK>>`, `<<HOBBIES_BLOCK>>`.

Image slots (file-based, not text):
`profile.png`, `signature.png` in `\UserDataDir`.

## Cover-letter contract (hardcoded 4 paragraphs)

The template enforces the canonical four-paragraph shape from `~/application-pipeline/data/user-info/writing-style.md`:

1. `<<COVER_INTRO>>` — opening hook: which position, why now.
2. `<<COVER_PIVOT>>` — applicant's working style and prior experience that transfers.
3. `<<COVER_FIT>>` — why this specific role/organisation.
4. `<<COVER_CLOSING>>` — invitation to a conversation.

`\opening{<<OPENING>>}` varies per Position (warm "Hallo liebes [X]-Team," vs formal "Sehr geehrte Damen und Herren,"). `\closing{Mit freundlichen Grüßen,}` is hardcoded.

The signature image is injected automatically by an override of `\makeletterclosing` — no manual print/sign/scan needed.

## Content pool format

`content_pool.tex` holds named `\newcommand` macros — one per career item — that the `/write-cv` skill selects from per Position.

Each item starts with a metadata header in LaTeX comments:

```latex
%%% ITEM: bachelor_thesis
%%% section: ausbildung
%%% tags: [always, awarded, academic]
%%% relevance: mle=high, games=medium
%%% summary: Bachelorarbeit Monte-Carlo am Spiel 2048, CBC-Förderpreis
\newcommand{\itemBachelorThesis}{%
  \subcventry{Bachelorarbeit}{Konzeption ...}{Entwicklung ...}%
}
```

Fields:

- `section` ∈ {`berufserfahrung`, `ausbildung`, `projects`} — which resume section the item belongs to.
- `tags` — free comma-separated labels. The label `always` is reserved for items that must appear in every application (Bachelor thesis, awarded projects, ...).
- `relevance` — comma-separated `jobtype=high|medium|low` pairs the orchestrator scores against.
- `summary` — one-line gist so the orchestrator can reason about an item without parsing the macro body.

The orchestrator writes `<<RESUME_BODY>>` as a sequence of `\section{...}` headers followed by `\itemFoo \itemBar` invocations in the chosen order. Section ordering between Berufserfahrung / Ausbildung / Projects varies per Position by importance.

## moderncv mini-DSL (cheatsheet)

Three macros cover virtually all content:

```latex
% Dated row with sub-bullets — used for jobs, degrees, projects.
\cventry{<dates>}{<role/title>}{<employer/school>}{<location>}{<grade>}{%
  \begin{itemize}\item ...\end{itemize}%
}

% Indented sub-row beneath a \cventry — used for thesis details under a degree.
\subcventry{<label>}{<title>}{<description>}

% Label/value row — used for skills, contact lines, single-fact entries.
\cvitem{<label>}{<value>}
```

The template's preamble removes the trailing dot moderncv appends to `\cventry`
(via `\xpatchcmd`) and tightens itemize spacing (`\setlist`), so the visual
style matches the historical Overleaf documents.

## Tier-1 cleanup (this issue)

Compared to upstream moderncv, the following files were **omitted** because nothing in `cv_template.tex` references them: `moderncvstylebanking.sty`, `moderncvstyleclassic.sty`, `moderncvstyleempty.sty`, `moderncvstyleoldstyle.sty`, `moderncvcolor{black,green,grey,orange,purple,red}.sty`, `moderncvcompatibility.sty`. A Tier-3 follow-up will refactor `moderncv.cls` + `moderncvstylecasual.sty` for clarity.
