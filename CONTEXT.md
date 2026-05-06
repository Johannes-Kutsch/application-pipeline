# Application Pipeline

A personal job-discovery and triage pipeline. Fetches listings from a small set of sources, classifies relevance with a local LLM, scores fit against the applicant's skill list, and emits a single rolling shortlist as markdown. Application authoring (CV/cover letter generation) is explicitly out of scope for v1.

## Scope

- **In scope (v1):** Working **Parsers** for **Bundesagentur**, **stellen.hamburg**, and **jobs-beim-staat**, each provably returning **Position Stubs** and capable of enriching them. Smoke-tested standalone on the laptop. No LLM, no orchestration, no **Results File** in v1.
- **In scope (v1.1):** Full pipeline running on the Pi — orchestrator, **Relevance Classifier**, **Match Judge**, **Deduplication**, rolling **Results File**, cron, Syncthing sync of the **Results File** to the laptop. Ollama lives only on the Pi.
- **Out of scope:** **CV** / **Cover Letter** generation, LaTeX pipeline, **Profile** ingestion, additional commercial parsers (LinkedIn / StepStone / Indeed / Adzuna), Playwright, browser automation.

## Language

### Pipeline artifacts

**Config**: A `settings/config.py` at the repo root that controls the search — `KEYWORDS`, `SKILLS`, `INCLUSION_KEYWORDS`, `NEGATIVE_KEYWORDS`, `SOURCES`, `LOCATIONS`, `INCLUDE_REMOTE`, the `PROMPTS_DIR` path, the **Layout** path override, and the `OLLAMA_*` settings (`base_url`, `classify_model`, `judge_model`, `read_timeout_seconds`, `json_retries`, `http_retries`, `keep_alive`). Plain Python literals, edited in place, with `#` comments. Loaded by the **Config Loader**, which returns a frozen typed `Config` dataclass. `KEYWORDS` is a single global list applied to every **Source** (Cartesian product over keyword × source); **Deduplication** absorbs the overlap. No per-source keyword overrides in v1. Each entry in `SOURCES` is a `SourceEntry` carrying its own `max_results` cap (default 1000) controlling how many newest-first listings that **Source** yields per run. `INCLUSION_KEYWORDS` and `NEGATIVE_KEYWORDS` drive the **Domain Pre-Filter**; both validate to entries of length ≥ 3. `PROMPTS_DIR` (default `prompts/`) is the directory containing per-language prompt files (see ADR-0006). _Avoid_: config file (without qualifier), settings file, search config.

**Layout**: A `settings/layout.py` at the repo root sitting alongside the **Config**, owning all cosmetic and structural choices for the **Results File**: tier emoji, tier color hex, named placeholder groups, the `FILE_HEADER` written on init, and the full `CARD_TEMPLATE` / `HEADLINE_TEMPLATE`. Plain Python module loaded at runtime by `load_user_module` (the same machinery as **Config**), validated into a frozen typed `Layout` dataclass. The **Renderer** consumes the `Layout` and substitutes placeholders via `str.format_map`. Edited freely by the user — re-design happens here, not in package code. Errors raise `LayoutError`, a subclass of `UserSettingsError` (the shared base with `ConfigError`). _Avoid_: style file, theme file, template file (when the loaded dataclass is meant).

**Results File**: A single rolling `results/current.md` that accumulates **Positions** since the last manual reset. Reset by moving/deleting the file; the next run creates a fresh one. _Avoid_: dated results file, output file.

**Position**: A single job listing surviving the **Relevance Classifier** and **Deduplication**, identified by a unique number within the current **Results File**. _Avoid_: job, listing, vacancy.

**Position Schema**: The dict structure every **Parser** must return — `title, company, location, language, url, source, raw_description` plus optional `salary, contract_type, employment_type, work_model, posted_date, deadline`. `language` (`"de" | "en" | "other" | "unknown"`) is filled by the **Parser** when source metadata exposes it; if the source has no language signal the **Parser** leaves it `None` and the **Domain Pre-Filter** fills it via `langdetect`, falling back to `"unknown"` when confidence is too low. The **Match Judge** later attaches a **Match Verdict** (`tier, matched, missing, summary`); these are not parser outputs. _Avoid_: output format.

**Raw Description**: The full unmodified text of a **Position** as returned by the **Parser**. Input to the **LLM Extractor**. _Avoid_: description (when full text is meant).

### Filtering & scoring

