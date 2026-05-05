# Application Pipeline

A personal job-discovery and triage pipeline. Fetches listings from a small set of sources, classifies relevance with a local LLM, scores fit against the applicant's skill list, and emits a single rolling shortlist as markdown. Application authoring (CV/cover letter generation) is explicitly out of scope for v1.

## Scope

- **In scope (v1):** Working **Parsers** for **Bundesagentur**, **stellen.hamburg**, and **jobs-beim-staat**, each provably returning **Position Stubs** and capable of enriching them. Smoke-tested standalone on the laptop. No LLM, no orchestration, no **Results File** in v1.
- **In scope (v1.1):** Full pipeline running on the Pi — orchestrator, **Relevance Classifier**, **Match Judge**, **Deduplication**, rolling **Results File**, cron, Syncthing sync of the **Results File** to the laptop. Ollama lives only on the Pi.
- **Out of scope:** **CV** / **Cover Letter** generation, LaTeX pipeline, **Profile** ingestion, additional commercial parsers (LinkedIn / StepStone / Indeed / Adzuna), Playwright, browser automation.

## Language

### Pipeline artifacts

**Config**: A `settings/config.py` at the repo root that controls the search — `KEYWORDS`, `SKILLS`, `SOURCES`, `LOCATIONS`, `INCLUDE_REMOTE`, plus optional path overrides for the two prompt files. Plain Python literals, edited in place, with `#` comments. Loaded by the **Config Loader**, which returns a frozen typed `Config` dataclass. `KEYWORDS` is a single global list applied to every **Source** (Cartesian product over keyword × source); **Deduplication** absorbs the overlap. No per-source keyword overrides in v1. Each entry in `SOURCES` is a `SourceEntry` carrying its own `max_results` cap (default 1000) controlling how many newest-first listings that **Source** yields per run. _Avoid_: config file (without qualifier), settings file, search config.

**Results File**: A single rolling `results/current.md` that accumulates **Positions** since the last manual reset. Reset by moving/deleting the file; the next run creates a fresh one. _Avoid_: dated results file, output file.

**Position**: A single job listing surviving the **Relevance Classifier** and **Deduplication**, identified by a unique number within the current **Results File**. _Avoid_: job, listing, vacancy.

**Position Schema**: The dict structure every **Parser** must return — `title, company, location, language, url, source, raw_description` plus optional `salary, contract_type, employment_type, work_model, posted_date, deadline`. `language` (`"de" | "en" | "other"`) is filled by the **Parser** when source metadata exposes it; if the source has no language signal the **Parser** leaves it `None` and the **Relevance Classifier** fills it as part of its call. The **Match Judge** later attaches a **Match Verdict** (`tier, matched, missing, summary`); these are not parser outputs. _Avoid_: output format.

**Raw Description**: The full unmodified text of a **Position** as returned by the **Parser**. Input to the **LLM Extractor**. _Avoid_: description (when full text is meant).

### Filtering & scoring

**Triage Profile**: A short prose description of the applicant — experience, hard skills, role-shape preferences, what they're looking for. Embedded directly inside each prompt file in `settings/prompts/` (one full prompt per LLM call site, fed as-is to the model with minimal `{placeholder}` substitution for the listing). The Profile is no longer a Loader concern; the prompt author owns language coverage. Not to be confused with the Phase-2 **CV Profile** used for generating application material. _Avoid_: profile (without qualifier), bio.

**Skill**: A hard-skill or technology item from the applicant's comprehensive list in the **Config**'s `SKILLS`. Substituted into the **Match Judge** prompt as a structured inventory. Not used in any formula — there is no `Skills ∩ Requirements` matcher. _Avoid_: keyword (when matching).

**Keyword**: A search term used to query a **Source**, from the **Config**'s `KEYWORDS`. Distinct from a **Skill** — does not affect judgment. _Avoid_: skill (when querying).

**Match Verdict**: The structured output of the **Match Judge** for an in-domain **Position** — `{tier: green | amber | red, matched: [...], missing: [...], summary: "2-3 sentences"}`. Drives both rendering (Card vs Headline) and Card content. _Avoid_: score, rating.

