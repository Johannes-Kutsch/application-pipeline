# Classifier becomes a three-check gatekeeper; `domain-fit.md` merges into `match-criteria.md`

The **Relevance Classifier** stops being a domain-only filter. Its verdict field is renamed `in_domain` → `matches`, and the prompt enumerates three sequential checks the LLM must pass before emitting a Header + Summary:

1. Is the role in the candidate's domain?
2. Can the candidate realistically apply (skill / experience floor)?
3. Does the role fit the candidate's preferences (role type, seniority, hard no-gos)?

Any "no" short-circuits to `<verdict>{"matches": false}</verdict>` and the candidate never enters the **Pool**.

In the same change, the **Triage Profile** collapses from three files (`self-description.md` + `domain-fit.md` + `match-criteria.md`) to two: `self-description.md` + `match-criteria.md`. The retired `domain-fit.md`'s content (in-scope / out-of-scope domains) merges into `match-criteria.md`. The per-call-site split (ADR-0016: classifier saw `self-description + domain-fit`; judge saw `self-description + match-criteria`) retires — **both call sites now see the same merged USER_INFO**, emitted as two named sub-blocks (`# Kandidatenprofil` + `# Match-Kriterien`) instead of a single `<user-info>` wrapper.

## Why

- **Tighter classifier bar implements one of issue #524's directions.** At ~500-item Pool intake, the Judge cannot do all the per-item fit work in a single end-of-run call. Moving the fit check to the per-item classifier (Haiku, parallel) shrinks Pool size at the source and reduces the Judge's job to ranking among already-vetted matches.
- **The classifier needed match-criteria content to do its job.** Before this ADR, the classifier could not drop a listing for being "pure management" or "consulting" because that knowledge lived in `match-criteria.md`, which only the judge saw. The three-check verdict requires the classifier to see preference content; the file merge follows from that.
- **`domain-fit.md` and `match-criteria.md` were the same content type in two files.** Both are statements of "what makes a role a fit"; one was domain whitelist/blacklist, the other was preference filters. The split was driven by ADR-0016's per-call-site routing, which retires here. With both call sites sharing content, two files become one.
- **`matches` is the honest name.** `in_domain` describes the old single check. With three sequential checks, the field tracks "does this role pass all three" — a match verdict.
- **Prompt-structure cleanup rides along.** The listing block moves above the instruction block (context-first ordering). Header rules become positional ("if the value is given under 'Zu klassifizierende Stellenanzeige'") rather than provenance-based ("vom Parser vorausgefüllt"); the latter referenced an undefined pipeline-implementation concept the LLM doesn't need to know. The verdict JSON example fixes to single-line valid JSON. A worked `<header-example>` block demonstrates the segment-drop rule.
- **Pre-fill bullets render only when populated.** Empty values for `{COMPANY}` / `{LOCATION}` / `{POSTED_DATE}` drop their bullet entirely (line-level, `value is None or value.strip() == ""`) — three empty bullets would have signalled "considered and found absent" rather than "no signal".

## Considered alternatives