**Triage Profile**: A short prose description of the applicant — experience, hard skills, role-shape preferences, what they're looking for. Embedded directly inside each prompt file in `prompts/` (one full prompt per LLM call site **per language**, fed as-is to the model with minimal `{placeholder}` substitution for the listing). Per ADR-0006, files are named `{call_site}.{lang}.md` (e.g. `classify_relevance.de.md`); the prompt author writes the Profile in each language natively. The Profile is not a Loader concern. Not to be confused with the Phase-2 **CV Profile** used for generating application material. _Avoid_: profile (without qualifier), bio.

**Skill**: A hard-skill or technology item from the applicant's comprehensive list in the **Config**'s `SKILLS`. Rendered as a bullet list into the **Match Judge** prompt's `{skills}` slot at OllamaExtractor construction (one entry per line, `- ` prefix). Also reused by the **Domain Pre-Filter** as part of the inclusion-keyword whitelist. Not used in any formula — there is no `Skills ∩ Requirements` matcher, and the LLM's `matched`/`missing` lists are open-vocabulary (no closed-vocab validation against `SKILLS`). _Avoid_: keyword (when matching).

**Keyword**: A search term used to query a **Source**, from the **Config**'s `KEYWORDS`. Distinct from a **Skill** — does not affect judgment. Distinct from `INCLUSION_KEYWORDS` and `NEGATIVE_KEYWORDS`, which drive the **Domain Pre-Filter**. _Avoid_: skill (when querying), inclusion/negative keyword (when querying).

**Inclusion Keyword**: An entry in the **Config**'s `INCLUSION_KEYWORDS`. Patterns (length ≥ 3) that, if found via case-insensitive substring match in a **Position**'s title or `raw_description`, force the **Position** past the **Domain Pre-Filter** even if a **Negative Keyword** also matches. Concatenated with `SKILLS` to form the effective whitelist. _Avoid_: skill, keyword.

**Negative Keyword**: An entry in the **Config**'s `NEGATIVE_KEYWORDS`. Patterns (length ≥ 3) that, if found in a **Position**'s title or `raw_description`, cause the **Domain Pre-Filter** to drop the listing — unless an **Inclusion Keyword** or **Skill** also matches (whitelist wins). _Avoid_: blacklist, exclusion.

**Match Verdict**: The structured output of the **Match Judge** for an in-domain **Position** — `{tier: green | amber | red, matched: [...], missing: [...], summary: "2-3 sentences"}`. `matched` and `missing` are short open-vocabulary phrases authored by the LLM (validation is type/length only — no closed-vocabulary check against `SKILLS`). Drives both rendering (Card vs Headline) and Card content. _Avoid_: score, rating.

**Match Tier**: The `tier` field of the **Match Verdict** — green / amber / red — assigned directly by the **Match Judge** LLM, not derived from a numeric formula. **Card = green tier**; amber and red render as **Headlines**. _Avoid_: score, rating.

**Relevance Classifier**: The conceptual name for the call site that decides whether a **Position** is in the applicant's professional domain (AI / Data / Game Dev / SWE). Implemented as a direct call to `LLMExtractor.classify_relevance` — there is no wrapper class. Runs **only on Positions that survive the Domain Pre-Filter**, so its volume is bounded by the Pre-Filter's drop rate. **Off-domain Positions are discarded — never written to the Results File.** _Avoid_: filter (ambiguous), gate.

**Domain Pre-Filter**: A deterministic module that runs before the **Relevance Classifier**, dropping **Positions** that match a **Negative Keyword** without also matching an **Inclusion Keyword** or **Skill**. Whitelist wins over blacklist. Substring match (case-insensitive, after `normalize()`) over `title + raw_description`. Also fills `Position.language` via `langdetect` when the **Parser** left it `None`. Pure, stateless, pre-LLM. Per ADR-0005. _Avoid_: filter (ambiguous), gate, classifier.

**Deduplication**: The process of skipping **Positions** seen in a previous run. Two-tier: exact-match on URL, plus exact-match on the `(company, title, city)` tuple lowercased. When the tuple matches under a new URL (a syndicated copy), the **Deduplication Store** records an alias entry under the new URL — copying the original record's `status` and `first_seen` — so subsequent runs short-circuit on URL lookup without re-checking the tuple. The alias write is an internal index optimization performed inside `is_seen`; the call's return value is unaffected. `first_seen` therefore answers "when did this *role* first appear, under which URL", not "when did this URL first appear". (A genuinely fuzzy match — Levenshtein, token-set ratio — may replace the second tier later if exact-on-lowercased proves too brittle; v1 keeps it simple.)

