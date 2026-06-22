# Usage

## Installation

Requires Python >= 3.11 and a working `cron` daemon. The production LLM Extractor uses the Agent
Runtime backend (`ruhken-agent-runtime`), which is installed automatically with the package. It is
separate from the optional Agent Skills templates seeded by `init`. For background see
ADR-0053 (backend decision) and ADR-0054 (runtime logging).

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

Agent Skill package templates live in `src/application_pipeline/templates/agent-skills/`. `init`
materialises that canonical source into byte-identical `.claude/skills/` and `.codex/skills/`
runtime files with tool-local `_shared/` support docs.

```bash
application-pipeline init --refresh
```

### `run`

Execute one full pipeline tick and write today's Daily Results File.

```bash
application-pipeline run
```

### `compile-cv`

Compile a per-listing `cv.tex` CV Slot-Map into application-specific PDFs named
`cover_<application-folder>.pdf`, `resume_<application-folder>.pdf`, and
`combined_<application-folder>.pdf`.

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
|       |-- cover-patterns.md
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

### `user-info/cv/cover-patterns.md`

Reusable cover-letter paragraph patterns for `/write-cv`. Each pattern captures the slot, argument
type, trigger conditions, placeholders, and the approved paragraph text. Use this library for
repeatable paragraphs; ad-hoc style signals are handled inline during drafting.

### `user-info/cv/facts.tex`

Listing-invariant LaTeX facts and presentation macros such as name, city, personal info, languages,
and hobbies. Used by the CV template during `compile-cv`.

### `user-info/cv/content_pool.tex`

Reusable CV item macros for resume sections. `/write-cv` selects these into the resume slots by
section and listing relevance.

### `cv-template/cv_skeleton.tex`

Package-owned format-by-example for CV Slot-Maps. `init --refresh` may overwrite it, so put personal
content in `user-info/cv/` instead.
