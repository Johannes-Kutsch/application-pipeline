# Daily results file replaces the trio; FILE_HEADER and Run Divider retired

The **Results File** stops being a rolling trio (`green.md`/`amber.md`/`red.md`). It becomes one dated markdown file per calendar day under `data/results/YYYY-MM-DD.md`, holding exactly the **Daily Top-5** **Cards** in **Rank** order (or fewer if the pool was thin). The hardcoded **FILE_HEADER** is dropped — daily files have no preamble. The **Run Divider** is dropped — daily-file presence-or-absence is the operator heartbeat; metrics move to `pipeline_orchestrator.events.jsonl` per ADR-0018.

Companions: ADR-0020 (tier retired), ADR-0022 (extracts/pool), ADR-0023 (quota), ADR-0024 (one run per day).

## Why

- **A dated file per day is the smallest artifact that answers the applicant's question.** "What are today's best 5 matches?" — one file containing exactly that. The trio answered a different question ("everything ever judged, bucketed by tier").
- **Per-tier reset semantics were a workaround for an accumulating-file problem that no longer exists.** Daily files self-reset by date.
- **The Run Divider was a multi-fire-per-day heartbeat.** Under one run per day (ADR-0024), daily-file presence answers the same question; the metrics belong in operator-only artifacts.
- **Syncthing semantics simplify.** Daily files are write-once on the Pi and never re-touched after the day's run.

## Considered alternatives

- **Single rolling `top5.md` overwritten daily.** Rejected: loses history.
- **Daily file plus a permanent `archive.md`.** Rejected: dated files *are* the archive.
- **Run Divider as end-of-day metadata block.** Rejected: conflates applicant-facing and operator-facing.
- **Date by wall-clock-at-write.** Rejected: a run sleeping through a quota window (ADR-0023) needs an unambiguous logical-day rule. Cron-anchored (start-of-run date) gives one file per cron fire.

## Consequences

- **Results File Manager** module surface becomes `ensure_initialized(path)` (just `mkdir`; no header write) and `append(path, rendered_block)` (write + flush + fsync). Path: `data_dir / "results" / f"{cron_anchored_date}.md"`.
- **FILE_HEADER** removed from the package. **Run Divider** rendering removed.
- **Orchestrator end-of-run row** lands in `pipeline_orchestrator.events.jsonl` as `event=run_complete` with the fields the divider carried.
- **Cron-anchored logical day**: orchestrator captures `run_started_at`; `date_for_file = run_started_at.date()`. Same path regardless of sleep duration / midnight crossing.
- **`<5` candidates**: file carries however many cards exist. **`=0`**: no file written. No empty file, no placeholder.
- **Failure semantics**: judge failure → no daily file; **Failure Report** in `data/failures/<timestamp>.md` (ADR-0010). Classifier partial failure → run continues to judge on whatever classified successfully.
- **Migration**: trio and `.seen.json` are wiped on first deployment (per ADR-0024). Historical kept-records do not survive — accepted as not load-bearing.
