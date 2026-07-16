# Application Pipeline

Personal job-discovery and triage pipeline. Fetches listings from a small set of sources, classifies relevance with Claude, accumulates an in-domain **Pool** across days, emits one dated **Daily Results File** per day carrying the **Daily Top-5** **Cards** ranked by the **Match Judge**.

## Scope

- **In scope (v1):** Working **Parsers** for **Bundesagentur**, **stellen.hamburg**, **jobs-beim-staat**, smoke-tested standalone on the laptop.
- **In scope (v1.1):** Full pipeline on Pi — orchestrator, **LLM Enricher** (ADR-0024 — body fetch + strip + **Relevance Classifier** producing **Header** + **Summary**), **Match Judge** (one call per run, picks **Daily Top-5** from the **Pool**), **Deduplication**, daily file, cron once per day, Syncthing sync.
- **Out of scope:** **Profile** ingestion, additional commercial parsers, browser automation. (CV / cover-letter LaTeX compilation in scope via `application-pipeline compile-cv` — see Invocation.)

## Language

### Pipeline artifacts

**Config**: `config.py` at `<cwd>/application-pipeline/` (ADR-0016) controlling pipeline shape — `SOURCES`, `LOCATIONS`, `INCLUDE_REMOTE`, `MAX_LISTING_AGE_DAYS: int` (default 180, `≥ 1`) driving the **Freshness Gate**, `CLASSIFY_PARALLELISM: int` (default 4, `≥ 1`) sizing the classify worker pool (ADR-0023), `CLASSIFY_BATCH_SIZE: int` (default 10, `≥ 1`) — listings per classify call (ADR-0023), `DEDUP_COOLDOWN_DAYS: int` (default 30, `≥ 1`) gating decay of `selected_by_judge` and `expired` entries (ADR-0033). LLM service, model, and tool policy pinned behind the **LLM Extractor**, not operator config (ADR-0038); `CLAUDE_CLI_PATH`, `CLAUDE_CLASSIFY_PARALLELISM`, and `CLAUDE_CLASSIFY_BATCH_SIZE` retired and rejected at load time. No path override knobs (ADR-0003 amended). `layout.py` retired (ADR-0025). `KEYWORDS`/`NEGATIVE_KEYWORDS` live in **SearchTerms** (ADR-0015). Materialised by `init`; never overwritten by `init --refresh`. Loaded by **Config Loader** into frozen typed `Config`. `max_results` retired (ADR-0031). Cron weekdays 00:30. _Avoid_: config file, settings file, search config.

**Operator Credential**: Local secret needed by a pinned external service, stored only in `<settings-dir>/.env` as `OPENCODE_GO_API_KEY`, kept out of **Config**, and required at startup before parser work begins. _Avoid_: API config, global env, home env.

**Init Bootstrap**: Deep implementation behind `application-pipeline init [--refresh]` that materialises package template resources into the local workspace while preserving operator-owned artefacts. Caller-facing interface stays `init(cwd, refresh=False)` / CLI only. Internally owns seed policy, bucket routing, package-owned versus operator-owned classification, planned seed and cleanup actions, retired refresh paths, legacy `application-pipeline/skills/` cleanup, and normal/refresh console reporting. Package-owned artefacts include setup scripts, CV Skeleton, tool-root-inlined agent skill bodies, tool-root `SKILL.md` files, and package-templated tool-root `_shared` docs. Operator-owned artefacts include Config, Operator Credential `.env`, SearchTerms, Triage Profile, CV user-info files, `.runtime-data/`, user-added Agent Skill dirs, legacy `application-pipeline/agent-skills/`, and unknown files inside package-owned skill or `_shared` dirs. Tests cross the Init Bootstrap interface with temporary filesystem state and real package resources; avoid helper-level seams, template storage adapters, and filesystem-write ports.

