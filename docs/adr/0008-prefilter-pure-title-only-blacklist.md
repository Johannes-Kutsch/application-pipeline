# Domain Pre-Filter: pure title-only blacklist

Drop if any **Negative Keyword** matches title (case-insensitive substring, after `normalize()`). Whitelist removed — no rescue via `SKILLS` or `INCLUSION_KEYWORDS`. `SKILLS` is judge-only (ADR-0019).

## Why

- Whitelist redundant — every listing already passed upstream keyword filter. Body-scope matching caused false rescues (53% of decisions). Pre-Filter should not redo the judge's job.

## Consequences

- Single rule: `for kw in NEGATIVE_KEYWORDS: if kw.casefold() in normalize(title).casefold(): drop`.
- `SKILLS` becomes single-consumer — judge prompt's `{SKILLS}` slot only.