**Error semantics for `mark_seen`** (orchestrator behavior, single-writer Pi):
- If **Relevance Classifier** raises (Ollama down, timeout, etc.): **do not mark seen** — next run retries. Optimizes for transient failures over silent loss.
- If **Match Judge** raises after relevance accepted: same — **do not mark seen**, retry next run.
- For accepted in-domain Positions, the orchestrator **appends** the rendered block to the **Results File**, **fsyncs** the file, then marks seen with `status: "kept"`. A crash between fsync and mark will replay the listing on the next run (one bounded duplicate in `current.md`). This is preferred over the alternative — marking before appending would silently lose listings on a rare append failure. Off-domain Positions are marked with `status: "off_domain"` after the classifier returns false.
There is no automatic retry cap in v1 — a persistently-poisoned listing will re-pay enrich+classify cost on every run until manually skipped. Revisit if it actually happens. State lives in `.seen.json` (committed to git), shaped as `{url: {company_lc, title_lc, city_lc, status, first_seen}}`; the fuzzy index is built in-memory at load, not persisted. Off-domain **Positions** discarded by the **Relevance Classifier** are also marked seen — the gate is "already evaluated", not "already kept" — so re-runs don't re-pay classifier cost on the same noise. `status: "off_domain" | "kept"` is recorded for debugging. The pipeline is single-writer (Pi only); no cross-process locking. _Avoid_: duplicate filtering, URL filtering.

### Display

**Headline**: The single-line summary shown for every written **Position**: number, company, title, location, **Match Tier**, language, URL.

**Card**: The expanded view shown for green-tier **Positions** — adds the **Match Verdict**'s `matched` list (rendered with highlighting), `missing` list, and 2–3 sentence `summary`. The `matched` list comes directly from the **Match Judge**; no second LLM call is needed.

**Renderer**: A pure module exposing `render(position, verdict, number, layout) -> str`. Picks the **Layout**'s `CARD_TEMPLATE` for green tier, `HEADLINE_TEMPLATE` for amber/red. Builds a placeholder dict (raw fields + named placeholder groups + tier-derived `emoji` / `color` / `tier`), substitutes via `str.format_map`, returns the rendered block. No file I/O, deterministic on inputs. _Avoid_: formatter, presenter.

**Results File Manager**: The only module that reads or writes `results/current.md`. Surface: `ensure_initialized()` (writes the **Layout**'s `FILE_HEADER` if file is missing or 0 bytes; `mkdir(parents=True)` for missing parent dirs; idempotent on a non-empty file), `next_position_number()` (scans for `^## (\d+)\.` lines, returns max+1 or 1; raises `ResultsFileError` when the file is missing or 0 bytes — the **Pipeline Orchestrator** is expected to have called `ensure_initialized` first), `append(rendered_block)` (verbatim write + `flush` + `os.fsync`, propagates `OSError` unwrapped). Constructed with the path; the **Pipeline Orchestrator** picks the path (hardcoded `results/current.md`, not a Config field — reset is "move the file", not "change the path"). No garbage-file detection: a non-empty `current.md` with no `## N.` lines silently restarts numbering at 1. _Avoid_: writer, output manager.

### Sources & extraction

**Source**: A configured job board or API in the **Config**'s `SOURCES`, declared as a `SourceEntry` carrying a **Parser Type** and a per-source `max_results` cap. _Avoid_: site, board.

**Parser**: A Python module in `parsers/` that fetches **Positions** from one **Source**. Two-phase API: `discover()` returns cheap **Position Stubs** from list-pages; `enrich(stub)` fetches the detail page and returns a full **Position** conforming to the **Position Schema**. The orchestrator runs **Deduplication** between the phases so detail-fetches are paid only on unseen stubs. The **Parser** owns location filtering — it issues queries matching the **Config**'s `LOCATIONS` and `INCLUDE_REMOTE`. The orchestrator does not re-filter by location. _Avoid_: scraper, fetcher.

**Position Stub**: The cheap result of `Parser.discover()` — `url, title, company, city, source, language` (`language` may be `None` if the source list page exposes no signal). Carries exactly the fields **Deduplication** needs to decide whether to skip. _Avoid_: preview, summary.

**Parser Type**: The string on a `SourceEntry` identifying which **Parser** to use — maps directly to a filename in `parsers/`. _Avoid_: adapter.

**API Parser**: A **Parser** that calls a JSON endpoint. Used for Bundesagentur and stellen.hamburg.

**WebFetch Parser**: A **Parser** that fetches a URL and parses the returned HTML. Used for jobs-beim-staat.

**LLM Extractor**: A Python interface (Protocol) with two methods — `classify_relevance(language, title, raw_description) -> RelevanceVerdict` and `judge_match(language, raw_description) -> MatchVerdict`. `RelevanceVerdict` is `{in_domain: bool}` (language is no longer LLM output — the **Domain Pre-Filter** owns it). The `language` argument selects which per-language prompt file to use (per ADR-0006); it is not a prompt slot. The **Triage Profile** is embedded in each prompt file, not passed as an argument. **Skills** are bound at `OllamaExtractor` construction (not method-arg) and rendered into the `judge_match` prompt's `{skills}` slot. The v1 production implementation calls **Ollama** running locally (Qwen 3 8B Instruct Q4_K_M, per ADR-0001). Pi-only. _Avoid_: LLM, model (without qualifier).

**Match Judge**: The conceptual name for the call site that produces a **Match Verdict** for an in-domain **Position**. Implemented as a direct call to `LLMExtractor.judge_match` — there is no wrapper class. Replaces the formula-based Position Matcher. Runs only on **Positions** that passed the **Relevance Classifier**. Returns a **Match Verdict**.

**Pagination**: Successive page fetches per **Source**, ordered newest-first by posted_date, until either the **Source** signals no next page (empty page, no next-link, or API end-of-results) **or** the per-Source `max_results` cap is reached. Each `SourceEntry` in the **Config** carries its own cap (default 1000). The cap bounds first-run cost; on subsequent runs **Deduplication** keeps work small naturally. Sources that don't expose a posted_date sort key must approximate (e.g., the source's default ordering, which is typically reverse-chronological).

