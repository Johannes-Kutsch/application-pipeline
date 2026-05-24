# Card structure hardcoded; `layout.py` and the placeholder system retire

Card is a fixed two-block structure: `# **{rank}:** {Header}` + `{Summary}`. Header is a three-line LLM-authored block (ADR-0032). `{rank}` is the only Renderer-substituted placeholder. `layout.py`, `Layout` dataclass, `PLACEHOLDER_GROUPS`, `CARD_TEMPLATE`, and `str.format_map` all retire. `init --refresh` deletes `layout.py` from existing installs.

## Why

- User-tunable layout was solving a problem that no longer exists — with LLM-authored Headers, visual tuning moves into prompt engineering.
- The placeholder surface drove a real bug (issue #523 — vocabulary/field mismatch).
- Renderer becomes a single f-string. Easier to audit, harder to break.

## Consequences

- `Layout` dataclass, `LayoutError` retire. Renderer takes `(rank, header, summary)`, no `Position`/`Layout` arguments.
- `init --refresh` deletes `<settings-dir>/layout.py`.

## Supersedes

- Former ADR-0004 (Layout as user-editable module).
