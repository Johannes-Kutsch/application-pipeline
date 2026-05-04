# Application Pipeline

A personal job-discovery and triage pipeline. Fetches listings from a small set of sources, classifies relevance with a local LLM, scores fit against the applicant's skill list, and emits a single rolling shortlist as markdown. Application authoring (CV/cover letter generation) is explicitly out of scope for v1.

## Scope

- **In scope (v1):** Search across **Bundesagentur**, **stellen.hamburg**, and **jobs-beim-staat**; **Relevance Classifier**; **Match Score**; **Deduplication**; rolling **Results File**.
- **In scope (v1.1):** Pi cron deployment + Syncthing sync of the **Results File**.
- **Out of scope:** **CV** / **Cover Letter** generation, LaTeX pipeline, **Profile** ingestion, additional commercial parsers (LinkedIn / StepStone / Indeed / Adzuna), Playwright, browser automation.

## Language

### Pipeline artifacts

**Search Config**: A `.md` file with YAML frontmatter that controls the search — `keywords`, `skills`, `sources`, `match_threshold`, `locations`, `include_remote`. _Avoid_: config file, settings file.

**Results File**: A single rolling `results/current.md` that accumulates **Positions** since the last manual reset. Reset by moving/deleting the file; the next run creates a fresh one. _Avoid_: dated results file, output file.

**Position**: A single job listing surviving the **Relevance Classifier** and **Deduplication**, identified by a unique number within the current **Results File**. _Avoid_: job, listing, vacancy.

**Position Schema**: The dict structure every **Parser** must return — `title, company, location, language, url, source, requirements, nice_to_have, description, raw_description` plus optional `salary, contract_type, employment_type, work_model, posted_date, deadline`. `requirements` and `nice_to_have` are populated by the **LLM Extractor**, not by the **Parser**. _Avoid_: output format.

**Raw Description**: The full unmodified text of a **Position** as returned by the **Parser**. Input to the **LLM Extractor**. _Avoid_: description (when full text is meant).

### Filtering & scoring

**Skill**: A hard-skill or technology item from the applicant's comprehensive list in the **Search Config**. _Avoid_: keyword (when matching).

**Keyword**: A search term used to query a **Source**. Distinct from a **Skill** — does not affect scoring. _Avoid_: skill (when querying).

**Requirements**: Hard skills the **LLM Extractor** identifies as must-haves in a **Position**'s **Raw Description**. The basis for **Match Score**. _Avoid_: skills (of a position), must-haves.

**Nice-to-have**: An optional skill identified by the **LLM Extractor**. Excluded from **Match Score**. _Avoid_: bonus skill.

**Match Score**: The percentage of a **Position**'s **Requirements** that the applicant's **Skills** cover. Direction: their requirements ÷ my coverage of them. _Avoid_: fit, score (without qualifier).

**Match Threshold**: The configured minimum **Match Score** below which a **Position** is rendered as a **Headline** only (still written to the **Results File**, just collapsed). Does not gate inclusion. _Avoid_: cutoff, filter.

**Match Tier**: Color tier for a **Position** by **Match Score**: green (≥70%), amber (40–70%), red (<40%). _Avoid_: rating.

**Relevance Classifier**: A component that decides whether a **Position** is in the applicant's professional domain (AI / Data / Game Dev / SWE), based on title and **Raw Description**. v1 has one production implementation backed by the **LLM Extractor**. **Off-domain Positions are discarded — never written to the Results File.** _Avoid_: filter (ambiguous), gate.

**Deduplication**: The process of skipping **Positions** seen in a previous run. Two-tier: exact-match on URL, plus fuzzy match on `(company, title, city)` lowercased. State lives in `.seen.json` (committed to git). _Avoid_: duplicate filtering, URL filtering.

### Display

**Headline**: The single-line summary shown for every written **Position**: number, company, title, location, match %, language, URL.

**Card**: The expanded view shown for **Positions** at or above the **Match Threshold** — adds matched skills, missing skills, a 2–3 sentence summary, and the **Match Tier** color.

### Sources & extraction

**Source**: A configured job board or API in the **Search Config** with a declared **Parser Type** and `max_pages`. _Avoid_: site, board.

**Parser**: A Python module in `parsers/` that fetches **Positions** from one **Source** and returns them conforming to the **Position Schema** (sans `requirements` / `nice_to_have`, which are filled later by the **LLM Extractor**). _Avoid_: scraper, fetcher.

**Parser Type**: The string in the **Search Config** identifying which **Parser** to use — maps directly to a filename in `parsers/`. _Avoid_: adapter.

**API Parser**: A **Parser** that calls a JSON endpoint. Used for Bundesagentur and stellen.hamburg.

**WebFetch Parser**: A **Parser** that fetches a URL and parses the returned HTML. Used for jobs-beim-staat.

**LLM Extractor**: A Python interface (Protocol) with two methods — `extract_requirements(raw_description)` and `classify_relevance(title, description)`. The v1 production implementation calls **Ollama** running locally (Qwen 2.5 7B Instruct Q4_K_M). Same implementation runs on laptop (v1) and Pi (v1.1). _Avoid_: LLM, model (without qualifier).

**Pagination**: Successive page fetches per **Source** until `max_pages` or no next page is detected.

### Commands

`/search`: The Claude Code skill that triggers a single run of the pipeline locally. Thin wrapper over the Python entry point — the same code that v1.1 cron will invoke without Claude Code.

## Relationships

- A **Results File** contains zero or more **Positions**, each with a unique number. The file is rolling and reset manually; **Deduplication** state survives the reset.
- A **Position** belongs to exactly one **Source**, was fetched by exactly one **Parser**, and was enriched by the **LLM Extractor**.
- A **Position** is written only if it passes the **Relevance Classifier**. **Match Score** controls **Headline** vs **Card** rendering, not inclusion.
- **Requirements** and **Nice-to-have** are extracted from the **Raw Description** by the **LLM Extractor** after the **Parser** returns.
- **Skills** live in the **Search Config**; **Match Score** = |applicant's **Skills** ∩ **Requirements**| ÷ |**Requirements**|.

## Flagged ambiguities

- **"Filter"** — used to mean both **Relevance Classifier** (discards) and **Match Threshold** (collapses to **Headline**). These are distinct stages with distinct effects; never use the bare word "filter".
- **"Match Score" direction** — earlier drafts defined it as `|matched skills| / |my skills|`. Canonical: `|matched skills| / |their requirements|`. Reason: the latter answers "can I credibly apply?" — the question that actually decides whether a **Position** belongs in the **Results File**.
- **"Profile"** — Phase 2 vocabulary. Out of scope for v1; not yet defined here.
- **"Description" vs "Raw Description"** — `description` is the 2–3 sentence **LLM Extractor**-written summary shown in the **Card**. `raw_description` is the full original text. Never use "description" when the full text is meant.
- **"Source" vs "Parser"** — a **Source** is a config entry (what to search); a **Parser** is the module (how). One **Parser** can serve multiple **Sources** if their structure matches.
