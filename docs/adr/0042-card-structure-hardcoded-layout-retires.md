# Card structure hardcoded; `layout.py` and the placeholder system retire

The **Card** is now a fixed two-block structure:

```
# **{rank}:** {Header}

{Summary}
```

`{Header}` is a three-line block authored by the **LLM Enricher** at classify time (per ADR-0041) — the first line carries the role title, the second `company · location · work_model`, the third `posted_date · seniority · salary`, with the LLM either substituting a known value or dropping the segment (and its `·` separator) when the value is absent. `{Summary}` is the prose paragraph also authored at classify time.

`{rank}` is the only placeholder substituted by the **Renderer**, which collapses to a one-line concatenation. `layout.py`, the `Layout` dataclass, the `PLACEHOLDER_GROUPS` / `CARD_TEMPLATE` constants, the named-group separator logic, and the `str.format_map` plumbing all retire. `init` stops seeding `layout.py`; `init --refresh` deletes it from existing installs.

## Why

- **User-tunable layout was solving a problem that no longer exists.** ADR-0004's framing was "user iterates on layout often, package code rarely." With Header content authored by the LLM (per ADR-0041), the per-listing visual choices (which fields to show, how to order them, separator characters) move into prompt-engineering territory — the user tunes the *prompt* if they want a different Header shape, not a Python module.
- **The placeholder surface drove a real bug.** Issue #523 surfaced a mismatch between the placeholder vocabulary documented in the `Card` glossary entry and the fields actually produced by the classifier. The fix is not to expand the vocabulary — it's to delete the vocabulary entirely. Without separately persisted Position fields (per ADR-0041), there is nothing to placeholder-substitute beyond the rank.
- **One source of truth for visual layout.** Today the Header layout would have to be reflected in both the classify prompt (what the LLM emits) and `layout.py` (what the user can recombine). Dropping the second eliminates drift.
- **Renderer becomes inspection-grade trivial.** A 60-line Renderer module with placeholder dict assembly, group separator logic, and null-policy rules collapses to a single f-string. Easier to audit, harder to break.
- **Aligns with the existing "Card structure is what the LLM emits" reality after ADR-0041.** Once the Header is an LLM-authored string, the only thing the Renderer adds is the rank prefix. There's no per-user customization surface left to defend.

## Considered alternatives

- **Keep `layout.py` but reduce it to a `CARD_TEMPLATE` over `{header}` and `{summary}`.** Rejected: one-knob template files are an attractive nuisance — users will reach for them expecting power that isn't there, and the package still has to ship the loader, error type, and refresh policy for a knob with one degree of freedom.
- **Move the entire Card structure into the classify prompt** (LLM emits `# **{rank}:** ...` directly, Renderer just substitutes `{rank}`). Rejected during grilling (Q16): `rank` isn't known at classify time — the Judge assigns it — so threading a rank placeholder through `extracts.json` persistence is awkward. Splitting the rank prefix into the Renderer keeps the rank-assignment phase clean.
- **Keep a thin user-overridable "card top template"** for users who want `# {rank}. ` instead of `# **{rank}:** `. Rejected: cosmetic tweaks at this granularity belong in a `git diff` against the package, not a runtime knob; the rank-prefix string is one line in `renderer.py`.
- **Leave `layout.py` files alone on `init --refresh`** rather than deleting. Rejected: the file would still load (`load_user_module` doesn't care that nothing reads it) but contribute nothing; users would discover months later that their edits had no effect. Deleting on refresh is the loud-fail equivalent.
- **Special-case: keep `layout.py` if it differs from the seeded default** (preserve user edits). Rejected: complicates the refresh contract, and the "preserved" edits would still be dead code.

## Consequences

- **`layout.py` deleted from the package template tree** (`src/application_pipeline/templates/layout.py` retires).
- **`init --refresh` learns to delete** `<settings-dir>/layout.py` when present. Plain `init` no longer seeds the file. Existing installs lose the file on their next cron tick (refresh runs unconditionally per ADR-0011 amendment).
- **`Layout` dataclass, `LayoutError`, `UserSettingsError` (or its subclass for layout)** all retire. `UserSettingsError` remains as `ConfigError`'s parent if any other user-settings types use it; otherwise also retires.
- **`Renderer` module collapses** to:
  ```python
  def render(rank: int, header: str, summary: str) -> str:
      return f"# **{rank}:** {header}\n\n{summary}\n"
  ```
  No `Position` argument, no `MatchVerdict` argument, no `Layout` argument. Renderer caller (orchestrator) reads Header + Summary from `extracts.json` keyed by stable id, passes them plus the Judge-assigned rank.
- **Orchestrator wiring**: the existing "load Layout at startup, thread it through" path retires. The Renderer call site moves from "render(position, verdict, layout)" to "render(rank, header, summary)".
- **CONTEXT.md `Card` glossary entry** is rewritten to describe the fixed two-block structure. Placeholder vocabulary, `PLACEHOLDER_GROUPS`, null-policy rules all leave the glossary.
- **CONTEXT.md `Layout` and `Renderer` entries** retire (Layout) or shrink to a one-sentence one-function description (Renderer).
- **Test suite**: `tests/renderer/` shrinks to one happy-path test plus rank-bounds. `tests/layout/` retires entirely.
- **Companion ADR-0041** drives this — Card content authorship moved to the LLM, which made the placeholder surface vestigial. ADR-0042 finishes the cleanup.

## Supersedes / amends

- **Supersedes ADR-0004** (Layout as a user-editable Python module). ADR-0004 was already partially superseded by the ADR-0011 amendment ("no path override knobs"); ADR-0042 retires the remaining substance.
