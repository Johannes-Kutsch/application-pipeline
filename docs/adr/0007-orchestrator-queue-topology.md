# Orchestrator queue topology: shared outbound + per-parser inbound

The orchestrator uses **one shared `outbound` queue** (all parsers push) plus **one dedicated `inbound[parser_id]` queue per parser**. Outbound items are tagged `(parser_id, payload)` where `payload ∈ {PositionStub, Position, ParserError, PARSER_DONE, PARSER_DEAD(exc)}`. Inbound decisions: `ENRICH | SKIP | SKIP_AND_END_QUERY`. End-query rides on the same decision channel — no separate async signalling path.

Refines ADR-0005 (which pinned producers/consumers but left queue mechanics open).

## Why

- **`queue.Queue` has no select-on-many.** Shared outbound is the standard fan-in idiom; parser identity rides on the item so the consumer routes responses back without inspecting the payload.
- **Per-parser inbound preserves lockstep.** Each parser has at most one outstanding stub. Routing back via `inbound[parser_id]` means no parser receives another's decision; no wasted wakeups.
- **Folding end-query into the decision keeps the protocol synchronous.** The parser is blocked waiting after every push; `SKIP_AND_END_QUERY` is the natural moment to deliver "and stop the current generator." Parser handles inline: receive → `current_generator.close()` → advance to next `(keyword, location)`.
- **`PARSER_DEAD` is a normal item, not an exception that escapes the thread.** Wrap parser-thread bootstrap in `try/except BaseException`, push `PARSER_DEAD(parser_id, exc)` before re-raising. Main thread's "until all signalled done" loop always progresses.

## Consequences

- One `outbound` queue plus `dict[str, queue.Queue]` for inbound (4 queue objects with v1's three parsers).
- Parser thread loop drains queries, pushes stub → blocks on decision → enriches on ENRICH / closes generator on SKIP_AND_END_QUERY / loops on SKIP. `BaseException` is caught and posted as `PARSER_DEAD`.
- Parsers must tolerate `gen.close()` mid-iteration (`GeneratorExit` inside `discover()`).
- Only main thread reads `outbound` and writes `inbound`; parsers are write-only on outbound, read-only on their own inbound.
- `SKIP_AND_END_QUERY` is only emitted on `run_state.is_aborted` (per ADR-0017).
