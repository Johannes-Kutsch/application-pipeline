# Classifier runs solo `claude -p` calls per Position; batching retired

One Claude request per **Position** — `classify_relevance(item) -> (RelevanceVerdict, CallUsage)`. No id field, no array protocol, no batch size knob. Identical prefix amortised via Anthropic's 5-min prompt cache. Prompt instructs short-circuit on first strong out-of-domain signal. Supersedes the prior batched-single-turn protocol.

Amended by ADR-0032: output shape changes to `{matches, header, summary}`. Amended by ADR-0031: parallelised to N workers.

## Why

- Whole-batch blast radius: one malformed JSON forfeits 100 verdicts. Solo bounds to one item.
- Lost-in-the-middle attention: 150k-token batch degrades mid-batch extract quality.
- JSON brittleness: forcing 100 well-formed records is the source of batch failures.
- Prompt cache makes solo affordable — ~90% prefix discount on cache hits. Cost differential vs batching ~15%.

## Consequences

- `classify_relevance(item)` replaces `classify_relevance_batch`. `<verdict>` tag (singular).
- `Config.claude_classify_batch_size` retired. Unknown fields raise `ConfigError`.
- Every call writes event + transcript rows. Volume ~200 rows/day vs ~2 batched.
- Quota handling (ADR-0016) unchanged: sleep-and-retry on the same solo call.
