# application-pipeline

A personal job-discovery and triage pipeline. It fetches listings from configured sources, filters
out noise, classifies each position's relevance with Claude, accumulates a rolling pool of in-domain
candidates, and emits one dated results file per day ranking the top five matches.

## Why

- **Automated discovery** — parsers walk configured job boards each morning so you don't have to.
- **Noise reduction** — the Domain Pre-Filter drops title-level mismatches cheaply, before any LLM
  cost is incurred.
- **Freshness control** — the Freshness Gate discards listings beyond your configured age ceiling and
  any past their deadline.
- **Structured ranking** — the Match Judge scores each in-domain candidate against your skills and
  match criteria, returning an explicit rank with matched/missing skill lists.
- **One file per day** — a dated markdown file with up to five cards lands in your settings folder
  and propagates via Syncthing if configured.

## The pipeline

Each cron tick walks every configured Source × Keyword × Location combination, then routes each
Position through the following phases in order:

1. **Parsers** — `discover()` yields cheap Position Stubs; `enrich()` fetches the detail page and
   returns a full Position with raw description.
2. **Deduplication** — skips URLs seen in previous runs (exact URL or company/title/location tuple),
   routing known in-domain positions directly back into the Pool.
3. **Domain Pre-Filter** — drops any Position whose title matches a Negative Keyword (configured in
   `config.py` as `NEGATIVE_KEYWORDS`).
4. **Freshness Gate** — drops listings older than `MAX_LISTING_AGE_DAYS` or past their deadline.
5. **Relevance Classifier** — a batched Claude call decides `in_domain: true/false`; in-domain
   positions receive a Structured Extract and enter the Pool.
6. **Match Judge** — a single Claude call at end-of-run picks the Daily Top-5 from the Pool,
   returning a Match Verdict (rank, matched skills, missing skills, summary) for each winner.
7. **Daily Results File** — the five Cards are written to `<settings-dir>/results/YYYY-MM-DD.md` in
   rank order.

## Getting started

- **[docs/usage.md](docs/usage.md)** — installation, CLI reference, and per-file editing guide for
  the settings folder.
- **[docs/cron-setup.md](docs/cron-setup.md)** — unattended operation via cron, flock semantics,
  optional Syncthing, migration from a legacy layout, and PyPI release procedure.

## Acknowledgements

Install flow, cron wrapper shape, and flock-based serialisation modelled on
[pycastle](https://github.com/Johannes-Kutsch/pycastle).
