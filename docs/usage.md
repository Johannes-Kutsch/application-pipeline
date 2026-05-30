# Usage

## Installation

Requires Python >= 3.11, a working `cron` daemon, and the Claude CLI on `$PATH`.

```bash
python3 -m venv .venv
.venv/bin/pip install application-pipeline
```

All subsequent commands use the binary installed inside the venv:

```bash
.venv/bin/application-pipeline <subcommand>
```

## CLI Reference

### `init`

Seed `<cwd>/application-pipeline/` with the default config, user-info files, CV skeleton, agent
skills, and cron helper scripts. Safe to re-run: existing user-owned files are not overwritten.

```bash
application-pipeline init
```

Pass `--refresh` to update package-owned files such as setup scripts, CV skeleton, and agent skill
bodies after an upgrade. User-owned files under `user-info/` are still preserved.

```bash
application-pipeline init --refresh
```

### `run`

Execute one full pipeline tick and write today's Daily Results File.

```bash
application-pipeline run
```

### `compile-cv`

Compile a per-listing `cv.tex` CV Slot-Map into `cover.pdf`, `resume.pdf`, and `combined.pdf`.

```bash
application-pipeline compile-cv application-pipeline/applications/<folder>/
```

## Settings Folder Layout

After `init`, `<cwd>/application-pipeline/` contains:

```text
application-pipeline/
|-- config.py
|-- cv-template/
|   `-- cv_skeleton.tex
|-- setup/
|   |-- cron.sh
|   |-- cron-install.sh
|   `-- cron-uninstall.sh
|-- user-info/
|   |-- search-terms/
|   |   |-- keywords.md
|   |   `-- negative-keywords.md
|   |-- triage-profile/
|   |   |-- gate-criteria.md
|   |   |-- candidate-profile.md
|   |   `-- skills.md
|   `-- cv/
|       |-- facts.tex
|       |-- content_pool.tex
|       |-- writing-style.md
|       |-- positive-exemplars.md
|       |-- profile.png
|       `-- signature.png
|-- results/
`-- applications/
```

`.runtime-data/` is created at runtime for logs, state, extracts, and failure reports.

## Per-File Editing Guide

### `config.py`

Plain Python literals for pipeline mechanics. Search terms and skills do not live here.

| Setting | Purpose |
|---|---|
| `SOURCES` | List of `SourceEntry(parser_type=...)` selecting which job boards to query. |
| `LOCATIONS` | City names to search. Must be supported by the configured parsers. |
| `INCLUDE_REMOTE` | Set `True` to also query remote/homeoffice slots where parsers support them. |
| `MAX_LISTING_AGE_DAYS` | Freshness Gate threshold in days. Listings older than this are dropped. |
| `CLAUDE_CLASSIFY_PARALLELISM` | Relevance Classifier worker pool size. |
| `CLAUDE_CLASSIFY_BATCH_SIZE` | Listings per classifier LLM call, if present in your config. |
| `DEDUP_COOLDOWN_DAYS` | Days before selected/expired seen entries stop suppressing duplicates. |

### `user-info/search-terms/keywords.md`

Flat `-` bullets for search queries sent to the configured sources. This file must exist and contain
at least one entry.

### `user-info/search-terms/negative-keywords.md`

Optional flat `-` bullets for title-only exclusions. If any entry matches a discovered title
case-insensitively, the Domain Pre-Filter drops the listing before LLM classification.

### `user-info/triage-profile/gate-criteria.md`

Classifier-only criteria: broad domain-in/domain-out signals and hard exclusions. Keep this file
concise and gate-shaped. Preference and ranking nuance belongs in `candidate-profile.md`.

### `user-info/triage-profile/candidate-profile.md`

Judge-facing applicant profile: who the candidate is, what roles are attractive, and which soft
signals should rank one matched listing above another.

### `user-info/triage-profile/skills.md`

Hard-skill pool. The Match Judge receives the flat skill list; `/write-cv` reads the grouped
structure to assemble the `skills_block` slot.

### `user-info/cv/writing-style.md`

Cover-letter voice, phrasing rules, and cover strategy. Use short declarative bullets:

- `## Voice` for high-level tone.
- `## Do` for preferred phrasing and behavior.
- `## Don't` for anti-rules, not concrete bad examples.
- `## Register` for `Sie`/`Du`, greeting, and audience conventions.
- `## Cover-Strategie` for content arc and slot strategy, such as hook placement, pivot choice, and
  project-evidence discipline.

Do not store exemplars here. If a bad draft sentence reveals a reusable rule, abstract it into a
Do/Don't or `Cover-Strategie` bullet and discard the concrete sentence.

### `user-info/cv/positive-exemplars.md`

Positive style models only, usually snippets from handwritten letters. Do not add negative examples
or "do not write like this" samples here or in `writing-style.md`.

### `user-info/cv/facts.tex`

Listing-invariant LaTeX facts and presentation macros such as name, city, personal info, languages,
and hobbies. Used by the CV template during `compile-cv`.

### `user-info/cv/content_pool.tex`

Reusable CV item macros for resume sections. `/write-cv` selects these into the resume slots by
section and listing relevance.

### `cv-template/cv_skeleton.tex`

Package-owned format-by-example for CV Slot-Maps. `init --refresh` may overwrite it, so put personal
content in `user-info/cv/` instead.
