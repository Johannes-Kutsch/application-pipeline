# Domain Pre-Filter becomes a pure title-only blacklist

Single rule: drop a **Position** if any **Negative Keyword** matches its title (case-insensitive substring, after `normalize()`). Whitelist removed entirely — no rescue via `SKILLS` or `INCLUSION_KEYWORDS`, no body-text matching. `INCLUSION_KEYWORDS` is retired from the **Config** schema; legacy values in user configs are silently ignored with one INFO log line. `SKILLS` keeps its remaining role (judge prompt's `{skills}` slot) and stops being a prefilter input.

## Why

- **The whitelist became redundant.** Every parser queries with `Config.KEYWORDS`, so every listing reaching the Pre-Filter has already passed an upstream keyword filter. The whitelist was filtering an already-filtered stream.
- **Empirical confirmation.** Over 1800 Pre-Filter decisions: `whitelist_only=576 (32%)` + `whitelist_rescue=372 (21%)` = the whitelist fired on 53% of decisions, mostly on incidental tech-stack mentions in body text (e.g. "Learning Experience Designer" rescued because the body contained `git`).
- **Body-scope matching was the proximate cause.** Both lists matched against `title + raw_description`. Body matches generated most false rescues and would also drive most false drops. Title is high-signal.
- **Pre-Filter should not redo the judge's job.** Title-blacklist for unambiguous off-domain ("Sales Manager", "Werkstudent"), nothing else. Body text is the judge's job.
- **`SKILLS` stops being dual-consumer.** Dual role meant any prefilter-side pruning of generic skills (`python`, `git`) weakened the judge's view of the applicant. Removing the prefilter consumer eliminates the coupling.
- **`INCLUSION_KEYWORDS` becomes dead config.** Loader silently ignores unknown attributes with one INFO line per run (`config has unused field 'INCLUSION_KEYWORDS' — safe to remove, see ADR-0019`). Hard-erroring on a harmlessly-extra field blocks the run for no operator benefit.
- **False-negative risk is explicit and accepted.** A "Senior Data Engineer" body that says "we don't take juniors" no longer drops via the body match — only a title like "Junior Engineer" does. Smaller collateral surface than naive whitelist-removal.

## Considered alternatives

- **Tighten the whitelist (drop generic single-word tokens like `git`, `python`).** Rejected: requires a prefilter-only opt-out because `SKILLS` is also a judge consumer.
- **Restrict whitelist matching to title only, keep rescue.** Rejected: papers over the symptom; the rescue mechanism is solving a problem that no longer exists.
- **Remove the Pre-Filter entirely.** Rejected: title-level off-domain listings should not pay classify tokens.
- **Hard-error on legacy `INCLUSION_KEYWORDS`.** Rejected: blocking the run on a stale field after a package upgrade is hostile; silent-ignore + INFO respects the "log unparseable inputs at INFO" stance.

## Consequences

- **Pre-Filter module simplifies.** Single rule: `for kw in NEGATIVE_KEYWORDS: if kw.casefold() in normalize(title).casefold(): drop`. Per-position transcript `reason` collapses to `{passed, blacklist_drop}`. `whitelist_matches` and `body_len` are removed; `blacklist_matches` and `title_len` remain.
- **`Config` loses `INCLUSION_KEYWORDS`.** Removed from the typed dataclass.
- **`SKILLS` becomes single-consumer** — judge prompt's `{skills}` slot only.
- **Expected pass-rate drop**: ~26% → ~47% (estimated from the 1800-decision sample). One-time visible cost step-down on first run after deploy.
- **Future firehose parsers.** A parser that cannot apply `KEYWORDS` on the wire must apply it client-side as a title-substring filter before yielding stubs from `discover()`. The Pre-Filter assumes every yielded stub matches its `ParserQuery.keyword`. Parser-local invariant.
