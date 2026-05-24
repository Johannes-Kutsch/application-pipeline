# Search terms live in user-info/, not on Config

`KEYWORDS`, `SKILLS`, `NEGATIVE_KEYWORDS` moved out of `config.py` into markdown files in `<settings-dir>/user-info/search-terms/` (split into three files per ADR-0024). Loaded as a separate `SearchTerms` object. Missing/empty Keywords raises `SearchTermsError`; Skills and Negative Keywords optional.

## Why

- These are user-authored knobs — same person who writes `self-description.md`. Different downstream consumers from pipeline-shape Config.
- `config.py` should be for pipeline mechanics (sources, locations, parallelism).

## Consequences

- `SearchTerms` object threaded into orchestrator, **Domain Pre-Filter**, and `ClaudeExtractor` alongside `Config`.
- Hard cutover: existing installs error until files exist. No auto-migration.
