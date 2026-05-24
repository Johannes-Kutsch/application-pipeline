# Domain Pre-Filter becomes a pure title-only blacklist

Single rule: drop a **Position** if any **Negative Keyword** matches its title (case-insensitive substring, after `normalize()`). Whitelist removed — no rescue via `SKILLS` or `INCLUSION_KEYWORDS`, no body-text matching. `INCLUSION_KEYWORDS` retired from Config; legacy values silently ignored with INFO log. `SKILLS` stops being a prefilter input (ADR-0025 — judge prompt only).

## Why

- Whitelist was redundant — every listing reaching the Pre-Filter already passed an upstream keyword filter from `Config.KEYWORDS`.
- Body-scope matching caused false rescues (53% of decisions involved the whitelist, mostly incidental tech-stack body mentions like "Learning Experience Designer" rescued because body contained `git`).
- Pre-Filter should not redo the judge's job. Title-blacklist for unambiguous off-domain only.
- `SKILLS` stops being dual-consumer — removing prefilter consumer eliminates coupling.

## Consequences

- Single rule: `for kw in NEGATIVE_KEYWORDS: if kw.casefold() in normalize(title).casefold(): drop`. Transcript reason collapses to `{passed, blacklist_drop}`.
- `SKILLS` becomes single-consumer — judge prompt's `{SKILLS}` slot only.
- Future firehose parsers must apply `KEYWORDS` client-side before yielding stubs.
