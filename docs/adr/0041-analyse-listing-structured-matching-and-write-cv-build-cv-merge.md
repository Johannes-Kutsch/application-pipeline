# analyse-listing replaces grilling with structured matching; write-cv absorbs build-cv

`/analyse-listing` no longer grills the user. It extracts requirements from the listing, matches them against the **Bullet Library**, and finalizes exactly 4 bullets collaboratively. `/write-cv` absorbs `/build-cv`; both `/build-cv` and `/iterate-cv` are retired.

## Why

- Grilling was open-ended and slow; structured requirement matching is faster and produces a directly actionable output (4 finalized bullets).
- Bullet selection and drafting belonged in `/analyse-listing` — it has the listing context and the user's attention before the CV is written.
- Skills run in one session; session-context handoff eliminates the need for a persistent `analysis.md` file and all its downstream dependencies.
- `/build-cv` was a thin step after `/write-cv` with no independent use case; merging reduces the number of commands.
- `/iterate-cv` had no remaining purpose once `/build-cv` was merged and `analysis.md` was retired.

## Consequences

- `/analyse-listing` write-rules: `bullet-library.md` and `candidate-profile.md`. No file written to the application folder.
- `/write-cv` write-rules: `cv.tex` and `cover-patterns.md`. Reads 4 finalized bullets from session context; includes compile and overflow loop (formerly `/build-cv`).
- `analysis.md` no longer exists. References in CONTEXT.md, ADR-0013, and ADR-0026 updated.
- `build-cv/` directory removed from template tree (ADR-0026).
- `gate-criteria.md` updates dropped from `/analyse-listing`; only `candidate-profile.md` receives generalizable signals from new bullet drafting.