**Match Tier**: The `tier` field of the **Match Verdict** — green / amber / red — assigned directly by the **Match Judge** LLM, not derived from a numeric formula. **Card = green tier**; amber and red render as **Headlines**. _Avoid_: score, rating.

**Relevance Classifier**: A component that decides whether a **Position** is in the applicant's professional domain (AI / Data / Game Dev / SWE), based on title and **Raw Description**. v1 has one production implementation backed by the **LLM Extractor**. **Off-domain Positions are discarded — never written to the Results File.** _Avoid_: filter (ambiguous), gate.

**Deduplication**: The process of skipping **Positions** seen in a previous run. Two-tier: exact-match on URL, plus exact-match on the `(company, title, city)` tuple lowercased. When the tuple matches under a new URL (a syndicated copy), the **Deduplication Store** records an alias entry under the new URL — copying the original record's `status` and `first_seen` — so subsequent runs short-circuit on URL lookup without re-checking the tuple. The alias write is an internal index optimization performed inside `is_seen`; the call's return value is unaffected. `first_seen` therefore answers "when did this *role* first appear, under which URL", not "when did this URL first appear". (A genuinely fuzzy match — Levenshtein, token-set ratio — may replace the second tier later if exact-on-lowercased proves too brittle; v1 keeps it simple.)

**Error semantics for `mark_seen`** (orchestrator behavior, single-writer Pi):
- If **Relevance Classifier** raises (Ollama down, timeout, etc.): **do not mark seen** — next run retries. Optimizes for transient failures over silent loss.
- If **Match Judge** raises after relevance accepted: same — **do not mark seen**, retry next run.
- For accepted in-domain Positions, the orchestrator **appends** the rendered block to the **Results File**, **fsyncs** the file, then marks seen with `status: "kept"`. A crash between fsync and mark will replay the listing on the next run (one bounded duplicate in `current.md`). This is preferred over the alternative — marking before appending would silently lose listings on a rare append failure. Off-domain Positions are marked with `status: "off_domain"` after the classifier returns false.
There is no automatic retry cap in v1 — a persistently-poisoned listing will re-pay enrich+classify cost on every run until manually skipped. Revisit if it actually happens. State lives in `.seen.json` (committed to git), shaped as `{url: {company_lc, title_lc, city_lc, status, first_seen}}`; the fuzzy index is built in-memory at load, not persisted. Off-domain **Positions** discarded by the **Relevance Classifier** are also marked seen — the gate is "already evaluated", not "already kept" — so re-runs don't re-pay classifier cost on the same noise. `status: "off_domain" | "kept"` is recorded for debugging. The pipeline is single-writer (Pi only); no cross-process locking. _Avoid_: duplicate filtering, URL filtering.

### Display

**Headline**: The single-line summary shown for every written **Position**: number, company, title, location, **Match Tier**, language, URL.

**Card**: The expanded view shown for green-tier **Positions** — adds the **Match Verdict**'s `matched` list (rendered with highlighting), `missing` list, and 2–3 sentence `summary`. The `matched` list comes directly from the **Match Judge**; no second LLM call is needed.

### Sources & extraction

**Source**: A configured job board or API in the **Config**'s `SOURCES`, declared as a `SourceEntry` carrying a **Parser Type** and a per-source `max_results` cap. _Avoid_: site, board.

**Parser**: A Python module in `parsers/` that fetches **Positions** from one **Source**. Two-phase API: `discover()` returns cheap **Position Stubs** from list-pages; `enrich(stub)` fetches the detail page and returns a full **Position** conforming to the **Position Schema**. The orchestrator runs **Deduplication** between the phases so detail-fetches are paid only on unseen stubs. The **Parser** owns location filtering — it issues queries matching the **Config**'s `LOCATIONS` and `INCLUDE_REMOTE`. The orchestrator does not re-filter by location. _Avoid_: scraper, fetcher.

**Position Stub**: The cheap result of `Parser.discover()` — `url, title, company, city, source, language` (`language` may be `None` if the source list page exposes no signal). Carries exactly the fields **Deduplication** needs to decide whether to skip. _Avoid_: preview, summary.

**Parser Type**: The string on a `SourceEntry` identifying which **Parser** to use — maps directly to a filename in `parsers/`. _Avoid_: adapter.

