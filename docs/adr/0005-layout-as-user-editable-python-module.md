# Layout as a user-editable Python module in `settings/`

> **Amended by [ADR-0023](0023-layout-auto-discovered-from-config-sibling.md):** `LAYOUT` defaults to `"layout.py"` next to `config.py` and errors if missing, instead of falling back silently to `layout.default()` when unset. An explicit `LAYOUT = None` selects the stub.

The cosmetic and structural choices for the **Results File** — tier emoji, tier color, named placeholder groups, the file header written on init, and the full `CARD_TEMPLATE` / `HEADLINE_TEMPLATE` — live in a `settings/layout.py` alongside `settings/config.py`. Loaded at runtime by the same machinery as **Config**, validated into a frozen typed `Layout` dataclass, consumed by a pure **Renderer** that substitutes placeholders via `str.format_map`.

## Why

- **The user iterates on layout often, package code rarely.** Adding/removing fields, swapping emoji, retuning the visual hierarchy is the kind of thing the user wants to do without editing files inside `src/application_pipeline/`. Putting layout next to `config.py` matches the existing edit-restart-see workflow.
- **`str.format_map` + plain Python constants is the lightest tool that does the job.** No Jinja dependency, no escape rules, no DSL. Multi-line strings + `{placeholder}` substitution covers every layout decision the user wanted to control. Named placeholder groups (`PLACEHOLDER_GROUPS = {"meta": (" · ", ["location", "language", "url"])}`) handle the dangling-separator problem without conditional templating.
- **Loader plumbing already exists.** `load_user_module` (extracted from the existing Config Loader for reuse) handles `importlib.util` exec, missing-attribute checks, and typed errors. Layout reuses it; future user-edited files would too.
- **Errors share a base.** `LayoutError` and `ConfigError` both subclass `UserSettingsError`. A caller catching the base gets a single recovery point for "user-edited file in `settings/` failed".
- **Renderer stays pure.** Layout is passed in as an explicit argument (`render(position, verdict, number, layout)`); no module-level layout state, no import-time side effects. Determinism preserved, tests stay trivial.

## Considered alternatives

- **Hardcode templates inside `renderer.py`.** Rejected: the user explicitly wants to redesign Card/Headline freely. The PRD's original "Out of Scope: configurable header template" was about *runtime* config (env vars, CLI flags); a developer-edited code constants file is compatible with that intent and addresses the actual workflow need.
- **Jinja2 templates.** Rejected: heavier dependency, Jinja's escape and whitespace rules are extra cognitive load, and conditional logic in templates is unnecessary once named placeholder groups handle the dangling-separator case.
- **TOML config.** Rejected: plain Python lets the user define groups as tuples-of-list, mix string and dict constants, and add `#` comments inline. TOML would force an extra schema and lose the comments-and-constants ergonomics.
- **Layout fields inside `Config`.** Rejected: layout is large (multi-line templates, multiple maps), Config is search parameters. Mixing them clutters `config.py` and gives the two unrelated concerns the same release cadence.
- **No `PLACEHOLDER_GROUPS`; one placeholder per field, author handles `None` manually.** Rejected: forces author to either accept dangling separators (`Hamburg ·  · <url>` when language is None) or write each variable-presence field on its own line. Groups are the smallest mechanism that solves this without a template engine.

## Consequences

- A second user-edited file in `settings/`. The user's surface grows by one file, but workflow matches `config.py` exactly.
- The Renderer must take a `Layout` argument. No module-level layout caching; the Orchestrator loads layout once at startup and threads it through.
- `LayoutError` joins `ConfigError` under `UserSettingsError`; the existing `config/loader.py` is refactored to use the shared `load_user_module` helper.
- Highlighting style, emoji choice, section ordering, and which fields appear become layout decisions, not code decisions. Future PRs adding a new optional `Position` field need to update layout (or document that the field is exposed but not templated by default), not the renderer.
- A typo in a placeholder name (`{compny}` instead of `{company}`) raises `KeyError` from `str.format_map` at render time, not a silent empty substitution. This is intentional — fail-loud is a better property when iterating on templates.
- Validation runs at startup: `layout_loader` checks every `placeholder_groups` entry references a known field and that `tier_emoji` / `tier_color` cover all three tiers. A malformed `layout.py` fails the run before the first listing is fetched, not after.
