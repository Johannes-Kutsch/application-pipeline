# Ubiquitous Language

## Core Artifacts

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Search Config** | The `.md` file with YAML frontmatter that controls what the search looks for — keywords, sources, skills, thresholds, and language filters | config file, settings file |
| **Results File** | The dated markdown file (`results/YYYY-MM-DD_results.md`) that accumulates all **Positions** found in a day's searches | output file, search results |
| **Position** | A single job listing found during a search, identified by a unique number within a **Results File** | job, listing, vacancy, offer |
| **Position Schema** | The common dict structure every **Parser** must return — 14 fields including title, company, requirements, nice_to_have, raw_description, and optional fields (salary, contract_type, employment_type, work_model, posted_date, deadline) | output format, return type |
| **Profile** | A structured markdown file (`profile_de.md` / `profile_en.md`) that is the canonical source of truth about the applicant's experience, skills, and background | CV, resume (when used as a data source) |
| **Style File** | A LaTeX file containing only visual/formatting definitions — never modified by Claude | template (avoid — ambiguous) |
| **Content File** | A LaTeX file containing only the applicant's data for a specific application — the only LaTeX file Claude writes | template (avoid — ambiguous) |
| **CV** | The generated document (`cv_de.tex` / `cv_en.tex`) produced by `/generate-cv`, compiled to PDF — equivalent to Lebenslauf in German context | resume (in German applications) |
| **Cover Letter** | The generated document (`letter_de.tex` / `letter_en.tex`) produced by `/generate-letter` — equivalent to Anschreiben in German context | application letter |
| **Application** | The folder containing all generated documents for a single selected **Position** | submission |
| **Reference Material** | Existing documents used as source input for **Profile** ingestion — master CV, website, past cover letters, skills list | source material, input files |
| **Raw Description** | The full unmodified text of a **Position** as returned by the **Parser** — stored in the **Results File** and used by the CV generator for tailoring | description (when full text is meant) |

## Search & Matching

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Skill** | A hard-skill or technology item from the **Search Config** used to score **Positions** | keyword (when referring to matching) |
| **Keyword** | A search term used to find **Positions** on job boards — distinct from a **Skill** | skill (when referring to search terms) |
| **Requirements** | Hard skills extracted from a **Position**'s description by the **Parser** — used as the basis for **Match Score** calculation | skills (of a position), must-haves |
| **Nice-to-have** | An optional skill extracted from a **Position**'s description — excluded from **Match Score** calculation | bonus skill, preferred skill |
| **Match Score** | The percentage of the applicant's hard **Skills** that overlap with a **Position**'s **Requirements** | score, fit |
| **Match Threshold** | The configured minimum **Match Score** below which a **Position** is collapsed to a **Headline** only | filter, cutoff |
| **Match Tier** | A color classification of a **Position** based on its **Match Score**: green (>70%), amber (40–70%), red (<40%) | rating, level |

## Position Display

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Headline** | The single-line summary shown for every **Position**, containing number, company, title, location, match %, language, and URL | summary line |
| **Card** | The expanded view shown for **Positions** above the **Match Threshold**, adding matched/missing skills and a description | detail view, expanded view |

## Sources & Parsers

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Source** | A configured job board or API endpoint in the **Search Config**, with a declared **Parser Type** and `max_pages` | job board, site |
| **Parser** | A Python module in `parsers/` that fetches **Positions** from one **Source** and returns them conforming to the **Position Schema** | scraper, crawler, fetcher |
| **Parser Type** | The string in the **Search Config** that identifies which **Parser** to use for a **Source** — maps directly to a filename in `parsers/` | parser name, adapter |
| **API Parser** | A **Parser** that calls a structured REST API — cleanest data, used for Bundesagentur für Arbeit (V1) and Adzuna (V2) | — |
| **WebFetch Parser** | A **Parser** that fetches a URL with search parameters encoded in the URL and parses the returned HTML | scraper (when URL-based) |
| **Playwright Parser** | A **Parser** that uses browser automation to interact with JavaScript-heavy sites or form-based search | browser scraper |
| **Pagination** | The process of fetching successive pages from a **Source** until `max_pages` is reached or no next page is detected | paging, page iteration |
| **Deduplication** | The process of skipping **Positions** whose URL was already seen in a previous search run | duplicate filtering |

