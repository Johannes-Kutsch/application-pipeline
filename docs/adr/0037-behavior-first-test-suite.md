# Behavior-first test suite

Tests exercise caller-visible behavior through the narrowest real interface. They do not freeze implementation topology, private symbols, fake call logs, or exact hand-edited Markdown prose unless that shape is a documented public contract.

## Decision

- Delete tests inspecting imports, source text, `__all__`, private symbols, or internal collaborator call order.
- Refactor to assert public outcomes: returned values, Run Summary, Log Artifacts, Daily Results File semantics, Deduplication Store state, Card Store state, CLI exit/output.
- Keep exact output assertions only when exact output is the documented contract.
- Keep private/fake boundary tests only for true external dependencies (HTTP, `pdflatex`, filesystem failure injection).

## Module-specific guidance

- **Init Bootstrap**: test through `init`/CLI with temp filesystem + real package resources.
- **Agent Skills**: test existence, non-empty, required metadata, `_shared` links, no retired paths. No prose snapshots.
- **Prompt Loader**: test slot validation, routing, brace handling. No prose snapshots.
- **DailyResultsFile**: assert Card semantics (Rank, Header, URL, Summary, Raw Description, append). No blank-line snapshots.
- **Failure Report**: test stage, error class, message, timestamp, log tail. No layout snapshots.
- **Compile CV Workflow**: test through `compile_cv`/CLI. Cover PDF pub, preflight, pdflatex failure surfacing, cleanup.

## Why

- Suite accumulated tests that make further refactors expensive by failing on internal changes while behavior stays the same.
