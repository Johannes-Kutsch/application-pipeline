# Orchestrator queue topology: shared outbound + per-parser inbound, with end-query folded into the decision channel

The **Pipeline Orchestrator** uses **one shared `outbound` queue** (all parser threads push into it) plus **one dedicated `inbound[parser_id]` queue per parser thread**. Outbound items are tagged `(parser_id, payload)` where `payload ∈ {PositionStub, Position, ParserError, PARSER_DONE, PARSER_DEAD(exc)}`. Inbound decisions are `ENRICH | SKIP | SKIP_AND_END_QUERY`. The end-query signal **rides on the same per-stub decision channel** as `ENRICH/SKIP`; there is no separate async signalling path.

This ADR refines ADR-0007 (which pinned "parser threads as pure producers" but left the queue topology and end-query mechanics underspecified).

## Why

PRD #23 round-2 step 10 says *"Pull next item from any outbound queue"* — but `queue.Queue` has no select-like wait-on-many. PRD #23 round-2 step 3 says *"the Orchestrator stops consuming the generator and signals the parser thread to move to its next `(keyword, location)` query via the inbound queue"* — but the parser thread is, by the lockstep cadence, *blocked* on `inbound.get()` waiting for the per-stub decision; an out-of-band signal cannot reach a blocked thread without either a second channel or shoving it through the existing one. Both gaps need closing before the orchestrator can be implemented.

- **Shared outbound is the standard fan-in idiom.** N producers, one consumer, one blocking `get()`. Parser identity rides on the item itself, so the consumer can route the response back to the right inbound queue without parsing the item.
- **Per-parser inbound preserves the lockstep contract.** Each parser thread has at most one outstanding stub. Routing the decision back via `inbound[parser_id]` means a parser thread never receives a decision intended for a different parser. No parser-id filter on the receive side; no wasted wakeups.
- **Folding the end-query signal into the decision keeps the protocol synchronous.** Because the parser is blocked waiting for *some* decision after every push, returning `SKIP_AND_END_QUERY` is the natural moment to deliver "and stop the current generator." The parser handles it inline: receives `SKIP_AND_END_QUERY` → calls `current_generator.close()` → advances to the next `(keyword, location)` from its work-list → starts a fresh `discover()` → pushes its first stub into the shared outbound. No async signal handler, no query-ID correlation, no race.
- **`PARSER_DEAD` is a normal item, not an exception that escapes the thread.** Wrapping the parser-thread bootstrap in `try / except BaseException` and pushing `PARSER_DEAD(parser_id, exc)` before re-raising means the main thread's "until all parsers signalled done" loop always makes forward progress even when a parser thread crashes from a bug. The traceback gets logged at ERROR; surviving parsers continue.

## Considered alternatives

- **Per-parser outbound queue + main thread polls each in a loop with a small timeout.** Rejected: burns CPU on idle, has worst-case tail latency proportional to the polling interval, and the timeout knob is one more thing to tune for a Pi. The shared outbound has no idle cost.
- **Shared outbound and shared inbound, decisions tagged with `parser_id` and filtered by each parser thread.** Rejected: every inbound `put` wakes all N parsers, each of whom checks the tag and goes back to sleep if it isn't theirs. With N=3 the overhead is negligible, but the per-parser inbound is no more code and removes the wasted wakeups entirely.
- **Out-of-band signalling for end-of-query** (e.g., `threading.Event` per parser, checked between stub pushes). Rejected: introduces a window where the parser pushes one more stub before noticing the event, which the main thread then has to discard — the lockstep guarantee gets blurry. Folding into the decision message preserves "the parser only acts on what the main thread told it about *this* stub."
- **Drop queues entirely; spin a `ThreadPoolExecutor` of futures, one per parser.** Rejected: futures don't model a streaming protocol cleanly. We'd end up rebuilding the queues by hand on top of `concurrent.futures` plumbing, or accepting that each parser only delivers its result as a list at the end (losing the lockstep back-pressure that motivated the producer/consumer split in ADR-0007).
- **Let parser-thread crashes propagate uncaught.** Rejected: the main thread's `until all PARSER_DONE` loop deadlocks indefinitely. A cron tick that hangs forever is the worst possible failure mode (no log line, no exit code, no surface for cron's mail-on-failure).

## Consequences

- The orchestrator owns one `queue.Queue` instance for `outbound` and one `dict[str, queue.Queue]` indexed by `parser_id` for `inbound`. With v1's three parsers, that is four queue objects total.
- Each parser thread's main loop is approximately:
  ```python
  try:
      for query in self.worklist:
          gen = self.parser.discover(query)
          try:
              for stub in gen:
                  outbound.put((self.id, stub))
                  decision = inbound[self.id].get()
                  if decision is ENRICH:
                      try:
                          position = self.parser.enrich(stub)
                          outbound.put((self.id, position))
                      except ParserError as exc:
                          outbound.put((self.id, exc))
                  elif decision is SKIP_AND_END_QUERY:
                      gen.close()
                      break
                  # else: SKIP — keep looping
          finally:
              gen.close()
  except BaseException as exc:
      outbound.put((self.id, PARSER_DEAD(exc)))
      raise
  else:
      outbound.put((self.id, PARSER_DONE))
  ```
- Parsers must be tolerant of `gen.close()` mid-iteration (a `GeneratorExit` raised inside `discover()`). The existing parsers' `discover()` implementations are simple generators with no resource-acquisition outside the parser's already-context-managed `httpx.Client`, so `GeneratorExit` is safe.
- `RunSummary` may grow a `parsers_dead: int` counter, or rely on the ERROR log line; the choice is left to the orchestrator implementer since it is reversible.
- Stub parsers in integration tests must be `Parser`-Protocol-compliant (no-op `__enter__`/`__exit__`) and their `discover()` must tolerate `gen.close()`. The existing `Parser` Protocol already requires both.
- ADR-0007's "single-writer at the file level becomes literal" property is preserved: only the main thread reads `outbound` and only the main thread writes to `inbound[parser_id]`; parser threads are write-only on `outbound` and read-only on `inbound[self.id]`.
