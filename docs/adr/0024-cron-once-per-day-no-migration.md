# Cron fires once per day at 00:30 local; deployment wipes prior state

Cron for `application_pipeline` fires once per calendar day. Each fire is one logical run — parsers walk every **Source**, classifier processes everything `not_classified`, judge picks the **Daily Top-5**, daily file is written, run ends. The seeded `cron-install.sh` (ADR-0027) hardcodes `30 0 * * *` — 00:30 local, chosen to run before any pycastle cron tick at 01:00 local on the same host. Deployment of the v2 refactor wipes the existing `.seen.json`, `extracts.json`, and `results/{green,amber,red}.md`. Deployment of the PyPI-distribution change (ADR-0027) additionally retires the legacy `~/application-pipeline/{repo,releases,current,data}/` layout — the operator removes those directories and re-runs `application-pipeline init <dir>` + `bash setup/cron-install.sh`. No automatic state migration is provided. Historical kept-records do not survive.

## Why

- **One judge call per day means one cron fire per day is sufficient.** Multi-fire would either re-issue the judge call (progressively filling the daily file) or fire-but-skip-the-judge. Either is strictly more complex than "once a day, one file, five cards."
- **Parser freshness is no longer a forcing function.** Under daily output, "caught at 9am" vs "caught at 5pm" both surface in *tomorrow's* file. Intra-day cadence is invisible to the applicant.
- **Quota interaction is cleaner.** ADR-0023's sleep has unbounded duration; with multi-fire cron, a sleeping run blocks several subsequent fires; with once-per-day, at most one missing daily file before the operator notices.
- **Migration not worth the engineering cost.** Auto-rename, wipe, or hybrid (preserve terminal statuses, wipe transitional) were considered. Applicant chose **wipe**. The "preserve don't-re-classify-noise" win of hybrid costs perhaps one day of classifier tokens to recover organically — not worth the migration code, schema-version handling, or load-time branch.
- **00:30 local sits before pycastle's 01:00 fire** on the shared host, so the pipeline's daily file lands before agentic coding work begins competing for CPU and Claude quota.

## Considered alternatives

- **Multi-fire cron, one judge per day, gated on "no file yet" today.** Rejected: small cost saved (parser HTTP amortized) vs orchestrator-level complexity (per-fire idempotence, partial pool state across fires).
- **Multi-fire with `selected_by_judge` flips mid-day.** Rejected: up to `5 × cron_fires` cards per day; re-introduces green-burial in a new shape.
- **Auto-migrate statuses + backfill extracts.** Rejected: effectively a wipe-with-extra-steps (extracts must be re-classified anyway).
- **One-shot CLI subcommand for migration.** Rejected: same conclusion as auto-migrate.
- **Configurable cron hour via env-var.** Rejected: one-line `crontab -e` edit covers any user who wants a different hour; no need to keep an installer knob alive.

## Consequences

- **Cron line** (written by `cron-install.sh` per ADR-0027): `30 0 * * * <repo>/setup/cron.sh >> <logs_dir>/cron.log 2>&1 # application-pipeline:<repo>`.
- **Deployment includes a state-wipe step** documented in `docs/cron-setup.md` (or the deploy script): `rm -rf ~/application-pipeline-old-layout/` (the legacy `repo/`, `releases/`, `current/`, `data/` tree if migrating from the pre-PyPI deploy), then run `application-pipeline init <dir>` and `bash setup/cron-install.sh`.
- **No load-time branch for legacy statuses.** Loader assumes the new enum (`not_classified | out_of_domain | in_domain | selected_by_judge | expired | enrich_failed | external_redirect`). A legacy value raises with a wipe-instruction hint.
- **First-day-after-cutover**: parsers walk every source as if nothing existed; classifier processes the full first-run volume (potentially hitting quota — ADR-0023 handles it). Judge picks 5. By day 3 the pool is steady-state.
- **Operator signal**: `results/YYYY-MM-DD.md` existence is primary; absence → next-cheapest signal is `pipeline_orchestrator.events.jsonl` newest row timestamp.
- **`Config` schema unchanged by this ADR** — cron and wipe are operational.
