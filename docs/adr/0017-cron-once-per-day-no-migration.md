# Cron fires weekdays at 00:30 local; deployment wipes prior state

Cron fires once per **weekday** (Mon–Fri). Seeded `cron-install.sh` (ADR-0020) hardcodes `30 0 * * 1-5`. Deployment of the v2 refactor wipes `.seen.json`, `extracts.json`, and the old trio. No automatic state migration.

## Why

- One judge call per day means one cron fire per day is sufficient.
- Quota interaction is cleaner — at most one missing daily file before the operator notices.
- Migration not worth engineering cost. Applicant chose wipe — the "preserve don't-re-classify-noise" win costs one day of classifier tokens, not worth migration code.
- 00:30 local sits before pycastle's 01:00 fire on the shared host.

## Consequences

- Cron line: `30 0 * * 1-5`. `cron.sh` is self-locating via `$(dirname "$0")/../..` (ADR-0022).
- No load-time branch for legacy statuses. A legacy value raises with a wipe-instruction hint.
- First-day-after-cutover processes all sources from scratch.
