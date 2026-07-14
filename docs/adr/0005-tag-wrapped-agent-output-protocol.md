# Tag-wrapped Agent Output Protocol

Every LLM response carries structured payload in semantic XML tags — `<verdict>` for classifier, `<verdicts>` for judge (ADR-0009). **Agent Output Protocol** extracts via rightmost-closing-tag walk-back + regex fence-strip, then `json.loads`. Bare-JSON fallback with `protocol_fallback` log when tags absent.

## Why

- Tag-anchored parsing durable — preamble/fences don't kill extraction.

## Consequences

- `application_pipeline/llm/agent_output.py` exports `extract_json_block(text, tag)` and `AgentOutputProtocolError`.
- Prompt templates close with tag instruction + example per call site.
