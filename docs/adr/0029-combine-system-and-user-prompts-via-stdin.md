# Combine system+user prompts via stdin; do not use `--system-prompt`

`ClaudeCliInvoker.call()` sends a single combined prompt via stdin. No `--system-prompt`. Wire shape:

```
claude -p - --output-format json --model <alias> [--effort <level>] \
  --no-session-persistence --disable-slash-commands --tools "" --setting-sources user
```

Subprocess cwd: `tempfile.gettempdir()` (severs cwd-keyed CLAUDE.md and auto-memory discovery).

## Why

- **`--system-prompt` empirically destroys classifier format compliance.** Production transcripts showed the model emitting markdown essays with no `<verdict>` block. Probe A (combined via stdin) → correct format. Probe B (split via `--system-prompt`) → essay. Hypothesis: non-`--bare` mode appends to the built-in agent system prompt, leaving the helpful-agent persona dominant.
- `--bare` stays out — silently breaks OAuth subscription auth on current CLI.
- Four harness-strip flags reduce unwanted agent behaviour and token tax.
- Trailing analysis after the verdict tag is parser-tolerated by `extract_json_block`'s rightmost-closing-tag strategy.

## Consequences

- Single prompt file per call site (system + user merged). `SplitPromptTemplate` retired.
- Transcript shape simplified — only `prompt` field, no `system_prompt`.
- ADR-0010 / ADR-0016 / ADR-0028 references unchanged.
