# Layout as a user-editable Python module in the synced folder

`settings/layout.py` lives alongside `config.py` in the synced folder (see ADR-0011). Plain Python: tier emoji/color (retired per ADR-0020), named placeholder groups, full `CARD_TEMPLATE` / `HEADLINE_TEMPLATE`. Loaded at runtime by the same `load_user_module` machinery as **Config**, validated into a frozen `Layout` dataclass, consumed by a pure **Renderer** via `str.format_map`.

When `LAYOUT` is unset, the loader defaults to `"layout.py"` next to `config.py` and **errors if missing** — matching how `USER_INFO_DIR` resolves. An explicit `LAYOUT = None` selects the built-in `layout.default()` stub (reserved for tests).

## Why

- **User iterates on layout often, package code rarely.** Field tweaks, emoji swaps, visual hierarchy — should not require editing `src/`. Co-location with `config.py` matches the edit-restart-see workflow.
- **`str.format_map` + Python constants is the lightest tool.** No Jinja, no DSL. Named placeholder groups (`PLACEHOLDER_GROUPS = {"meta": (" · ", ["location", "url"])}`) handle the dangling-separator problem.
- **Loader plumbing already exists.** `load_user_module` (extracted from Config Loader) handles `importlib.util`, missing-attr checks, typed errors. `LayoutError` and `ConfigError` share `UserSettingsError`.
- **Renderer stays pure.** Layout passed in explicitly (`render(position, verdict, layout)`); no module-level state.
- **Auto-discovery footgun closure.** A user with a valid `layout.py` who forgot to add `LAYOUT = "layout.py"` would silently get the stub. Pattern-symmetry with `USER_INFO_DIR` + fail-loud on missing closes the gap. Explicit `LAYOUT = None` keeps the stub reachable.

## Consequences

- Renderer takes a `Layout` argument; orchestrator loads once at startup and threads it through.
- `LayoutError` joins `ConfigError` under `UserSettingsError`.
- Typo in placeholder name raises `KeyError` from `str.format_map` at render time — fail-loud is intentional.
- Validation at startup: `layout_loader` checks every `placeholder_groups` entry references a known field; missing/invalid `layout.py` fails before the first fetch.
- Per ADR-0020, the placeholder set excludes `emoji`/`color`/`tier` and includes `rank`.
