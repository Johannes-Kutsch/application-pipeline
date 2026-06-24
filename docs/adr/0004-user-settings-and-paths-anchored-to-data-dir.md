# User settings in a flat settings directory; all paths anchored to it

**Config** (`config.py`) at `<cwd>/application-pipeline/` (ADR-0017). All output/state paths derived from it. Pipeline-written paths under `.runtime-data/` (ADR-0028).

**Amendment:** no path override knobs. `USER_INFO_DIR`, `LAYOUT`, prompt path knobs all retired into `_REMOVED_FIELDS`.

## Why

- One mental model: settings folder = where to edit and read results. Templates inside the package via `importlib.resources`. No override surface.

## Consequences

- `init` writes via `importlib.resources`. `init --refresh` overwrites package-owned scaffolding only.