**API Parser**: A **Parser** that calls a JSON endpoint. Used for Bundesagentur and stellen.hamburg.

**WebFetch Parser**: A **Parser** that fetches a URL and parses the returned HTML. Used for jobs-beim-staat.

**LLM Extractor**: A Python interface (Protocol) with two methods — `classify_relevance(profile, title, raw_description, language) -> RelevanceVerdict` and `judge_match(profile, skills, raw_description) -> MatchVerdict`. `RelevanceVerdict` is `{in_domain: bool, language: "de" | "en" | "other"}`; if the **Parser** supplied a `language` value it is passed through unchanged, otherwise the LLM classifies it as part of the call. Folding language detection into the cheap classify call keeps it to one extra prompt slot rather than a separate call. The v1 production implementation calls **Ollama** running locally (Qwen 2.5 7B Instruct Q4_K_M). Same implementation runs on laptop (v1) and Pi (v1.1). _Avoid_: LLM, model (without qualifier).

**Match Judge**: A named class wrapping `LLMExtractor.judge_match`. Replaces the formula-based Position Matcher. Runs only on **Positions** that passed the **Relevance Classifier**. Returns a **Match Verdict**.

**Pagination**: Successive page fetches per **Source**, ordered newest-first by posted_date, until either the **Source** signals no next page (empty page, no next-link, or API end-of-results) **or** the per-Source `max_results` cap is reached. Each `SourceEntry` in the **Config** carries its own cap (default 1000). The cap bounds first-run cost; on subsequent runs **Deduplication** keeps work small naturally. Sources that don't expose a posted_date sort key must approximate (e.g., the source's default ordering, which is typically reverse-chronological).

### Invocation

The full pipeline runs as a single Python entry point on the Pi (the project's package name is `application_pipeline` per `pyproject.toml`; the exact module path is decided at implementation time). There is no `/search` Claude Code skill — that idea was retired. The laptop is used only during development to smoke-test individual **Parsers** in isolation (no LLM, no **Deduplication**, no **Results File**). Ollama, the **Relevance Classifier**, the **Match Judge**, **Deduplication**, and **Results File** rendering only ever run on the Pi. Note: the `pycastle/` directory in this repo is an unrelated RALPH Loop coding-agent plugin used to *build* this project; it is not this project's Python package.

## Relationships

- A **Results File** contains zero or more **Positions**, each with a unique number. The file is rolling and reset manually; **Deduplication** state survives the reset.
- A **Position** belongs to exactly one **Source**, was fetched by exactly one **Parser**, and was enriched by the **LLM Extractor**.
- A **Position** is written only if it passes the **Relevance Classifier**. The **Match Tier** in its **Match Verdict** controls **Headline** vs **Card** rendering, not inclusion.
- The **Match Judge** runs after **Relevance Classifier** acceptance, takes `(Triage Profile, Skills, Raw Description)`, and returns a **Match Verdict**. There is no string-intersection or percentage formula.
- The **Triage Profile** is embedded in the prompt files in `settings/prompts/`; **Skills** and **Keywords** live in `settings/config.py` and reach the LLM via prompt-template substitution.

## Flagged ambiguities

- **"Filter"** — used to mean both **Relevance Classifier** (discards) and **Match Threshold** (collapses to **Headline**). These are distinct stages with distinct effects; never use the bare word "filter".
- **"Match Score"** — formerly a percentage formula. Removed. The **Match Judge** LLM emits **Match Tier** directly. See [ADR-0003](docs/adr/0003-match-tier-judged-by-llm.md).
- **"Profile"** — disambiguate. The v1 **Triage Profile** (prose, embedded in the prompt files in `settings/prompts/`, drives LLM judgment) is distinct from the Phase-2 **CV Profile** (structured, used to generate application material). Never use "profile" without a qualifier.
- **"Summary" vs "Raw Description"** — the 2–3 sentence summary in the **Card** is part of the **Match Verdict**, written by the **Match Judge**. `raw_description` is the full original text. Never use "description" when the full text is meant.
- **"Source" vs "Parser"** — a **Source** is a config entry (what to search); a **Parser** is the module (how). One **Parser** can serve multiple **Sources** if their structure matches.
