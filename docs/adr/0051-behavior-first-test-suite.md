# Behavior-first test suite

Tests should exercise caller-visible behavior through the narrowest real interface that owns that behavior. They should not freeze implementation topology, private symbols, fake collaborator call logs, call counts, or exact hand-edited Markdown prose unless that exact shape is a documented public contract.

## Context

The suite has accumulated tests that helped during refactors but now make further refactors expensive. Examples include source-shape tests, private Compile CV Workflow tests, FakeStatusDisplay call-log assertions outside Status Display's own tests, exact generated Markdown snapshots, and byte-equality checks for hand-edited Agent Skills and Prompt Markdown.

Those tests fail when behavior stays the same but internals change. This conflicts with the repo's deep-module direction: modules such as Init Bootstrap, Parser Lifecycle, Parser Intake, Classify Stage, Pool, DailyResultsFile, Failure Report, Prompt Loader, and Compile CV Workflow should hide their internal helpers behind stable public seams.

## Decision

- Delete tests whose only purpose is to inspect imports, source text, `__all__`, private symbols, hidden class names, test-source contents, or internal collaborator call order.
- Refactor valuable tests so they assert public outcomes: returned values, Run Summary, Run Divider, Run Log content, Log Artifacts, Daily Results File semantics, Failure Reports, Deduplication Store state, Card Store state, CLI exit/output, or parser interface results.
- Keep private or fake boundary tests only when the fake represents a true external dependency, such as HTTP, `pdflatex`, or filesystem failure injection.
- Prefer pytest fixtures for repeated setup.
- Keep exact output assertions only when exact output is itself the documented public contract.

## Module-specific guidance

- **Init Bootstrap**: test through `init` or the CLI with temporary filesystem state and real package resources. Preserve package-owned versus operator-owned artifact policy. Do not test helper-level seed plans, storage adapters, or byte identity for hand-edited Agent Skills and Prompt Markdown.
- **Agent Skills**: test that seeded tool-root `SKILL.md` files exist, are non-empty, carry required metadata where applicable, link required tool-local `_shared` docs, and avoid retired path references. Do not snapshot workflow prose.
- **Prompt Loader**: test slot validation, call-site routing, brace handling, and rendered classifier/judge inputs. Do not snapshot prompt prose.
- **Cover Paragraph Pattern Library**: keep parser-level tests with small inline fixtures, plus one shipped-template smoke test that loads successfully.
- **DailyResultsFile**: test `ensure_initialized` and `commit` through the public interface. Assert Card semantics such as Rank order, Header, URL, Summary, Raw Description, and append behavior; avoid full-file blank-line snapshots unless the Card contract itself changes.
- **Failure Report**: test diagnostic usefulness: stage, error class, error message, timestamp/package-tag behavior, and log tail presence. Avoid exact Markdown layout snapshots.
- **Compile CV Workflow**: test `compile_cv` or the CLI. Preserve coverage for PDF publication, Config preflight, malformed CV Slot-Map messages, pdflatex failure surfacing, success cleanup, and failure build-dir retention. Do not assert build-loop pass order unless it is the behavior under test.
- **Status Display**: test row/phase protocol in Status Display's own focused tests. Elsewhere prefer Run Summary, lifecycle Log Artifacts, Run Log, or Daily Results File observations.
- **Run Metrics**: test summaries, dividers, Run Log output, and externally meaningful counters. Avoid display call-log assertions where possible.
- **Parser Lifecycle, Parser Intake, Classify Stage, and Pool**: test through their public seams and downstream effects. Avoid queue sentinels, handoff call logs, worker topology, and private storage assertions.

## Consequences

The suite becomes less useful as an internal architecture freeze, but more useful as a regression suite for operator-visible behavior. Some exact Markdown and call-log tests will be deleted or rewritten. Hand-edited Agent Skills and Prompt Markdown remain protected against broken affordances without making normal wording edits tedious.
