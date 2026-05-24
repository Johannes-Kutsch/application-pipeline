# Orchestrator queue topology: shared outbound + per-parser inbound

One shared `outbound` queue (all parsers push) plus one dedicated `inbound[parser_id]` queue per parser. Outbound items tagged `(parser_id, payload)`. Inbound decisions: `ENRICH | SKIP | SKIP_AND_END_QUERY`. Refines ADR-0004. Amended by ADR-0042: enrich queue replaced by classify queue.

## Why

- `queue.Queue` has no select-on-many — shared outbound is standard fan-in.
- Per-parser inbound preserves lockstep; no parser receives another's decision.
- `SKIP_AND_END_QUERY` only emitted on `run_state.is_aborted` (ADR-0011).
- `PARSER_DEAD` is a normal item, not an escaping exception.

## Consequences

- One `outbound` queue plus `dict[str, queue.Queue]` for inbound.
- Only main thread reads `outbound` and writes `inbound`.
