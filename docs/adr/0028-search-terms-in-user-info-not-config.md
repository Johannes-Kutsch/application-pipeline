# Search terms live in user-info/, not on Config

`KEYWORDS`, `SKILLS`, and `NEGATIVE_KEYWORDS` move out of `config.py` into a single markdown file in `<settings-dir>/user-info/`, loaded as a separate `SearchTerms` object — not as fields on `Config`. Three `##` sections (Keywords / Skills / Negative Keywords), flat bullet lists. No length validation on negative keywords (lifted). Missing or empty Skills / Negative Keywords sections are valid; empty Keywords raises `UserSettingsError`.

The split mirrors the editing surface: these are user-authored knobs (the same person who writes `self-description.md` writes these), not pipeline mechanics. They share `user-info/` with the **Triage Profile** but are a distinct concept — different downstream consumers (`KEYWORDS` → parser orchestration, `NEGATIVE_KEYWORDS` → **Domain Pre-Filter**, `SKILLS` → judge's `{skills}` slot; none of these are LLM-prompt content the way the Triage Profile is).

## Considered alternatives

- **Keep on `Config`, just relocate the values** — rejected: `config.py` is for pipeline-shape knobs (sources, locations, batch sizes). Mixing the bulleted human-authored term lists into Python literals there made `config.py` the awkward middle of "code the user edits".
- **Three separate files** (`keywords.md`, `skills.md`, `negative-keywords.md`) — rejected: fragments the editing surface; one file keeps the three short lists co-located.
- **Auto-migrate from legacy `config.py` keys on first run** — rejected: one-shot migration code that lives forever after a single operator's one-time copy-paste. Hard cutover matches ADR-0024's precedent.

## Consequences

- New `SearchTerms` object loaded via a dedicated loader; threaded into orchestrator, **Domain Pre-Filter**, and `ClaudeExtractor` construction alongside `Config`. `Config` no longer carries these three fields.
- Existing installs error on first run after upgrade until the new file exists. User manually copies the three lists across.
- `user-info/` now holds two distinct concepts (Triage Profile + SearchTerms); CONTEXT.md must keep them named separately.