- **Keep the per-call-site file split; just inject `match-criteria.md` into the classifier too.** Rejected: the file split was an artefact of the routing rule, not a content boundary. Once both call sites read both files, the split is friction without a payoff.
- **Inject `skills.md` into the classifier as well.** Rejected (grilled #535): skills are an enumerated list, and explicit enumeration primes the LLM toward strict subset matching — the exact failure mode the retired `matched` / `missing` open-vocab lists had (per ADR-0041). Q2's skill-floor check is judgment, not enumeration; the narrative profile carries the right primitive.
- **Two abort buckets ("out of domain" / "no fit") instead of three sequential checks.** Rejected during grilling: the asymmetry between Q2 (skill floor — *can* the candidate apply) and Q3 (preferences — *would* the candidate apply) is real, even when downstream consequence is identical. Three named checks document the LLM's reasoning path; two would collapse it.
- **Add a third verdict value `matches: "borderline"` for amber cases.** Rejected: amber lives in match-criteria narrative ("consulting: amber"), and the Pool / Judge pipeline expects binary verdicts. A tri-state would require new dedup statuses and a third routing branch.
- **Keep `in_domain` as the field name to avoid the rename churn.** Rejected: the field is the dominant identifier in `RelevanceVerdictV2`, `.transcripts.jsonl` payloads, and the classifier prompt itself. Carrying a stale name through three call sites is more confusing than a single migration.
- **Backwards-compat shim that reads either field name.** Rejected: matches ADR-0024 wipe-state precedent. A shim's only job is to slow down the deprecation of a field nobody parses outside the package.

## Consequences

- **`RelevanceVerdictV2.in_domain` renames to `matches`.** Validator in `__post_init__` and the JSON parsing in `ClaudeExtractor.classify_relevance` switch to the new key. The legacy `{"in_domain": ...}` shape now fails with `ExtractorMalformedJSONError` — hard cutover, no translation.
- **`user-info/triage-profile/` ships two files** post-`init`: `self-description.md` + `match-criteria.md`. `init --refresh` does not touch user-authored content (per ADR-0011 / ADR-0032 unchanged). Hard-cutover migration: prompts.py raises `PromptError` if the legacy `domain-fit.md` is still present, with a message naming the merge target — the operator merges manually.
- **`prompts.py` emits two named sub-blocks** instead of `<user-info>...</user-info>`. The classifier and judge templates inject the same combined block. The per-call-site concatenation logic (`classify_user_info` vs `judge_user_info`) retires.
- **The classifier prompt is restructured**: candidate profile → listing block → instructions (three-check classification, then summary). `<classification-rules>`, `<header-rules>`, `<header-example>`, `<summary-rules>` blocks are explicit. The verdict JSON example is single-line valid JSON.
- **Pre-fill bullet rendering** drops empty lines at the `ClassifyItem` rendering boundary inside the **LLM Enricher**'s call path. Title always present.
- **CONTEXT.md updates**: **Triage Profile** (two files; both call sites see both), **Relevance Classifier** (three-check verdict; `matches` field; sees match-criteria), **Match Judge** (same USER_INFO content as classifier), **LLM Extractor** (`RelevanceVerdict` shape uses `matches`). The `in_domain` *dedup status* name is unchanged — only the verdict field is renamed.
- **Test updates**: `tests/test_prompt_loader.py` (rendered prompt contains both sub-block headers; legacy `domain-fit.md` raises `PromptError`); `tests/test_claude_extractor_v2.py` (new verdict shape parses; legacy `in_domain` shape fails); `tests/test_llm_enricher.py` + `tests/test_orchestrator.py` (`matches: true` writes `in_domain` dedup status + extract; `matches: false` writes `out_of_domain`).
- **Tracking**: implementation lives in issue #535.

## Amendment (post-issue #535): render-responsibility moves into templates

The "both call sites see the same merged `{USER_INFO}` block with `# Kandidatenprofil` + `# Match-Kriterien` headers" rule above describes the *content* invariant only. The loader (`prompts.py`) no longer renders headers — it exposes three named profile slots that each prompt template places (with its own heading level) explicitly:

- `{SELF_DESCRIPTION}` — body of `self-description.md`.
- `{MATCH_CRITERIA}` — body of `match-criteria.md`.
- `{SKILLS}` — `\n`-joined `- {skill}` bullets from `SearchTerms.skills` (the former judge-only `{skills}` slot, lifted to a shared profile slot under the Caps naming convention).

Per call-site validation: data slots stay required (`{LISTING_BULLETS}` + `{RAW_DESCRIPTION}` for classify; `{CANDIDATES}` for judge), the three profile slots are *allowed* in either template in any subset. A future need to put `{SKILLS}` into the classifier (which ADR-0019 currently forbids) is a deliberate prompt edit, not a loader change. The judge keeps its existing `## Kandidatenprofil` / `## Kompetenzprofil` / `## Match-Kriterien` H2 structure; the classifier uses flat H1.

Consequences:

- `load_prompts(config)` becomes `load_prompts(config, search_terms)` so the loader can substitute `{SKILLS}` at load time.
- `ClaudeExtractor` no longer needs `SearchTerms` (the skills block is baked into the judge template on load); the kwarg is dropped.
- The judge's old lowercase `{skills}` and `{candidates}` placeholders rename to `{SKILLS}` and `{CANDIDATES}` (Caps convention applies to all v2 placeholders).

## Supersedes / amends

- **Supersedes ADR-0016** in the USER_INFO file-routing area: the per-call-site `self-description + domain-fit` vs `self-description + match-criteria` split retires. ADR-0016's hardcoded-protocol / externalised-content split and the single-language pipeline decision remain in force.
- **Amends ADR-0032**: the four-file `triage-profile/` layout (`self-description.md` + `domain-fit.md` + `match-criteria.md` + `writing-style.md`) becomes three files (`self-description.md` + `match-criteria.md` + `writing-style.md`). `writing-style.md` is unaffected (CV-authoring-only, not v1-injected).
- **Amends ADR-0041** in documentation only: the prompt shape it described changes; the `RelevanceVerdict` contract (`{in_domain, header, summary}` → `{matches, header, summary}`) retains the same operational semantics under a renamed field.