### Invocation

The full pipeline runs as a single Python entry point on the Pi (the project's package name is `application_pipeline` per `pyproject.toml`; the exact module path is decided at implementation time). There is no `/search` Claude Code skill — that idea was retired. The laptop is used only during development to smoke-test individual **Parsers** in isolation (no LLM, no **Deduplication**, no **Results File**). Ollama, the **Relevance Classifier**, the **Match Judge**, **Deduplication**, and **Results File** rendering only ever run on the Pi. Note: the `pycastle/` directory in this repo is an unrelated RALPH Loop coding-agent plugin used to *build* this project; it is not this project's Python package.

## Relationships

- A **Results File** contains zero or more **Positions**, each with a unique number. The file is rolling and reset manually; **Deduplication** state survives the reset.
- A **Position** belongs to exactly one **Source**, was fetched by exactly one **Parser**, and was enriched by the **LLM Extractor**.
- A **Position** is written only if it passes the **Domain Pre-Filter** *and* the **Relevance Classifier**. The **Match Tier** in its **Match Verdict** controls **Headline** vs **Card** rendering, not inclusion.
- The **Domain Pre-Filter** runs before the **Relevance Classifier** and never bypasses the LLM gate; it only drops listings.
- The **Match Judge** runs after **Relevance Classifier** acceptance, takes `(language, raw_description)` plus construction-bound **Skills** and the per-language prompt file's embedded **Triage Profile**, and returns a **Match Verdict**. There is no string-intersection or percentage formula.
- The **Triage Profile** is embedded in the prompt files in `prompts/{call_site}.{lang}.md`; **Skills** are bound at `OllamaExtractor` construction and reach the LLM via the `{skills}` slot; `INCLUSION_KEYWORDS` / `NEGATIVE_KEYWORDS` live in `settings/config.py` and reach the **Domain Pre-Filter** directly.

## Flagged ambiguities

- **"Filter"** — used to mean three different things: the **Domain Pre-Filter** (deterministic drop), the **Relevance Classifier** (LLM-based discard), and the **Match Threshold** (collapses to **Headline**). These are distinct stages with distinct effects; never use the bare word "filter".
- **"Match Score"** — formerly a percentage formula. Removed. The **Match Judge** LLM emits **Match Tier** directly. See [ADR-0003](docs/adr/0003-match-tier-judged-by-llm.md).
- **"Profile"** — disambiguate. The v1 **Triage Profile** (prose, embedded in the prompt files in `settings/prompts/`, drives LLM judgment) is distinct from the Phase-2 **CV Profile** (structured, used to generate application material). Never use "profile" without a qualifier.
- **"Summary" vs "Raw Description"** — the 2–3 sentence summary in the **Card** is part of the **Match Verdict**, written by the **Match Judge**. `raw_description` is the full original text. Never use "description" when the full text is meant.
- **"Source" vs "Parser"** — a **Source** is a config entry (what to search); a **Parser** is the module (how). One **Parser** can serve multiple **Sources** if their structure matches.
