# Application Pipeline

Personal job-discovery and triage pipeline. Fetches listings from a small set of sources, classifies relevance with Claude, accumulates an in-domain **Pool** across days, emits one dated **Daily Results File** per day carrying the **Daily Top-5** **Cards** ranked by the **Match Judge**.

## Scope

- **In scope (v1):** Working **Parsers** for **Bundesagentur**, **stellen.hamburg**, **jobs-beim-staat**, smoke-tested standalone on the laptop.
- **In scope (v1.1):** Full pipeline on Pi — orchestrator, **LLM Enricher** (ADR-0032 — body fetch + strip + **Relevance Classifier** producing **Header** + **Summary**), **Match Judge** (one call per run, picks **Daily Top-5** from the **Pool**), **Deduplication**, daily file, cron once per day, Syncthing sync.
- **Out of scope:** **Profile** ingestion, additional commercial parsers, browser automation. (CV / cover-letter LaTeX compilation in scope via `application-pipeline compile-cv` — see Invocation.)

## Language

### Pipeline artifacts

**Config**: `config.py` at `<cwd>/application-pipeline/` (ADR-0022) controlling pipeline shape — `SOURCES`, `LOCATIONS`, `INCLUDE_REMOTE`, optional `claude_cli_path`, `MAX_LISTING_AGE_DAYS: int` (default 180, `≥ 1`) driving the **Freshness Gate**, `claude_classify_parallelism: int` (default 4, `≥ 1`) sizing the classify worker pool (ADR-0031, ADR-0042), `claude_classify_batch_size: int` (default 10, `≥ 1`) — listings per classify call (ADR-0046), `DEDUP_COOLDOWN_DAYS: int` (default 30, `≥ 1`) gating decay of `selected_by_judge` and `expired` entries (ADR-0044). No path override knobs (ADR-0008 amended). `layout.py` retired (ADR-0033). `KEYWORDS`/`NEGATIVE_KEYWORDS` live in **SearchTerms** (ADR-0021). Materialised by `init`; never overwritten by `init --refresh`. Loaded by **Config Loader** into frozen typed `Config`. `max_results` retired (ADR-0041). Cron: weekdays 00:30 (ADR-0017). _Avoid_: config file, settings file, search config.

