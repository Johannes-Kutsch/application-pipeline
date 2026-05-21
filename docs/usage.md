# Usage

## Installation

Requires Python ≥ 3.11, a working `cron` daemon, and the Claude CLI on `$PATH`.

```bash
python3 -m venv .venv
.venv/bin/pip install application-pipeline
```

All subsequent commands use the binary installed inside the venv:

```bash
.venv/bin/application-pipeline <subcommand>
```

## CLI reference

### `init`

Seed a settings folder with the default config, layout, user-info files, LaTeX template, and cron
helper scripts. Safe to re-run — existing files are never overwritten.

```bash
application-pipeline init <settings-dir>
```

Pass `--refresh` to also seed any files introduced in a newer release without touching files you
have already edited. The seeded `setup/cron.sh` calls `init --refresh` on every cron tick so new
template files self-heal after a `pip install --upgrade`.

```bash
application-pipeline init --refresh <settings-dir>
```

### `run`

Execute one full pipeline tick against the given config file and write today's Daily Results File.
This is what `setup/cron.sh` calls.

```bash
application-pipeline run <settings-dir>/config.py
```

## Settings folder layout

After `init`, the settings folder contains:

```
<settings-dir>/
├── config.py          # search knobs — keywords, sources, locations, skills
├── layout.py          # card template for the Daily Results File
├── user-info/
│   ├── triage-profile/
│   │   ├── self-description.md   # your background, fed into both classifier and judge
│   │   ├── domain-fit.md         # what counts as in-domain for the classifier
│   │   ├── match-criteria.md     # what makes a good match for the judge
│   │   └── writing-style.md      # authoring style (future use — not injected in v1)
│   ├── contact.tex           # LaTeX contact block
│   ├── content_pool.tex      # LaTeX CV content
│   └── identity.tex          # LaTeX identity block
├── latex/
│   └── cv_template.tex       # LaTeX CV template
├── setup/
│   ├── cron.sh               # the script cron calls each tick
│   ├── cron-install.sh       # writes the crontab line
│   └── cron-uninstall.sh     # removes the crontab line
└── results/                  # Daily Results Files written here (created on first run)
```

## Per-file editing guide

### `config.py`

Plain Python literals — edit in any text editor. Key knobs:

| Setting | Purpose |
|---|---|
| `KEYWORDS` | Search terms sent to every Source (Cartesian product). |
| `SKILLS` | Hard skills surfaced in the Match Judge prompt. Not used by the pre-filter. |
| `NEGATIVE_KEYWORDS` | Title substrings (≥ 3 chars) that cause the Domain Pre-Filter to drop a listing before any LLM call. |
| `SOURCES` | List of `SourceEntry(parser_type=...)` selecting which job boards to query. |
| `LOCATIONS` | City names to search. Must be supported by the configured parsers. |
| `INCLUDE_REMOTE` | Set `True` to also query each source's remote/homeoffice slot. |
| `MAX_LISTING_AGE_DAYS` | Freshness Gate threshold in days (≥ 1, default 180). Listings older than this are dropped. |

### `layout.py`

Controls the visual structure of each Card in the Daily Results File.

- **`PLACEHOLDER_GROUPS`** — maps a group name to a separator and a list of field names. The group
  collapses its fields with the separator, omitting `None` values, and the result replaces the group
  name as a placeholder in `CARD_TEMPLATE`.
- **`CARD_TEMPLATE`** — a `str.format_map`-style template. Available placeholders: `{company}`,
  `{title}`, `{location_segment}`, `{posted_date}`, `{contract_type}`, `{employment_type}`,
  `{salary}`, `{summary}`, `{matched_bullets}`, `{missing_bullets}`, `{raw_description}`, `{rank}`,
  `{url}`.

### `user-info/triage-profile/self-description.md`

Free-text description of your background, experience, and target roles. Injected into both the
Relevance Classifier prompt and the Match Judge prompt via the `{USER_INFO}` slot. Keep it
concise — bullets and fragments are preferred over full sentences.

### `user-info/triage-profile/domain-fit.md`

Defines the in-domain boundary for the Relevance Classifier. List the role families that should be
classified `in_domain: true` and those that should be `in_domain: false`. The classifier does not
see your match criteria or skills — only this file and `self-description.md`.

### `user-info/triage-profile/match-criteria.md`

Defines what makes a position a strong, partial, or poor match for the Match Judge. Include
location preferences, seniority level, contract preferences, and any hard disqualifiers. The judge
uses this alongside `self-description.md` and the `SKILLS` list from `config.py`.

### `user-info/triage-profile/writing-style.md`

Authoring style notes for future CV/cover-letter generation (v2). Not injected into any v1 prompt —
edit freely without affecting pipeline output.

### `user-info/*.tex` and `latex/cv_template.tex`

LaTeX content fragments and the CV template. Used by the future authoring pipeline (v2). Edit to
match your actual CV content and preferred formatting.
