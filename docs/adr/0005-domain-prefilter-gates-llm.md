# Domain Pre-Filter gates the LLM classify call

> **Superseded by [ADR-0026](0026-prefilter-pure-title-only-blacklist.md)**: the whitelist mechanism (`SKILLS` + `INCLUSION_KEYWORDS`) and body-text matching have been removed. The Pre-Filter is now a pure title-only blacklist. The volume-reduction goal and "LLM stays the authority on in-domain" framing below carry forward; the rest is historical.


A deterministic **Domain Pre-Filter** module runs before any LLM call. It examines each **Position**'s `title + raw_description` against two configured keyword lists — `Config.inclusion_keywords` (whitelist) and `Config.negative_keywords` (blacklist) — using case-insensitive substring match after a shared `normalize()` pass (whitespace collapse + `casefold`). **Whitelist hits override blacklist hits.** Any **Position** that survives (no blacklist hit, *or* any whitelist hit regardless of blacklist) is forwarded to `LLMExtractor.classify_relevance` for the holistic in-domain decision. The Pre-Filter never decides in-domain alone; it only drops obvious slop.

## Why

- **An LLM call on a Pi is not a "cheap discard."** A `classify_relevance` call on Pi 5 takes several seconds to tens of seconds wall time even with the lighter models (`qwen3:0.6b` / `qwen3:4b`). PRD #20 originally framed `classify_relevance` as the cheap discard; on Pi hardware that framing is wrong. The actual cheap discard is regex.
- **Volume reduction is the bottleneck.** Bundesagentur + stellen.hamburg can produce hundreds of new listings per day across the configured **Keywords**. Running an LLM call on every one would blow past any reasonable cron window. A deterministic pre-filter cuts the LLM-classify volume to listings the cheap rules genuinely cannot decide.
- **Whitelist-wins precedence preserves cross-field opportunities.** A listing matching a blacklist term in the company name (e.g. `"Pflegeheim AG"`) but mentioning `"Python"` in the description should reach the LLM, not be dropped. Anchoring the whitelist to the applicant's `Config.skills` plus a broader `Config.inclusion_keywords` (role names, tech families) keeps the rescue net wide.
- **The LLM stays the authority on in-domain.** Pre-Filter is a volume reducer, never a decision-maker. Anything that passes goes to the LLM. False positives on the whitelist cost an LLM call (recoverable); false drops on the blacklist are the only loss-of-information path, and they're bounded to listings that match a blacklist term *and* mention nothing in the applicant's skill stack.
- **Language detection is a separate concern handled by the orchestrator.** `application_pipeline/language.py` runs `langdetect` on each enriched `Position` before pre-filtering; the orchestrator calls it and passes the result downstream. The Pre-Filter receives only the position text and has no language awareness.

## Considered alternatives

- **No pre-filter; let the LLM judge every listing.** Rejected: throughput exceeds reasonable cron windows on Pi 5 (several seconds to tens of seconds per call × hundreds of listings).
- **Two-tier: confident-in (skip to `judge_match`) + confident-out (drop) + ambiguous (LLM classify).** Rejected: skip-to-judge based on keyword alone risks losing cross-field opportunities. Pre-Filter only drops; never bypasses the LLM gate.
- **Embedding-based gate (BGE-M3 or `paraphrase-multilingual-MiniLM-L12-v2`).** Deferred to v1.1: cleaner relevance signal, but adds a runtime dependency and a threshold-tuning loop. Substring keyword matching handles obvious-slop cases at zero dependency cost; the embedding upgrade is a clean future swap behind the same module surface.
- **Fuzzy matching (Levenshtein-1).** Rejected: short keywords (`Go`, `R`, `C`, `Java`) explode false positives in German prose; German inflection (Pflege/Pflegekraft/Pflegerin) is better handled by stem-style keyword authoring (`Pfleg` instead of `Pflege`) which the substring matcher handles deterministically.
- **Tighten parser-level queries (negative terms in the search itself).** Adopted as a complementary measure, not a substitute. Parser-level filtering reduces input volume but cannot examine descriptions; the Pre-Filter still runs because some boards do not support exclusion terms in queries and because description-level signals matter.

## Consequences

- The pipeline gains a new module: `application_pipeline/prefilter/`. PRD #20 declares it a precondition for `LLMExtractor.classify_relevance`.
- `Config` gains `inclusion_keywords: list[str]` and `negative_keywords: list[str]`; both validated at load time (each entry a non-empty string of length ≥ 3).
- `Config.skills` is reused as part of the whitelist (concatenated with `inclusion_keywords` for matching); the user's hard-skill inventory IS the broadest natural whitelist.
- `RelevanceVerdict.language` is removed. `Position.language` is resolved by `application_pipeline/language.py`, invoked by the orchestrator immediately after enrichment and before pre-filtering. `"unknown"` sentinel when confidence is too low; downstream `OllamaExtractor` falls back to the English prompt file in that case.
- `LLMExtractor.classify_relevance` is now the **survivor classifier**, not the every-listing gate. Its absolute call volume drops materially; its per-call importance rises (every call is a genuine ambiguity).
- A shared text helper at `application_pipeline/text/normalize.py` is used by Pre-Filter and (after migration) Dedup Store. The helper uses `casefold` (not `lower`), so `Straße` and `Strasse` collide as the same key — desired for German city/company names.
- The Pre-Filter is single-pass, stateless, deterministic — trivially testable without a model.
