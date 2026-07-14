# Search terms live in user-info/, not on Config

`KEYWORDS`, `NEGATIVE_KEYWORDS` moved from `config.py` into markdown files under `<settings-dir>/user-info/search-terms/` (ADR-0018 splits into per-section files). `SearchTerms` object loaded separately. Missing/empty keywords → `SearchTermsError`.

## Why

- User-authored knobs, different downstream consumers from pipeline-shape Config.

## Consequences

- `SearchTerms` threaded into orchestrator and **Domain Pre-Filter**.
- Hard cutover: existing installs error until files exist.
