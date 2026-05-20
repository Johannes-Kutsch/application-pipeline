# Cron fires once per day; deployment wipes `.seen.json` and the old trio

Pi cron for `application_pipeline` reduces from multi-fire-per-day to a single fire per day. Each fire is one logical run — parsers walk every **Source**, classifier processes everything `not_classified`, judge picks the **Daily Top-5**, daily file is written, run ends. Deployment of the v2 refactor wipes the existing `.seen.json`, `data/extracts.json` (if any), and `data/results/{green,amber,red}.md`. No automatic state migration is provided. Historical kept-records do not survive.

Companions: ADR-0020, ADR-0021, ADR-0022, ADR-0023.

## Why

- **One judge call per day means one cron fire per day is sufficient.** Multi-fire would either re-issue the judge call (progressively filling the daily file) or fire-but-skip-the-judge. Either is strictly more complex than "once a day, one file, five cards."
- **Parser freshness is no longer a forcing function.** Under daily output, "caught at 9am" vs "caught at 5pm" both surface in *tomorrow's* file. Intra-day cadence is invisible to the applicant.
- **Quota interaction is cleaner.** ADR-0023's sleep has unbounded duration; with multi-fire cron, a sleeping run blocks several subsequent fires; with once-per-day, at most one missing daily file before the operator notices.
- **Migration not worth the engineering cost.** Auto-rename, wipe, or hybrid (preserve terminal statuses, wipe transitional) were considered. Applicant chose **wipe**. The "preserve don't-re-classify-noise" win of hybrid costs perhaps one day of classifier tokens to recover organically — not worth the migration code, schema-version handling, or load-time branch.
- **Cron timing is a Pi-side knob, not `Config`.** Recommended: early-morning hour (e.g. 03:00 local) so the daily file is on the laptop when the applicant wakes up. Setup-doc recommendation, not a code contract.

## Considered alternatives

- **Multi-fire cron, one judge per day, gated on "no file yet" today.** Rejected: small cost saved (parser HTTP amortized) vs orchestrator-level complexity (per-fire idempotence, partial pool state across fires).
- **Multi-fire with `selected_by_judge` flips mid-day.** Rejected: up to `5 × cron_fires` cards per day; re-introduces green-burial in a new shape.
- **Auto-migrate statuses + backfill extracts.** Rejected: effectively a wipe-with-extra-steps (extracts must be re-classified anyway).
- **One-shot CLI subcommand for migration.** Rejected: same conclusion as auto-migrate.

## Consequences

- **Cron line**: e.g. `0 3 * * * /home/pi/application-pipeline/current/.venv/bin/python -m application_pipeline`, wrapped by `flock` and the deploy wrapper (ADR-0009).
- **Deployment includes a state-wipe step** in `docs/pi-setup.md` or the deploy script: `rm data/.seen.json data/results/green.md data/results/amber.md data/results/red.md`.
- **No load-time branch for legacy statuses.** Loader assumes the new enum (`not_classified | out_of_domain | in_domain | selected_by_judge | expired | enrich_failed | external_redirect`). A legacy value raises with a wipe-instruction hint.
- **First-day-after-cutover**: parsers walk every source as if nothing existed; classifier processes the full first-run volume (potentially hitting quota — ADR-0023 handles it). Judge picks 5. By day 3 the pool is steady-state.
- **Operator signal**: `data/results/YYYY-MM-DD.md` existence is primary; absence → next-cheapest signal is `pipeline_orchestrator.events.jsonl` newest row timestamp.
- **`Config` schema unchanged by this ADR** — cron and wipe are operational.