**SearchTerms**: User-authored search/filter knobs (ADR-0021) — `KEYWORDS`, `NEGATIVE_KEYWORDS`. Two markdown files under `<settings-dir>/user-info/search-terms/` (ADR-0024): `keywords.md`, `negative-keywords.md`. Filename *is* the section. Flat `-` bullet entries. `keywords.md` missing/empty → `SearchTermsError`; `negative-keywords.md` optional. `KEYWORDS` → parser orchestration, `NEGATIVE_KEYWORDS` → **Domain Pre-Filter**. `skills.md` relocated to **Triage Profile** dir (#615). _Avoid_: search config, term file.

**Layout**: _Retired_ per ADR-0033. Card structure hardcoded in **DailyResultsFile**. `init --refresh` deletes `layout.py`. _Avoid_: do not reintroduce.

**Daily Results File**: One dated markdown file at `<settings-dir>/results/YYYY-MM-DD.md`, holding **Daily Top-5** as **Cards** in **Rank** order. Date is cron-anchored (ADR-0015/0016). No preamble, no Run Divider. Write-once; synced read-only. _Avoid_: results file (without "daily"), `current.md`.

**Failure Report**: Markdown at `<settings-dir>/.runtime-data/failures/<timestamp>.md` (ADR-0007, path ADR-0037). Triggers: `cron.sh` errors (ADR-0020), orchestrator runtime errors, **Match Judge** failure, parser-dead events (ADR-0041), missing title in discover (ADR-0041), native enrich failures (ADR-0040). Must include enough context to diagnose without `run.log`. Acknowledged by deletion. Quota errors do NOT trigger — they sleep (ADR-0016). Per-listing soft failures (oversized/malformed) stash to sibling dirs without `seen.json` write. _Avoid_: incident report, error log.

**Position**: A single job listing surviving **Relevance Classifier** and **Match Judge** top-5 selection. Card shape fixed (ADR-0033): `# **{rank}:** {Header}` + `{Summary}`. _Avoid_: job, listing, vacancy.

**Position Schema**: _Retired_ per ADR-0032. Closed-enum fields live inside the LLM-authored **Header**. `PositionStub` and `EnrichResult` survive (ADR-0038). "Parsers never guess" invariant retires — LLM Enricher infers from body. _Avoid_: do not reintroduce per-field extraction.

**Raw Description**: Full body text, fetched and stripped by parser's `enrich()` (per-source CSS selector or generic library fallback). Fed to LLM and persisted in **Card Store** at classify time. Rendered into **Daily Results File** after **Summary**, fenced by `---` above and below. Empty body dropped by **Content Gate** (ADR-0030). Oversized stashed to `.runtime-data/failures/oversized/`, no `seen.json` mark. _Avoid_: description (when full text is meant).

**Structured Extract**: _Retired_ per ADR-0032. Replaced by **Header** + **Summary** + **Raw Description**. `extracts.json` is `{listing_id: {header, summary, body}}`, keyed by **Listing ID** integer. _Avoid_: do not reintroduce.

**Header**: Three-line block authored by the **LLM Enricher** at classify time (ADR-0032) — title, `company · location · work_model`, `posted_date · seniority · salary`. LLM substitutes known values, infers from body, or drops segments. Persisted in `extracts.json`. _Avoid_: card top, headline.

**Summary**: Prose paragraph authored at classify time (ADR-0032), describing the role in the **Triage Profile**'s frame. Persisted alongside **Header**. **Match Judge** ranks on Header + Summary directly. _Avoid_: match verdict summary (retired), description (overloaded).

### Filtering & scoring

**Triage Profile**: Pipeline-facing applicant data. Lives in `<settings-dir>/user-info/triage-profile/` (ADR-0024, ADR-0034) as three files: `gate-criteria.md` (flat domain-in/out + hard exclusions — classifier only), `candidate-profile.md` (who-the-candidate-is + ranking preferences — judge only), `skills.md` (relocated from `search-terms/`, judge `{SKILLS}` slot, #615). Loader exposes `{GATE_CRITERIA}`, `{CANDIDATE_PROFILE}`, `{SKILLS}` slots. Classifier consumes `{GATE_CRITERIA}` only; judge consumes `{CANDIDATE_PROFILE}` + `{SKILLS}`. `skills.md` attribute-stripped inline by `prompts.py`. Bullets/keywords, German, concise (ADR-0019). `writing-style.md` and `positive-exemplars.md` relocated to `cv/` (#615). _Avoid_: profile (unqualified), bio, CV Profile (retired), `domain-fit.md` (retired), `self-description.md` (retired), `match-criteria.md` (retired).

### CV authoring

**CV Slot-Map**: Per-listing `<app_dir>/cv.tex` by `/write-cv`, consumed by `compile-cv` (ADR-0023). `^%% SLOT: <name>$` markers, raw TeX bodies. Thirteen slots: `recipient_company/name/street/zip_city`, `opening`, `cover_intro/pivot/fit/closing`, `resume_berufserfahrung/ausbildung/projekte`, `skills_block` (mechanically assembled from **Skill Group** pool — ADR-0025). Listing-invariant content in **Facts**. _Avoid_: cv document (`cv_template.tex` is the document).

**CV Skeleton**: Format-by-example at `<settings-dir>/cv-template/cv_skeleton.tex` (ADR-0023, relocated ADR-0035). Package-shipped, refreshable. `/write-cv` reads from settings dir, injects into prompt. _Avoid_: cv template (overloaded with `cv_template.tex`).

**Facts**: Listing-invariant data at `<settings-dir>/user-info/cv/facts.tex` (ADR-0023, ADR-0024, ADR-0027). Defines `\myFirstname`, `\myFamilyname`, `\myCity` plus `\PersonalInfo`, `\Languages`, `\Hobbies` presentation macros. `cv_template.tex` `\input`s via `\CvDataDir/facts`. _Avoid_: identity, contact (both retired filenames); per-field raw defs retired (folded into `\PersonalInfo`).

**Content Pool**: CV item macros at `<settings-dir>/user-info/cv/content_pool.tex` (ADR-0024). Each `\newcommand{\itemFoo}{...}` selected into `resume_*` slots. Per-item metadata: `always:`, `group:`, `relevance:`. Sections from `% ===== <name> =====` block headers. _Avoid_: item pool, CV pool.

**Skill**: Hard-skill item from `skills.md` in **Triage Profile** dir (#615, relocated from `search-terms/`). Dual-consumed (ADR-0025): `prompts.py` harvests flat list (attributes stripped inline) for judge `{SKILLS}` slot; `/write-cv` reads full structure for `skills_block`. Judge-only — **Domain Pre-Filter** no longer reads it (ADR-0013). Optional `{always}` attribute (within-group floor). _Avoid_: keyword (when matching).

**Skill Group**: H2 heading in `skills.md` (ADR-0025) — also `\cvitem{<group>}{...}` label. Unit of LLM selection for `/write-cv`: always-groups render; others picked by relevance. File order = render order. Empty groups collapse silently. _Avoid_: skill category, section (overloaded).

**Agent Skills**: Claude-Code agent workflows under `<cwd>/.claude/skills/` (ADR-0035). Four dirs: `_shared/`, `analyse-listing/`, `iterate-cv/`, `write-cv/`. Source of truth in `src/application_pipeline/templates/claude/skills/`. Refresh overwrites package-owned dirs; user-added dirs survive. _Avoid_: subagents, prompts (overloaded).

**Keyword**: Search term from `SearchTerms.KEYWORDS` (ADR-0021). Distinct from **Skill** and **Negative Keyword**. _Avoid_: skill (when querying).

**Negative Keyword**: Entry in `SearchTerms.NEGATIVE_KEYWORDS` (ADR-0021). Case-insensitive substring match in title only (ADR-0013). No length validation, no rescue. _Avoid_: blacklist, exclusion.

**Listing ID**: Auto-increment integer, primary key in `seen.json` and `extracts.json`. Assigned at first `is_seen` miss. Next ID derived as `max(existing IDs) + 1` on store load. URLs stored as `urls: [...]` list (most-recent-first) inside the record, with a reverse index for URL-tier dedup. `PositionStub` does not carry the ID — it is a parser-layer concept. The dedup layer assigns the ID; `RunScopedSeenResult` carries it downstream. `mark_*` methods take `listing_id: int`. _Avoid_: URL key, url id.

**Match Verdict**: Judge output per winner — `{id, rank: 1..5}` where `id` is the **Listing ID** integer (ADR-0032). Judge prompt uses the real integer ID, not URLs. Judge ranks only; Card's Summary is from classify-time. _Avoid_: score, rating, tier (retired).

**Rank**: Integer 1..5 assigned by the judge. Not a score, not a tier. _Avoid_: tier, score, position (overloaded).

**Pool**: Implicit set of `status == matched` listings re-discovered in the current run, keyed by **Listing ID**. Computed per-run from `seen.json` + today's parse output. Re-entry costs nothing (URL-tier dedup short-circuits). Enter: classified `matched`. Exit: judge picks it (`selected_by_judge`, extract deleted) or no longer re-discovered. No cap, no TTL in v1. _Avoid_: queue, candidate set — not the **Daily Top-5**.

**Daily Top-5**: ≤5 **Positions** the **Match Judge** returns, drawn from today's **Pool**. Each winner gets `mark_selected_by_judge(listing_id)` after Card append+fsync. _Avoid_: top-N, shortlist.

**Relevance Classifier**: Single-check LLM gatekeeper (#615, supersedes ADR-0034 three-check): domain fit + hard exclusions from `{GATE_CRITERIA}`. No candidate profile, no skill-floor check — stretch/experience judgment deferred to **Match Judge**. Emits **Header** + **Summary** on pass. `classify_relevance(items) -> list[RelevanceVerdict | None]`; output `<verdict id="N">{...}</verdict>` per item. Up to `claude_classify_batch_size` (default 10) listings per `claude -p` call (ADR-0046, supersedes ADR-0028). Single accumulator thread fills batches sequentially; full batches dispatched to parallel pool of N workers (ADR-0031, ADR-0047) that run only the LLM call. Final partial batch flushed when all parsers signal done. Unparseable verdicts → `None`, listing unmarked, re-discovered next run. Combined prompt via stdin (ADR-0029). Runs only on candidates surviving all non-LLM gates. _Avoid_: filter, gate.

**LLM Enricher**: Owns the classify LLM call. Receives `(listing_id, stub, body)` via classify queue (ADR-0042), runs `classify_relevance`, runs post-LLM **Freshness Gate** arm, writes Card on `matches=True` keyed by **Listing ID**. No httpx client, no body strip. Redirect-following lives in `parsers/body_fetch.py`. _Avoid_: enricher (unqualified), extractor.

**Freshness Gate**: Drops temporally invalid candidates (ADR-0018, ADR-0032, ADR-0038). `admit(stub, *, gate_arm, deadline=None) -> bool` at three sites: post-discover, post-enrich, post-LLM. Drops when `posted_date` exceeds `MAX_LISTING_AGE_DAYS` or `deadline < anchored_today`. `None` = no signal. Writes `expired`; on `matched → expired` deletes extract. Parser-thread drops summed into one `freshness` counter (ADR-0043). _Avoid_: staleness filter, expiry gate.

**Content Gate**: Drops empty or placeholder body post-enrich (ADR-0030, ADR-0042). Minimum 100 chars. No `seen.json` mark — retried next run. `admit(stripped_body, stub) -> bool`. Reason enum `{passed, empty_body, too_short}`. Effective customer: non-native-enrich parsers (ADR-0040). _Avoid_: empty-body filter.

**Domain Pre-Filter**: Title-only blacklist (ADR-0013). Substring match on **Negative Keywords**, case-insensitive. `admit(stub) -> bool`. Drops write `out_of_domain`. Log component `pipeline_prefilter`. _Avoid_: filter, gate, classifier.

**Gates Bundle**: _Retired as single call site_ per ADR-0042. Non-LLM gates invoked individually by parser thread. Pre-enrich: Freshness → Dedup → Pre-Filter. Post-enrich: Freshness → Content Gate. _Avoid_: gate runner, filter chain.

**EnrichResult**: From `Parser.enrich(stub)` (ADR-0038). Carries updated `stub`, `body: str`, `mode: Literal["native", "fallback"]`. Native-enrich parsers always `"native"`; `"fallback"` = scrape-is-primary (ADR-0040). On failure, `EnrichFailedError` — stub skipped, no `seen.json` write (ADR-0039). _Avoid_: enriched stub, enrich payload.

**Quota Wall**: Shared coordination for the parallel classify pool (ADR-0031). `raise_wall(reset_time)`, `wait_if_blocked()`, `is_active()`. `threading.Condition` + `reset_time`. One `event=quota_sleep` row per wall raise. _Avoid_: rate limiter, barrier.

**Match Judge**: Picks **Daily Top-5** from **Pool**. Single `judge_top_n(candidates)` per run. Takes `list[JudgeCandidate]` (**Listing ID** + Header + Summary), returns ≤5 `{id, rank}` where `id` is the integer **Listing ID**. Consumes `{CANDIDATE_PROFILE}` + `{SKILLS}` (#615). No gate criteria — listings already passed the classifier. On non-quota error → Failure Report, no daily file. _Avoid_: scorer.

### Deduplication and run state

**Deduplication**: Four-tier (ADR-0044): in-run `run_hit` (absorbs Cartesian overlap) plus persistent **Deduplication Store** with URL-tier (reverse index `{url → Listing ID}`), exact-tuple-tier `(company_lc, title_lc, location_lc)`, and fuzzy-tuple-tier (token-subset, min 4 tokens, shorter ⊂ longer, gender markers stripped). Tuple/fuzzy fire only when all three fields non-`None`. Tuple/fuzzy hits on existing records append the new URL to the record's `urls` list (no alias records). `is_seen` writes in-memory `pending` entry on miss, assigns **Listing ID** (populates indexes immediately, no persist). Checked at two points: post-discover and post-enrich (backfilled fields). `is_seen` returns `RunScopedSeenResult` carrying `listing_id: int`: `url_hit`/`tuple_hit`/`fuzzy_hit` skip; `judge_pending` routes to Pool (includes tuple/fuzzy hits on `matched` entries — first in run updates URL/title); `run_hit` skips within-run repeats; `miss` processes. Freshness-dropped listings: orchestrator calls `mark_expired(listing_id)` to populate indexes.

**Dedup status enum** (ADR-0014/ADR-0034/ADR-0044):
- `out_of_domain` — Pre-Filter or Classifier rejection. Terminal-skip.
- `matched` — classifier `matches: true`, extract written. In the **Pool**. Tuple/fuzzy hit returns `judge_pending` (first in run) or `run_hit`.
- `selected_by_judge` — judge picked, Card appended+fsynced. Extract deleted. Suppresses within `DEDUP_COOLDOWN_DAYS` (default 30); after cooldown decays to `judge_pending` (re-enters Pool, updates URL/title).
- `expired` — Freshness Gate drop or `matched → expired`. Deletes extract on `matched → expired`. Suppresses within `DEDUP_COOLDOWN_DAYS`; after cooldown decays to `miss` (re-enters pipeline). `status_last_changed` refreshed on each freshness re-drop.
- `pending` — in-memory only, not persisted. Claimed by first `is_seen` miss; overwritten by classify worker's `mark_*`.
- ~~`enrich_failed`~~ — _retired_ (ADR-0039). URLs stay unrecorded, retried next run.
- ~~`external_redirect`~~ — _retired_ (ADR-0032). Redirects followed silently.

**Error semantics for `mark_*`** (single-writer Pi):
- **Pre-filter drop** → `mark_out_of_domain(stub)`. No LLM cost.
- **Classifier non-quota error** → listing NOT marked; orchestrator continues; retry next tick. Malformed stashed to `failures/malformed/`.
- **Classifier `ClaudeUsageLimitError`** → **Quota Wall** raises; workers sleep until reset+2min. Cron-anchored day handles midnight.
- **Judge non-quota error** → no daily file, Failure Report, Pool intact.
- **Body fetch failure** → stub skipped, URL unrecorded (ADR-0039). Native-enrich: 401/403/5xx/3xx/JSON decode → Failure Report + parser dead; 404/400/422/retries-exhausted → silent skip.
- **Oversized/malformed LLM output** → stashed, no `seen.json` write, retried next run.

State at `<settings-dir>/.runtime-data/seen.json` (ADR-0037; synced via Syncthing, ADR-0002). Shape: `{listing_id: {urls: [...], company_lc, title_lc, location_lc, status, status_last_changed}}` — keyed by **Listing ID** integer. `urls` list ordered most-recent-first; no alias records. On-load migration from legacy URL-keyed format (collapses alias chains into `urls` lists, assigns integer IDs). `DeduplicationStore` exposes four methods taking `listing_id: int`: `mark_out_of_domain`, `mark_matched`, `mark_selected_by_judge`, `mark_expired`. Single-writer (ADR-0002); internal lock covers concurrent classify/judge writes. Config: `DEDUP_COOLDOWN_DAYS: int = 30` (ADR-0044). _Avoid_: duplicate filtering, URL filtering.

### Display

**Card**: Fixed markdown block per winner (ADR-0033): `# **{rank}:** {title}` (blank line) metadata lines + URL (blank line) `{Summary}` (blank line) `---` (blank line) `{Raw Description}` (blank line) `---`. _Avoid_: card template, headline.

**DailyResultsFile**: Module that owns both rendering and durability for the **Daily Results File** artefact. Card shape is hardcoded inside this module per ADR-0033. Write durability is `write + flush + fsync` per ADR-0015. Public interface: `ensure_initialized()` (mkdir parent dir) and `commit(*, rank, header, summary, url, body)` (render Card then append to file). `OSError` is wrapped as `ResultsFileError` at the public interface edge. Path bound at construction. _Avoid_: renderer, formatter, results file manager, writer, output manager.

**Atomic Write Helper**: `write_atomic(path, payload: bytes)` — crash-safe atomic overwrite via `.tmp` sibling + `os.write` → `os.fsync` → `os.replace`. Used by **Deduplication Store** and **Extract Store**. _Avoid_: persistence helper, file writer (overloaded with **DailyResultsFile**'s append).

### Observability

**Log Artifacts**: Under `<settings-dir>/.runtime-data/logs/` (ADR-0037), laid out by reader (ADR-0012), layer-subdirs (ADR-0036). Four types: `<layer>/<comp>.events.jsonl`, `lifecycle.jsonl`, `run.log`, `<layer>/<comp>.transcripts.jsonl`. Component identifiers stay layer-prefixed (`parser_`, `llm_`, `pipeline_`) in all data — subdir replaces prefix only on the filename. `llm_classify_relevance` for classifier, `llm_judge_match` for judge. _Avoid_: log file (unqualified).

**Run Log**: Per-run instance writing **Log Artifacts**. Constructed once from `cfg.logs_dir`; threaded into every component. Safe to share across threads — each method opens, writes, closes per call. _Avoid_: log writer, logger (overloaded).

**Status Display**: Live in-process progress view (ADR-0043). `StatusDisplay` Protocol: `RichStatusDisplay` (tty) and `PlainStatusDisplay` (cron). Uniform counters: queued/dropped/forwarded. Each parser gets parser row + gates row (non-zero drops only). `llm_classify_relevance` row adds `malformed` + `classifying` counters; `queued` shows current depth. Judge: terminal message only. _Avoid_: progress bar, TUI, dashboard.

### Sources & extraction

**Source**: A `SourceEntry` in `Config.SOURCES` carrying a **Parser Type**. Source = config entry; Parser = module. One-to-one in v1. _Avoid_: site, board.

**ParserHttp**: Per-parser HTTP layer wrapping `httpx.Client` with pacing (0.5s), retry-with-backoff on `{429, 502, 503, 504}` (max 3), typed error taxonomy. Used for discover, `body_fetch.py` fallback, and native enrich paths. _Avoid_: http client, fetcher.

**Parser**: Module in `src/application_pipeline/parsers/`. Two methods (ADR-0038): `discover(query) -> Iterable[PositionStub]` and `enrich(stub) -> EnrichResult`. Context manager owning `httpx.Client`; no shared mutable state (ADR-0004, amended ADR-0042 — threads now do `enrich()` I/O inline). Each thread runs: `discover → Freshness → Dedup → Pre-Filter → enrich → Freshness → Content Gate → classify queue` (ADR-0042). `has_native_enrich = True` → exclusive native path, no fallback (ADR-0040). `body_selector` is parser-private. In-parser dedup retired (ADR-0041). **Keyword-match invariant** (ADR-0013): every stub matches its query keyword in the title. _Avoid_: scraper, fetcher.

**Position Stub**: Result of `discover()` — `url, title, source, posted_date?, deadline?, company?, location?`. Required: url, title, source. Optional fields drive post-discover gates and Header pre-fills. `deadline` feeds the **Freshness Gate** `deadline` arm (`deadline < anchored_today` → drop). `enrich()` may back-fill optional fields. _Avoid_: preview, summary.

**External Redirect**: _Retired_ per ADR-0032. Redirects followed silently in `parsers/body_fetch.py`. _Avoid_: do not reintroduce.

**Parser Type**: String on `SourceEntry` mapping to parser filename. _Avoid_: adapter.

**Location**: Sealed `City(name) | Remote` passed via `ParserQuery.location`. _Avoid_: place, geo.

**City**: `City(name)` arm. Normalized via `normalize()`. No central catalog. _Avoid_: town, place.

**Remote**: `Remote` arm. Per-parser `remote_wire()`. _Avoid_: homeoffice (when the type variant is meant).

**Location Coverage**: Per-parser Protocol — `serves(name)`, `to_wire(name)`, `serves_remote`, `remote_wire()`. Validated at load time (ADR-0009). _Avoid_: location map, slug table.

**LLM Extractor**: Protocol: `classify_relevance(items: list[ClassifyItem]) -> (list[RelevanceVerdict | None], CallUsage)` and `judge_top_n(candidates) -> (list[MatchVerdict], CallUsage)`. `RelevanceVerdict` = `{matches, header?, summary?}` (ADR-0034). `MatchVerdict` = `{id: int, rank}` where `id` is the **Listing ID**. Production implementation: `ClaudeExtractor` via `claude -p` subprocess (ADR-0029 wire shape). Models: `haiku` classifier, `haiku --effort medium` judge (ADR-0010). Tags: `<verdict>`/`<verdicts>` via **Agent Output Protocol** (ADR-0010). _Avoid_: LLM, model (unqualified).

**Agent Output Protocol**: `extract_json_block(text, tag) -> Any` + `AgentOutputProtocolError` (ADR-0010). Finds rightmost closing tag, walks back, strips optional fence, `json.loads`. Fallback: when tags absent, attempts bare-JSON extraction from markdown code fence; logs `protocol_fallback` on recovery. _Avoid_: output parser, response handler.

**Pagination**: Page fetches until source returns empty. No dedup-driven early-stop (ADR-0011). `max_results` retired (ADR-0041). _Avoid_: paging.

### Invocation

Distributed via PyPI (ADR-0020). Install: `.venv/bin/pip install application-pipeline`, `init`, `bash setup/cron-install.sh`. Home dir hardcoded to `<cwd>/application-pipeline/` (ADR-0022).

- `application-pipeline run` reads `<cwd>/application-pipeline/config.py`.
- `application-pipeline init [--refresh]` seeds into `<cwd>/application-pipeline/`.
- `application-pipeline compile-cv <app_dir>` compiles per-listing draft from slot-map. Windows paths normalized to POSIX for `\def\CvDataDir{...}` (ADR-0024, ADR-0023).

Fail loud-and-fast (exit 2) if `config.py` missing. Cron weekdays 00:30 (ADR-0017). Each tick: `pip install --upgrade` (×2), `init --refresh`, `run`. **`init --refresh`** overwrites `setup/*.sh`, `cv-template/`, package-owned `.claude/skills/` dirs (ADR-0035), deletes `layout.py` (ADR-0033) and obsolete `skills/` dir (ADR-0035). Seeds-if-missing for `config.py`, `user-info/*`, `.gitignore` (ADR-0037). Never touches `.runtime-data/`. Templates organised into routing buckets (ADR-0035). No flock — single-writer on the Pi, overlapping ticks cannot occur. `pycastle/` in this repo is an unrelated RALPH Loop plugin used to *build* this project.

## Relationships

- A listing reaches a **Daily Results File** by passing (in order, ADR-0038/0042/0044): parser thread — **Freshness Gate** (stub; drop → `mark_expired`), **Dedup** (post-discover), **Domain Pre-Filter** (title), **Parser.enrich()**, **Dedup** (post-enrich, backfilled fields), **Freshness Gate** (post-enrich), **Content Gate** (body) → classify queue → **Relevance Classifier** LLM call → post-LLM **Freshness Gate** → **Match Judge** picks **Daily Top-5**.
- The **Pool** is `{url ∈ discovered_today : seen.json[url].status == matched}`.
- The **Match Judge** runs once per run, takes Header + Summary + Candidate Profile + Skills, returns ≤5 `{id, rank}`.
- **Triage Profile** reaches LLM via two disjoint paths (#615): `{GATE_CRITERIA}` → classifier only; `{CANDIDATE_PROFILE}` + `{SKILLS}` → judge only. `NEGATIVE_KEYWORDS` reaches **Domain Pre-Filter** directly.
