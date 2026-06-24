# Card structure hardcoded; `layout.py` retires

Card: `# **{rank}:** {Header}` + `{Summary}`. Header is three-line LLM-authored block (ADR-0025). `{rank}` is the only Renderer-substituted placeholder. `layout.py`, `Layout` dataclass, placeholder system all retire. `init --refresh` deletes `layout.py`.

## Why

- With LLM-authored Headers, visual tuning moves into prompt engineering. Placeholder surface drove a real bug (#523). Renderer becomes a single f-string.

## Consequences

- `Layout` dataclass, `LayoutError` retire. Renderer takes `(rank, header, summary)`.