## Commands

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| `/search` | The Claude Code skill that runs all configured **Parsers**, scores results, and appends **Positions** to the day's **Results File** | run search, fetch jobs |
| `/generate-cv [n] [lang]` | The Claude Code skill that generates a **CV** for **Position** number `n` in the specified language | make CV, create resume |
| `/generate-letter [n] [lang]` | The Claude Code skill that generates a **Cover Letter** for **Position** number `n` in the specified language | make cover letter, write Anschreiben |
| **Ingestion** | The one-time process of reading all **Reference Material** and producing `profile_de.md` and `profile_en.md` | import, setup |

## Relationships

- A **Results File** contains one or more **Positions**, each with a unique number that persists across same-day search runs.
- A **Position** has exactly one **Match Score**, one **Match Tier**, and one language.
- A **Position** belongs to exactly one **Source** and was fetched by exactly one **Parser**.
- A **Parser** conforms to the **Position Schema** — every field the downstream pipeline reads is guaranteed to exist.
- **Requirements** and **Nice-to-haves** are extracted from the **Raw Description** by the **Parser** at fetch time.
- An **Application** folder belongs to exactly one **Position** and may contain a **CV**, a **Cover Letter**, or both, in one or more languages.
- A **CV** and **Cover Letter** each consist of one **Content File** (written by Claude) and one **Style File** (never modified).
- A **Profile** is derived from **Reference Material** via **Ingestion** and is read by Claude during every **CV** and **Cover Letter** generation.
- **Skills** live in the **Search Config** and drive **Match Score** calculation.
- **Keywords** live in the **Search Config** but do not affect **Match Score** — they only affect which **Positions** a **Parser** fetches.

## Example Dialogue

> **Dev:** "When I add karriere.hamburg.de as a **Source**, what do I put in the config?"
>
> **Domain expert:** "Set `parser: webfetch_karriere_hamburg` and a `max_pages` cap. The **Parser Type** maps directly to the filename `parsers/webfetch_karriere_hamburg.py`. If that file doesn't exist yet, `/search` logs 'not yet implemented, skipping' and moves on."
>
> **Dev:** "Who extracts the **Requirements** and **Nice-to-haves** from the description?"
>
> **Domain expert:** "The **Parser** does — before returning the **Position**. By the time the **Position Schema** hits the matcher, `requirements` and `nice_to_have` are already split. The **Match Score** only touches `requirements`, never `nice_to_have`."
>
> **Dev:** "What happens if the same listing appears in two consecutive daily runs?"
>
> **Domain expert:** "**Deduplication** catches it — the **Position**'s URL is checked against `.seen_urls` before scoring. If it's there, the **Position** is skipped entirely and never written to the **Results File** again."
>
> **Dev:** "And the **Raw Description** — is that just for humans to read in the **Card**?"
>
> **Domain expert:** "No — the **Card** shows a 2-3 sentence `description` summary. The **Raw Description** is stored silently in the **Results File** for `/generate-cv` to use later. Without it, the CV generator only has the summary, and tailoring quality drops significantly."

## Flagged Ambiguities

- **"Resume" vs "CV"** — canonical choice: **CV** for the generated output document in both languages. In German applications, CV = Lebenslauf. "Resume" avoided to prevent American one-page vs European multi-page confusion.
- **"Template"** — used to mean both **Style File** and content skeleton. These are distinct: Claude writes **Content Files**, never **Style Files**. Avoid "template" without qualification.
- **"Profile"** — distinct from a **CV**: a **Profile** is a machine-readable data source Claude reads from; a **CV** is a human-readable document Claude writes to.
- **"Keyword" vs "Skill"** — both in the **Search Config**, different purposes. **Keywords** drive **Parser** queries. **Skills** drive **Match Score**. A term like "Python" can be both, but must be configured in the correct field.
- **"Description" vs "Raw Description"** — `description` is the 2-3 sentence human-readable summary shown in the **Card**. `raw_description` is the full original text stored for CV generation. Never use "description" when the full text is meant.
- **"Source" vs "Parser"** — a **Source** is a configured entry in the **Search Config** (what to search). A **Parser** is the Python module that does the fetching (how to search it). One **Parser** file can serve multiple **Sources** if they share the same structure.