**SearchTerms**: User-authored search/filter knobs (ADR-0015) — `KEYWORDS`, `NEGATIVE_KEYWORDS`. Two markdown files under `<settings-dir>/user-info/search-terms/` (ADR-0018): `keywords.md`, `negative-keywords.md`. Filename *is* the section. Flat `-` bullet entries. `keywords.md` missing/empty → `SearchTermsError`; `negative-keywords.md` optional. `KEYWORDS` → parser orchestration, `NEGATIVE_KEYWORDS` → **Domain Pre-Filter**. `skills.md` relocated to **Triage Profile** dir (#615). _Avoid_: search config, term file.

**Layout**: _Retired_ per ADR-0025. Card structure hardcoded in **DailyResultsFile**. `init --refresh` deletes `layout.py`. _Avoid_: do not reintroduce.

**Daily Results File**: One dated markdown file at `<settings-dir>/results/YYYY-MM-DD.md`, holding **Daily Top-5** as **Cards** in **Rank** order. Date is cron-anchored (ADR-0010/0011). No preamble, no Run Divider. Write-once; synced read-only. _Avoid_: results file (without "daily"), `current.md`.

**Failure Report**: Markdown at `<settings-dir>/.runtime-data/failures/<timestamp>.md` (path ADR-0027, ADR-0040). Trigger: parser-dead events only — a dead parser writes a report and the run continues. Contains full exception traceback (including chained exceptions); no log-tail. Fatal pipeline errors (LLM Extractor failures, **Match Judge** failure, unexpected exceptions) surface as unhandled exceptions to stderr instead (ADR-0040). The `application_pipeline.failure_report` module owns timestamp generation, package-tag discovery, markdown rendering, and atomic write. Acknowledged by deletion. Quota errors do NOT trigger — they sleep (ADR-0011). Per-listing soft failures (oversized/malformed) stash to sibling dirs without `seen.json` write. _Avoid_: incident report, error log, failure-report adapter.

**Malformed Classify Stash**: Per-listing soft-failure markdown under `<settings-dir>/.runtime-data/failures/malformed/` for **Relevance Classifier** output that cannot be applied. Records listing identity, error classification, Agent Runtime Log pointers (per-call evidence directory), and raw model output when useful; does not duplicate full prompts or **Raw Description** bodies. _Avoid_: failure report, transcript.

**Position**: A single job listing surviving **Relevance Classifier** and **Match Judge** top-5 selection. Card shape fixed (ADR-0025): `# **{rank}:** {Header}` + `{Summary}`. _Avoid_: job, listing, vacancy.

**Position Schema**: _Retired_ per ADR-0024. Closed-enum fields live inside the LLM-authored **Header**. `PositionStub` and `EnrichResult` survive (ADR-0028). "Parsers never guess" invariant retires — LLM Enricher infers from body. _Avoid_: do not reintroduce per-field extraction.

**Raw Description**: Full body text, fetched and stripped by parser's `enrich()`. Fed to LLM and persisted in **Card Store** at classify time. Rendered into **Daily Results File** after **Summary**, fenced by `---`. Empty body dropped by **Content Gate** (ADR-0022). Oversized stashed to `.runtime-data/failures/oversized/`, no `seen.json` mark. _Avoid_: description (when full text is meant).

**Structured Extract**: _Retired_ per ADR-0024. Replaced by **Header** + **Summary** + **Raw Description**. `extracts.json` is `{listing_id: {header, summary, body}}`, keyed by **Listing ID** integer. _Avoid_: do not reintroduce.

**Card Store**: In-process module (`application_pipeline.extracts.card_store`) owning `extracts.json` interpretation and persistence. Caller-facing seam is `load_card_store(path)` returning a store with `get`, `put`, `replace_body_if_present`, and `delete` keyed by **Listing ID**. Accepts current records with **Header** + **Summary** + optional **Raw Description** body, rejects legacy URL-keyed data, applies retired v1 wipe policy at load, validates malformed Card records with `ExtractStoreError`, and persists integer **Listing ID** string keys unchanged through the **Atomic Write Helper**. _Avoid_: extract store, card-store adapter, filesystem port.

**Header**: Three-line block authored by the **LLM Enricher** at classify time (ADR-0024) — title, `company · location · work_model`, `posted_date · seniority · salary`. LLM substitutes known values, infers from body, or drops segments. Persisted in `extracts.json`. _Avoid_: card top, headline.

**Summary**: Prose paragraph authored at classify time (ADR-0024), describing the role in the **Triage Profile**'s frame. Persisted alongside **Header**. **Match Judge** ranks on Header + Summary directly. _Avoid_: match verdict summary (retired), description (overloaded).

### Filtering & scoring

**Triage Profile**: Pipeline-facing applicant data at `<settings-dir>/user-info/triage-profile/` (ADR-0018, ADR-0034) as three files: `gate-criteria.md` (domain-in/out + hard exclusions — classifier only), `candidate-profile.md` (who-the-candidate-is + ranking preferences — judge only), `skills.md` (relocated from `search-terms/`, judge `{SKILLS}` slot, #615). `application_pipeline.triage_profile` owns the local filesystem artefact contract for prompt-slot loading. Exposes `{GATE_CRITERIA}`, `{CANDIDATE_PROFILE}`, and `{SKILLS}`. The **Prompt Loader** consumes those values and owns prompt template loading, slot validation, brace escaping, and final `PromptTemplate` construction. Classifier consumes `{GATE_CRITERIA}` only; judge consumes `{CANDIDATE_PROFILE}` + `{SKILLS}`. **TriageSkills** supplies the attribute-stripped judge projection for `{SKILLS}` while preserving the grouped projection for CV authoring. Bullets/keywords, German, concise (ADR-0013). Cover-writing style and paragraph-pattern decisions handled in `/write-cv`. _Avoid_: profile (unqualified), bio, CV Profile (retired), `domain-fit.md` (retired), `self-description.md` (retired), `match-criteria.md` (retired).

**TriageSkills**: Domain module owning the `skills.md` **Triage Profile** artefact. Parses once and exposes two projections: judge-facing flat **Skill** bullet text with trailing `{...}` attributes stripped, and grouped **Skill Group** data with attributes preserved for CV authoring. Missing `skills.md` → empty judge-facing view; flat legacy files valid for judge, degenerate to no **Skill Groups**. _Avoid_: prompt-private skills parser, separate skills parser.

### CV authoring

**CV Slot-Map**: Per-listing `<app_dir>/cv.tex` by `/write-cv`, consumed by `compile-cv` (ADR-0017). `^%% SLOT: <name>$` markers, raw TeX bodies. Thirteen slots: `recipient_company/name/street/zip_city`, `opening`, `cover_subject`, `cover_intro/closing/bullets`, `resume_berufserfahrung/ausbildung/projekte`, `skills_block` (mechanically assembled from **Skill Group** pool — ADR-0019). Listing-invariant content in **Facts**. _Avoid_: cv document (`cv_template.tex` is the document).

**CV Slot Contract**: In-process module `application_pipeline.cv_slot_contract` owning canonical CV Slot-Map vocabulary and behavior-relevant projections. Exposes the thirteen slots, two Cover Paragraph Pattern slots (`cover_intro`, `cover_closing`), and marker forms such as uppercase `<<SLOT_NAME>>`. Not a persisted artefact, not an adapter/port. _Avoid_: duplicated private slot constants, parser-private slot vocabulary, external seam.

**CV Skeleton**: Structure-only slot map at `<settings-dir>/cv-template/cv_skeleton.tex` (ADR-0017, relocated ADR-0026). Package-shipped, refreshable. `/write-cv` reads it as canonical slot list and order, not as content guidance. _Avoid_: cv template (overloaded with `cv_template.tex`).

**CV Template Contract**: `cv_template.tex` must compile and preserve the CV Slot Contract's declared template markers. Visual layout intentionally not part of the contract. _Avoid_: layout snapshot, pixel-perfect rendering.

**Compile CV Workflow**: Deep implementation behind `application-pipeline compile-cv <app_dir>` that owns config preflight, CV Slot-Map parsing, package LaTeX staging into `<app_dir>/.build/`, slot substitution, **Project Order Optimization** probe loop, cover/resume/combined build modes, two-pass execution, pdflatex error extraction, PDF publishing, success cleanup, and failure build-dir retention. Caller-facing interface remains `compile_cv(app_dir)` / CLI only. _Avoid_: compile-cv script, PDF writer, exposing build order to callers.

**Project Order Optimizer**: Internal pure Python module that generates all permutations of the `resume_projekte` macro-call list in ascending Kendall tau distance from the authored order, never yielding the original. Used by **Compile CV Workflow** to find the closest reordering that fits the resume within 2 pages. No file I/O or pdflatex dependency. _Avoid_: height estimator, heuristic reorder.

**Project Order Optimization**: Compile-time phase inside **Compile CV Workflow** that probes resume page count with a single pdflatex pass. Skipped when the baseline already fits in 2 pages or only one project entry is present. When triggered, iterates **Project Order Optimizer** permutations until page count ≤ 2, then prints a reorder notice; prints a warning and falls back to the original order if exhausted. The source `cv.tex` is never modified. _Avoid_: heuristic height ordering, layout optimizer.

**Pdflatex Adapter**: Internal compile-cv port for external `pdflatex`. Production preserves `pdflatex` command shape, `.build/` cwd, `capture_output`, and `TEXINPUTS=".<pathsep>"` vendored-moderncv isolation; exposes `page_count` parsed from the pdflatex `.log` "Output written on" line; tests use a fake adapter. _Avoid_: subprocess seam, public compile option, TeX engine config.

**Cover Strategy**: Default writing arc: opener (`cover_intro`) and closer (`cover_closing`) are read verbatim from **Cover Paragraph Patterns**; middle section is a **Bullet Library** selection (`cover_bullets`) — exactly 4 neutrally-phrased, data-backed facts finalized collaboratively by `/analyse-listing`. `/write-cv` reads the finalized 4 from session context. _Avoid_: fixed cover template, project list.

**Cover Subject**: Listing-specific subject line rendered bold above the opening greeting via moderncv's `\title{}`. Format: `Betreff: Ihre Stellenanzeige <Jobtitel>` with optional ` Refnr. <Nummer>` appended when the listing contains a reference number. Stored in `cover_subject` slot; filled automatically by `/write-cv` without an approval loop. _Avoid_: hardcoded subject, static Betreff.

**Cover Spacing**: Cover letter uses one proportional gap rule for both opening-to-intro and closing-to-sign-off transitions. Both gaps track the selected cover stretch level. _Avoid_: hardcoded gap size, asymmetric cover spacing.

**Pull-Fit**: Cover-letter argument that a listing is specifically attractive to the candidate, with capability fit implied through the same facts. Now realised via the **Bullet Library** (`cover_bullets` slot) rather than prose paragraphs. _Avoid_: suitability pitch, generic motivation.

**Bullet Library**: Candidate-maintained file at `<settings-dir>/user-info/cv/bullet-library.md` containing fact-backed capability entries. Each entry has a name and one sentence of neutral, data-backed text. `/analyse-listing` extracts listing requirements, matches them against the library, and finalizes exactly 4 entries collaboratively with the user; new entries are drafted when gaps are identified against `candidate-profile.md`. `/write-cv` reads the finalized 4 from session context and renders them as a `\begin{itemize}` block in the `cover_bullets` slot. _Avoid_: cover paragraph pattern (different format and purpose), skills block (different rendering surface), tag-based matching.

**Cover Paragraph Pattern**: Reusable cover-letter paragraph stored in `cover-patterns.md` under `# Intro Patterns` or `# Closing Patterns`. Format: heading + text only (no metadata). Used for `cover_intro` and `cover_closing`; not used for `cover_bullets`. _Avoid_: generic style exemplar, free-prose inspiration.

**Cover Paragraph Pattern Library**: In-process `application_pipeline.cover_patterns` interface for loading `cover-patterns.md` into a library value and projecting pattern views. Owns metadata, slot, placeholder, one-paragraph, and sentence-count validation; raises `CoverPatternError` at the module seam. Missing/empty artifacts → empty library. _Avoid_: raw pattern list, markdown parser seam, cover-patterns adapter.

**Cover Placeholder**: Controlled placeholder filled only from clear `/analyse-listing` signals or candidate/project context. Allowed: `Musterfirma`, `Musterteam`, `Musterrolle`, `Musterprodukt`, `Musterdomäne`, `Mustertechnologie`, `Musterprojekt`; `Musterprojekt` = candidate's evidence, `Musterprodukt` = employer's product. Skill/adjective/work-style placeholders avoided. _Avoid_: free skill slot, inferred trait.

**Interactive Cover Drafting**: `/write-cv` flow matching each cover paragraph against **Cover Paragraph Patterns** for candidate confirmation. `cover-patterns.md` grows only when candidate enters main drafting loop and approves a new pattern. Cover-shortening affects only `cv.tex`. _Avoid_: one-shot cover generation, post-build pattern learning.

**Interactive Cover Shortening**: Post-build loop for overlong cover PDFs — LLM proposes shortened full-paragraph variant per cover slot, candidate chooses one, only `cv.tex` changes. _Avoid_: fixed strip-down order for cover prose.

**Facts**: Listing-invariant data at `<settings-dir>/user-info/cv/facts.tex` (ADR-0017, ADR-0018, ADR-0021). Defines `\myFirstname`, `\myFamilyname`, `\myCity` plus `\PersonalInfo`, `\Languages`, `\Hobbies` presentation macros. `cv_template.tex` `\input`s via `\CvDataDir/facts`. _Avoid_: identity, contact (retired filenames); per-field raw defs retired (folded into `\PersonalInfo`).

**Content Pool**: CV item macro artefact at `<settings-dir>/user-info/cv/content_pool.tex` (ADR-0018), owned by the in-process `application_pipeline.content_pool` module. Each item is a `\newcommand{\itemFoo}{...}` selected into `resume_berufserfahrung`, `resume_ausbildung`, or `resume_projekte` through section-based **Content Pool Candidate** projections. Per-item metadata: `always:`, `group:`, `relevance:`. Sections from `% ===== <name> =====` block headers. The module preserves authored order, validates relevance levels and ambiguous artifact states, represents mandatory items and grouped alternatives explicitly, and renders or validates macro-call bodies for resume slots. `/write-cv` keeps listing-specific judgment; deterministic artifact behavior stays in this module. _Avoid_: raw content-pool parser, item pool, CV pool, content-pool adapter.

**Content Pool Candidate**: Selection-ready projection of one **Content Pool** item for a resume slot: item name, macro call, resume slot, section, authored order, `always` flag, `group` membership, and validated `relevance` metadata. Candidates expose deterministic artifact facts only; they do not decide whether an optional item is a good fit for a listing. _Avoid_: selected item, generated resume prose, raw metadata row.

**Skill**: Hard-skill item from `skills.md` in **Triage Profile** dir (#615). Dual-consumed through **TriageSkills** (ADR-0019): **Match Judge** receives flat attribute-stripped text, `/write-cv` receives full **Skill Group** structure. Judge-only — **Domain Pre-Filter** no longer reads it (ADR-0008). Optional `{always}` attribute. _Avoid_: keyword (when matching).

**Skill Group**: H2 heading in `skills.md` (ADR-0019) — also `\cvitem{<group>}{...}` label. Unit of LLM selection for `/write-cv`: always-groups render; others picked by relevance. File order = render order. _Avoid_: skill category, section (overloaded).

**Agent Skills**: Tool-consumed agent workflows seeded by `init` (ADR-0026, ADR-0036). Workflow bodies and support docs generated from one package-owned source and materialised inline into `.claude/skills/<workflow>/SKILL.md`, `.codex/skills/<workflow>/SKILL.md`, and tool-local `<tool-root>/_shared/` docs. Normal init seeds missing files; refresh overwrites package-owned files and preserves user-added dirs and legacy `application-pipeline/agent-skills/`. `application-pipeline/skills/` remains retired legacy path. _Avoid_: subagents, prompts (overloaded), LLM agents.

**Keyword**: Search term from `SearchTerms.KEYWORDS` (ADR-0015). Distinct from **Skill** and **Negative Keyword**. _Avoid_: skill (when querying).

**Negative Keyword**: Entry in `SearchTerms.NEGATIVE_KEYWORDS` (ADR-0015). Case-insensitive substring match in title only (ADR-0008). _Avoid_: blacklist, exclusion.

**Listing ID**: Auto-increment integer, primary key in `seen.json` and `extracts.json`. Assigned at first `is_seen` miss. URLs stored as `urls: [...]` list (most-recent-first) with reverse index for URL-tier dedup. `PositionStub` does not carry the ID — dedup layer assigns it; `RunScopedSeenResult` carries it downstream. `mark_*` methods take `listing_id: int`. _Avoid_: URL key, url id.

**Match Verdict**: Judge output per winner — `{id, rank: 1..5}` where `id` is the **Listing ID** integer (ADR-0024). Judge ranks only; Card's Summary is from classify-time. _Avoid_: score, rating, tier (retired).

**Rank**: Integer 1..5 assigned by the judge. Not a score, not a tier. _Avoid_: tier, score, position (overloaded).

**Pool**: In-process module (`application_pipeline.pool`) owning the run-scoped set of rediscovered/current-run `matched` listings keyed by **Listing ID**. Parser Intake admits `judge_pending` listings; Classify Stage admits newly classified `matched` listings. Projects **Match Judge** candidates from **Card Store** records (**Listing ID** + **Header** + **Summary**; missing **Cards** skipped), applies **Match Verdicts** by committing **Cards** to **Daily Results File** before transitioning **Deduplication Store** to `selected_by_judge`. **Orchestrator** keeps run assembly, **Pool** ownership, **Classify Stage** startup, **Match Judge** invocation, quota sleep, summaries; **Parser Lifecycle** owns parser execution coordination. _Avoid_: queue, candidate set - not the **Daily Top-5**.

**Daily Top-5**: ≤5 **Positions** the **Match Judge** returns from today's **Pool**. Card append+fsync happens before Deduplication Store `mark_selected_by_judge`. _Avoid_: top-N, shortlist.

**Classify Stage**: Internal orchestration module owning classify queue protocol, classify-ready submissions from **Parser Intake**, ADR-0023 batch accumulation, classify worker dispatch, **Quota Wall** retry, **Applied Classify Outcome** routing, **Run Metrics** classify counters, **Run Log** classify rows, and **Pool** admission for matched outcomes. Calls the **LLM Enricher** through the applied classify interface. _Avoid_: classifier (LLM call only), LLM Enricher, classify queue (when the owning module is meant), Match Judge.

**Relevance Classifier**: Single-check LLM gatekeeper (#615): domain fit + hard exclusions from `{GATE_CRITERIA}`. No candidate profile, no skill-floor check — stretch/experience judgment deferred to **Match Judge**. Emits **Header** + **Summary** on pass. Port: `classify_relevance(items) -> list[RelevanceVerdict | None]`; output `<verdict id="N">{...}</verdict>` per item. Up to `CLASSIFY_BATCH_SIZE` listings per call (ADR-0023). **Classify Stage** owns ADR-0023 batch accumulation plus dispatch to parallel worker pool (ADR-0023); **LLM Enricher** owns classifier side effects. Unparseable verdicts become retryable: listing unmarked, re-discovered next run. Combined prompt shape preserved through **Agent Runtime** (ADR-0038). _Avoid_: filter, gate.

**LLM Enricher**: Owns classify LLM call and applied classify outcome semantics. Called with `(listing_id, stub, body)`, invokes **LLM Extractor**'s `classify_relevance`, stashes malformed output, runs post-LLM **Freshness Gate**, writes Card on match, applies **Deduplication Store** transitions. Returns applied classify outcome with matched listings for **Pool** admission, metrics, and event data. Backend/provider failures abort the run. No httpx client, no body strip, no quota sleeping. _Avoid_: enricher (unqualified), extractor.

**Applied Classify Outcome**: Structured result after raw `RelevanceVerdict | None` values interpreted and side effects applied. Item states: matched, rejected/out-of-domain, retryable unparseable, post-LLM stale/expired. Carries matched `(Listing ID, PositionStub)` for **Pool** admission. _Avoid_: verdict (when side effects applied), classify result (ambiguous).

**Freshness Gate**: Drops temporally invalid candidates (ADR-0012, ADR-0024, ADR-0028). `admit(stub, *, gate_arm, deadline=None) -> bool` at three sites: post-discover, post-enrich, post-LLM. Drops when `posted_date` exceeds `MAX_LISTING_AGE_DAYS` or `deadline < anchored_today`. Writes `expired`; on `matched → expired` deletes extract. Parser-thread drops summed into one `freshness` counter (ADR-0032). _Avoid_: staleness filter, expiry gate.

**Content Gate**: Drops empty or placeholder body post-enrich (ADR-0022, ADR-0028). Minimum 100 chars. No `seen.json` mark — retried next run. `admit(stripped_body, stub) -> bool`. Reason enum `{passed, empty_body, too_short}`. Effective customer: non-native-enrich parsers (ADR-0030). _Avoid_: empty-body filter.

**Domain Pre-Filter**: Title-only blacklist (ADR-0008). Substring match on **Negative Keywords**, case-insensitive. `admit(stub) -> bool`. Drops write `out_of_domain`. Log component `pipeline_prefilter`. _Avoid_: filter, gate, classifier.

**Gates Bundle**: _Retired as single call site_ per ADR-0028. Non-LLM gates invoked individually by parser thread. Pre-enrich: Freshness → Dedup → Pre-Filter. Post-enrich: Freshness → Content Gate. _Avoid_: gate runner, filter chain.

**EnrichResult**: From `Parser.enrich(stub)` (ADR-0028). Carries updated `stub`, `body: str`, `mode: Literal["native", "fallback"]`. Native-enrich always `"native"`; `"fallback"` = scrape-is-primary (ADR-0030). On failure, `EnrichFailedError` — stub skipped, no `seen.json` write (ADR-0029). _Avoid_: enriched stub, enrich payload.

**Quota Wall**: Shared coordination for parallel classify pool (ADR-0023). Raised from **Agent Runtime** usage-limit outcomes carrying `reset_time`, then workers sleep and retry. `raise_wall(reset_time)`, `wait_if_blocked()`, `is_active()`. `threading.Condition` + `reset_time`. One `event=quota_sleep` row per wall raise. _Avoid_: rate limiter, barrier.

**Match Judge**: Picks **Daily Top-5** from **Pool**. Single `judge_top_n(candidates)` per run. Takes `list[JudgeCandidate]` (**Listing ID** + Header + Summary), returns ≤5 `{id, rank}`. Consumes `{CANDIDATE_PROFILE}` + `{SKILLS}` (#615). On non-quota error → fatal exception surfaces to stderr; no daily file. _Avoid_: scorer.

### Deduplication and run state

**Deduplication**: Four-tier (ADR-0033): in-run `run_hit` plus persistent **Deduplication Store** with URL-tier, exact-tuple-tier `(company_lc, title_lc, location_lc)`, and fuzzy-tuple-tier (token-subset, min 4 tokens, shorter ⊂ longer, gender markers stripped). Tuple/fuzzy fire only when all three fields non-`None`. Tuple/fuzzy hits append new URL to record's `urls` list. `is_seen` writes in-memory `pending` on miss, assigns **Listing ID** (populates indexes immediately, no persist). Checked at two points: post-discover and post-enrich. `is_seen` returns `RunScopedSeenResult` carrying `listing_id: int`: `url_hit`/`tuple_hit`/`fuzzy_hit` skip; `judge_pending` routes to Pool (first in run updates URL/title); `run_hit` skips within-run repeats; `miss` processes. Freshness-dropped listings: **Parser Intake** calls `mark_expired(stub)` to populate indexes.

**Deduplication Store Match Decision**: Private in-process policy inside the **Deduplication Store** mapping match fact + record status + cooldown age + run-scope claim state to one `RunScopedSeenResult` and one mutation plan. Not a port/adapter, not exposed to callers. _Avoid_: dedup policy adapter, external decision service, new seam.

**Dedup status enum** (ADR-0009/ADR-0034/ADR-0033):
- `out_of_domain` — Pre-Filter or Classifier rejection. Terminal-skip.
- `matched` — classifier `matches: true`, extract written. In the **Pool**. Tuple/fuzzy hit returns `judge_pending` (first in run) or `run_hit`.
- `selected_by_judge` — judge picked, Card appended+fsynced. Extract deleted. Suppresses within `DEDUP_COOLDOWN_DAYS` (default 30); after cooldown decays to `judge_pending`.
- `expired` — Freshness Gate drop or `matched → expired`. Deletes extract on transition. Suppresses within `DEDUP_COOLDOWN_DAYS`; after cooldown decays to `miss`. `status_last_changed` refreshed on each re-drop.
- `pending` — in-memory only, not persisted. Claimed by first `is_seen` miss; overwritten by classify worker's `mark_*`.
- ~~`enrich_failed`~~ — _retired_ (ADR-0029). URLs stay unrecorded, retried next run.
- ~~`external_redirect`~~ — _retired_ (ADR-0024). Redirects followed silently.

**Error semantics for `mark_*`** (single-writer Pi):
- **Pre-filter drop** → `mark_out_of_domain(stub)`. Does not reach the **LLM Extractor**.
- **Malformed classifier output** → listing NOT marked; retry next tick. Stashed to `failures/malformed/`.
- **LLM Extractor backend/provider invocation failure** → run aborts; exception surfaces to stderr. Listings not marked.
- **Classifier usage-limit outcome** → **Quota Wall** raises; workers sleep until reset+2min.
- **Judge non-quota error** → exception surfaces to stderr; no daily file, Pool intact.
- **Body fetch failure** → stub skipped, URL unrecorded (ADR-0029). Native-enrich: 401/403/5xx/3xx/JSON decode → **Failure Report** + parser dead + stderr notification; 404/400/422/retries-exhausted → silent skip.
- **Oversized/malformed LLM output** → stashed, no `seen.json` write, retried next run.

State at `<settings-dir>/.runtime-data/seen.json` (ADR-0027; synced via Syncthing, ADR-0001). Shape: `{listing_id: {urls: [...], company_lc, title_lc, location_lc, status, status_last_changed}}`. `urls` most-recent-first; no alias records. On-load migration from legacy URL-keyed format. `DeduplicationStore` exposes four methods taking `listing_id: int`: `mark_out_of_domain`, `mark_matched`, `mark_selected_by_judge`, `mark_expired`. Single-writer (ADR-0001); internal lock covers concurrent writes. Config: `DEDUP_COOLDOWN_DAYS: int = 30` (ADR-0033). _Avoid_: duplicate filtering, URL filtering.

### Display

**Card**: Fixed markdown block per winner (ADR-0025): `# **{rank}:** {title}` (blank line) metadata lines + URL (blank line) `{Summary}` (blank line) `---` (blank line) `{Raw Description}` (blank line) `---`. _Avoid_: card template, headline.

**DailyResultsFile**: Module owning rendering and durability for the **Daily Results File**. Card shape hardcoded per ADR-0025. Write durability `write + flush + fsync` per ADR-0010. Public interface: `ensure_initialized()` and `commit(*, rank, header, summary, url, body)`. `OSError` wrapped as `ResultsFileError`. _Avoid_: renderer, formatter, results file manager, writer, output manager.

**Atomic Write Helper**: `write_atomic(path, payload: bytes)` — crash-safe via `.tmp` sibling + `os.write` → `os.fsync` → `os.replace`. Used by **Deduplication Store** and **Card Store**. _Avoid_: persistence helper, file writer (overloaded with **DailyResultsFile**'s append).

### Observability

**Log Artifacts**: Under `<settings-dir>/.runtime-data/logs/` (ADR-0027), laid out by reader, layer-subdirs (ADR-0007). Pipeline-owned JSONL: `<layer>/<comp>.events.jsonl` and root `lifecycle.jsonl`; shared `run.log` for tracebacks. Production **LLM Extractor** evidence uses **Agent Runtime Logs**: pipeline-serialized per-call evidence directories under `llm/agent-runtime/classify/` and `llm/agent-runtime/judge/` (ADR-0038, #972). Pipeline-owned LLM transcript JSONL retired. Component identifiers layer-prefixed (`parser_`, `llm_`, `pipeline_`); subdir replaces prefix only on filename. _Avoid_: log file (unqualified).

**Run Log**: Per-run instance writing **Log Artifacts**. Constructed once from `cfg.logs_dir`; threaded into every component. Thread-safe — opens, writes, closes per call. _Avoid_: log writer, logger (overloaded).

**Agent Runtime Logs**: Production **LLM Extractor** prompt/response evidence: best-effort, pipeline-written one `.log` file per **Agent Runtime** call (ADR-0038). Three plain-text sections in order: `[prompt]` (sent prompt, written before the call), `[events]` (one line per `AgentEvent` appended live via `on_live_output`), `[result]` (outcome kind, selected provider, usage — written at completion). `usage` appears only here as raw per-call evidence — never in contract, summaries, or counters (ADR-0038). Classifier files under `llm/agent-runtime/classify/`; judge files under `llm/agent-runtime/judge/`. `AgentRuntimeInvocationResult.evidence_path` carries the file pointer; consumed by **Malformed Classify Stash** and **Failure Report**. Missing evidence is a diagnostic gap, not a failure. Retention: delete file after 30 days. _Avoid_: Claude transcript, ranking-agent log.

**Parser Lifecycle**: Internal parser-thread orchestration module owning parser-thread startup, per-query discovery loops, lifecycle control messages, not-served query accounting, parser-dead **Failure Report** recording, stall watchdog, thread joins, and parser summary emission. Accepts entered **Parser** adapters plus run collaborators from **Orchestrator**; calls **Parser Intake** per discovered **Position Stub**. Does not own **Parser Intake** gates, **Classify Stage** batching, **Pool**, **Match Judge** flow, or Config loading. _Avoid_: parser thread helper, parser queue protocol, lifecycle adapter.

**Maintenance**: Post-run cleanup behind `run_maintenance(logs_dir, failures_dir)`. Owns **Log Artifact** retention: root shared artifacts, ADR-0007 layer subdirs, old flat artifacts, and **Agent Runtime Logs**. Pipeline-owned logs use tail-line rule; **Agent Runtime Logs** deleted whole when older than 30 days. Also deletes old **Failure Report** markdown. _Avoid_: log rotation, cleanup adapter, exposing log layout to callers.

**Status Display**: Live in-process progress view (ADR-0032). `StatusDisplay` Protocol: `RichStatusDisplay` (tty) and `PlainStatusDisplay` (cron). Uniform counters: queued/dropped/forwarded. Each parser gets parser row + gates row (non-zero drops only). `llm_classify_relevance` adds `malformed` + `classifying` counters. Judge: terminal message only. _Avoid_: progress bar, TUI, dashboard.

### Sources & extraction

**Source**: A `SourceEntry` in `Config.SOURCES` carrying a **Parser Type**. Source = config entry; Parser = module. _Avoid_: site, board.

**ParserHttp**: Per-parser HTTP layer owning pacing (0.5s), retry-with-backoff on `{429, 502, 503, 504}` (max 3), status classification, redirect handling, **Log Artifact** rows, and discover/enrich error conversion. True external HTTP sits behind a private internal transport port; production uses the internal `httpx` transport adapter, and tests provide their scripted transport adapter from test helper code at that seam. Parser callers learn only construction, `get`, `enrich_get`, context lifetime, and close behaviour. _Avoid_: http client, fetcher, parser transport adapter (when the parser-facing module is meant), scripted ParserHttp adapter in production code.

**Parser**: Module in `src/application_pipeline/parsers/`. Two methods (ADR-0028): `discover(query) -> Iterable[PositionStub]` and `enrich(stub) -> EnrichResult`. Context manager owning `httpx.Client`; no shared mutable state. Threads do `enrich()` I/O inline (ADR-0002). `has_native_enrich = True` → exclusive native path, no fallback (ADR-0030). `body_selector` parser-private. In-parser dedup retired (ADR-0031). **Keyword-match invariant** (ADR-0008): every stub matches its query keyword in title. _Avoid_: scraper, fetcher.

**Parser Intake**: Internal parser-thread handoff module processing one **Position Stub** after `discover()`. Owns **Freshness Gate** (`discover`), **Deduplication** (post-discover), **Domain Pre-Filter**, `Parser.enrich()`, **Deduplication** (post-enrich), **Freshness Gate** (`post_enrich`), **Content Gate**, counter recording, parser-row **Status Display** updates, skip **Log Artifact** emission, **Pool** admission for `judge_pending` rediscovery, and forwarding to **Classify Stage**. Public interface: one narrow per-stub call. _Avoid_: gate bundle, new port, parser adapter.

**Position Stub**: Result of `discover()` — `url, title, source, posted_date?, deadline?, company?, location?`. Required: url, title, source. Optional fields drive post-discover gates and Header pre-fills. `enrich()` may back-fill. _Avoid_: preview, summary.

**External Redirect**: _Retired_ per ADR-0024. Redirects followed silently in `parsers/body_fetch.py`. _Avoid_: do not reintroduce.

**Parser Type**: String on `SourceEntry` mapping to parser filename. _Avoid_: adapter.

**Location**: Sealed `City(name) | Remote` passed via `ParserQuery.location`. _Avoid_: place, geo.

**City**: `City(name)` arm. Normalized via `normalize()`. No central catalog. _Avoid_: town, place.

**Remote**: `Remote` arm. Per-parser `remote_wire()`. _Avoid_: homeoffice (when the type variant is meant).

**Location Coverage**: Per-parser Protocol — `serves(name)`, `to_wire(name)`, `serves_remote`, `remote_wire()`. Validated at load time (ADR-0004). _Avoid_: location map, slug table.

**LLM Extractor**: Protocol: `classify_relevance(items: list[ClassifyItem]) -> list[RelevanceVerdict | None]` and `judge_top_n(candidates) -> list[MatchVerdict]`; usage/cost telemetry retired (ADR-0038). `usage_limit` remains a control-flow outcome for **Quota Wall** and judge retry. Backend-neutral or Agent Runtime names in current code. `RelevanceVerdict` = `{matches, header?, summary?}`. `MatchVerdict` = `{id: int, rank}`. Production: **Agent Runtime Invocation Port** with a default **Agent Runtime Invocation** adapter delegating to `ruhken-agent-runtime==0.1.2`; the **LLM Extractor** owns prompt construction, Agent Output Protocol parsing, classify/judge outcome mapping, usage-limit control-flow errors, retryable provider-failure handling, hard provider-failure surfacing, and `last_classify_log_path`. Evidence: **Agent Runtime Logs** only. Tags: `<verdict>`/`<verdicts>` via **Agent Output Protocol** (ADR-0005). _Avoid_: LLM, model (unqualified), LLM agents.

**Agent Runtime Invocation Port**: Internal **LLM Extractor** seam for true external **Agent Runtime** calls. Shape: prompt string, `logs_root`, classify/judge call site, optional **Operator Credential** provider auth; returns `completed`, `usage_limit`, `retryable_provider_failure`, or `hard_provider_failure` plus output, evidence directory, optional reset time, and optional provider message. Production uses the **Agent Runtime Invocation** adapter; tests use a mock adapter that records prompt, call site, logs root, and provider auth. _Avoid_: monkeypatching extractor-private runtime imports, exposing provider policy through Config.

**Agent Runtime Invocation**: Production adapter/module behind the **Agent Runtime Invocation Port**. Owns `ruhken-agent-runtime` request construction, provider auth forwarding, pinned service/model/effort/tool policy, RuntimeOutcome-to-invocation-result mapping, pipeline serialization of **Agent Runtime Logs**, and prompt normalization (Unicode `"Zs"` space separators → ASCII space; `"Cf"` format characters → dropped) for Windows stdin safety. `HardAgentError` from the AR boundary propagates as a fatal exception; it is not caught or converted to a result kind (ADR-0038). Does not own prompt construction, Agent Output Protocol parsing, extractor error semantics, quota-wall policy, or classifier/judge outcome mapping. _Avoid_: LLM Extractor behavior, prompt parser, usage telemetry contract.

**Agent Runtime**: Runtime package boundary used only behind the production **Agent Runtime Invocation** adapter. Not a replacement for **Agent Skills**; does not expose service/model/tool policy as operator config. Raw per-call runtime usage may appear only in **Agent Runtime Logs** evidence. _Avoid_: runtime (unqualified), LLM agents.

**Agent Output Protocol**: `extract_json_block(text, tag) -> Any` + `AgentOutputProtocolError` (ADR-0005). Rightmost closing tag, walk back, strip optional fence, `json.loads`. Bare-JSON fallback with `protocol_fallback` log. _Avoid_: output parser, response handler.

**Pagination**: Page fetches until source returns empty. No dedup-driven early-stop (ADR-0006). `max_results` retired (ADR-0031). _Avoid_: paging.

### Invocation

Distributed via PyPI (ADR-0014). Install: `.venv/bin/pip install application-pipeline`, `init`, `bash setup/cron-install.sh`. Home dir hardcoded to `<cwd>/application-pipeline/` (ADR-0016).

**Startup Runner**: Deep implementation behind `application-pipeline run [--no-judge]` and `application-pipeline cron [--no-judge]` that owns cwd-relative settings-dir discovery, missing Config exit-2 guidance, local Operator Credential preflight, cron Init Bootstrap refresh, Run Log and Status Display construction, Orchestrator invocation, completion-summary rendering, and best-effort post-run Maintenance. Caller-facing behavior stays the existing CLI surface plus one in-process startup request for tests. Cron differs from run only by explicit refresh-before-run policy. Startup failures surface naturally to stderr and do not write Failure Reports; parser-dead remains Parser Lifecycle behavior. _Avoid_: startup adapter, startup port, CLI helper, home env.

- `application-pipeline run` reads `<cwd>/application-pipeline/config.py`.
- `application-pipeline init [--refresh]` invokes **Init Bootstrap** to seed into `<cwd>/application-pipeline/` and tool-specific wrapper roots.
- `application-pipeline compile-cv <app_dir>` compiles per-listing draft from slot-map. Windows paths normalized to POSIX for `\def\CvDataDir{...}` (ADR-0018, ADR-0017).

Fail loud-and-fast (exit 2) if `config.py` missing. Cron weekdays 00:30. Each tick: `pip install --upgrade` (×2), `init --refresh`, `run`. **`init --refresh`** overwrites `setup/*.sh`, `cv-template/`, tool-root inlined skill bodies, tool-root `SKILL.md` files, and package-owned tool-root `_shared` docs (ADR-0026, ADR-0036), deletes `layout.py` (ADR-0025) and obsolete `skills/` dir (ADR-0026), and leaves legacy `application-pipeline/agent-skills/` untouched. Seeds-if-missing for `config.py`, `user-info/*` (ADR-0027). `.gitignore` retired from template. Never touches `.runtime-data/`. Templates organised into routing buckets (ADR-0026). No flock — single-writer on the Pi. `pycastle/` in this repo is an unrelated RALPH Loop plugin used to *build* this project.

## Relationships

- A listing reaches a **Daily Results File** by passing (ADR-0028/0033): **Parser Intake** handles Freshness Gate (discover) → Deduplication (post-discover) → Domain Pre-Filter → `Parser.enrich()` → Deduplication (post-enrich) → Freshness Gate (post-enrich) → Content Gate. **Parser Intake** admits rediscovered matched listings to **Pool** or forwards to **Classify Stage**. **Classify Stage** invokes **LLM Enricher** for classify call, malformed stashing, post-LLM Freshness Gate, Card write, Deduplication Store transition, and matched-outcome routing into **Pool** before **Match Judge** picks **Daily Top-5**.
- During run assembly, **Orchestrator** enters **Parser** adapters and prepares collaborators, then crosses **Parser Lifecycle** seam. **Parser Lifecycle** calls **Parser Intake** per stub and hands classify-ready output to **Classify Stage**.
- **Triage Profile** reaches LLM via two disjoint paths (#615): `{GATE_CRITERIA}` → classifier only; `{CANDIDATE_PROFILE}` + `{SKILLS}` → judge only. `NEGATIVE_KEYWORDS` reaches **Domain Pre-Filter** directly.
- **Triage Profile** prompt-slot loading is local-substitutable: tests use temporary filesystem state plus **TriageSkills** module, not mocks or new ports.
