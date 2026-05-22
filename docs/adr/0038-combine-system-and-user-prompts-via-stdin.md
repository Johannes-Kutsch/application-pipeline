# Combine system+user prompts via stdin; do not use `--system-prompt`

`ClaudeCliInvoker.call()` sends a single combined prompt body via stdin and does not pass `--system-prompt` to `claude -p`. The static half (instructions + `{USER_INFO}` + `{skills}` for the judge) and the per-call variable half (listing for the classifier, candidates block for the judge) are concatenated by the caller into one string before the subprocess is spawned. Wire shape per call:

```
claude -p - \
  --output-format json \
  --model <alias> \
  [--effort <level>] \
  --no-session-persistence \
  --disable-slash-commands \
  --tools "" \
  --setting-sources user
# stdin: <combined system+user body>
# subprocess cwd: tempfile.gettempdir()
```

`--bare` stays out (it silently breaks OAuth subscription auth on the current CLI). The four harness-strip flags strip skill auto-trigger, built-in tool schemas, project-level settings, and the session log without touching the auth path. The neutral cwd severs the harness's cwd-keyed discovery channels (project `CLAUDE.md`, auto-memory, project `.claude/` settings).

This ADR replaces an earlier wire-shape iteration (prompt-split via `--system-prompt`, briefly with `--bare`) that broke format compliance and OAuth respectively — see git log for the prior ADR-0038/0039/0040 sequence.

## Why

- **`--system-prompt` empirically destroys classifier format compliance.** Production transcripts in `application-pipeline/logs/llm_classify_relevance.transcripts.jsonl` (2026-05-22 UTC, post-ADR-0040) show the model emitting a markdown analysis essay — emoji headers, tables, trailing *"Möchten Sie eine spezifische Analyse?"* — with no `<verdict>` block, on every call. Reproduced deterministically by isolating the prompt-delivery shape:
  - **Probe A** — same prompt content, same other flags, **combined** body via stdin, no `--system-prompt` → model emits `<verdict>{"in_domain": false}</verdict>` correctly (trailing analysis, but the closing-tag-rightmost parser in `agent_output.extract_json_block` handles it).
  - **Probe B** — same content, same other flags, **split**: stdin = user half, system half via `--system-prompt` → markdown essay, no `<verdict>` tag. Bit-for-bit reproduces the production failure.

  *Hypothesis (not proven):* under non-`--bare` mode, `--system-prompt` is appended to (rather than replacing) the built-in Claude Code agent system prompt, leaving the helpful-coding-agent persona dominant; the classifier instructions land as a soft request the model feels free to ignore. The mechanism is not separately verified — only the symptom-flag binding is. Treat any future "let's split the prompt for caching" proposal as needing a fresh Probe-A/B comparison first.
- **Cache-amortisation argument from the prior ADR-0038 is dropped.** Any prompt-cache benefit now comes via user-turn prefix-match as it did pre-ADR-0038 ("by accident" in 0038's own words). The operator accepts this without re-measurement; if `usage.cache_read_input_tokens` looks wrong in practice, a follow-up forensics issue can re-introduce explicit caching via the Anthropic Messages API at the cost of subscription pricing (which ADR-0015 / ADR-0023 chose against).
- **The four harness-strip flags pull their weight under combined-prompt mode too.** Their per-flag necessity is not separately verified once `--system-prompt` is gone (the production failure that originally motivated `--disable-slash-commands` was running *with* `--system-prompt`, so its load-bearing role under combined mode is unproven). Retained anyway: token-neutral-at-worst, constrain known classes of unwanted agent behaviour, and reduce harness token tax measured in `usage.input_tokens`. Pruning to a minimal set is a possible follow-up if the wire shape ever needs to shrink.
- **`--bare` stays out for the same reason it was dropped in the prior ADR-0039.** Empirically, with `--bare` set and no `ANTHROPIC_API_KEY` in the environment, the CLI exits 1 with `result: "Not logged in"` even when `~/.claude/.credentials.json` holds valid OAuth tokens and a non-bare call from the same shell succeeds. Subscription pricing is load-bearing for ~200 classifier calls per run (ADR-0036).
- **Neutral cwd severs cwd-keyed harness channels.** The harness discovers `CLAUDE.md` by walking up from cwd and keys auto-memory on cwd (`~/.claude/projects/<slug-of-cwd>/memory/`). Running the subprocess from `tempfile.gettempdir()` makes both lookups whiff. Verified manually: from the project cwd the model knows the project name; from `$TEMP` it does not.
- **Trailing analysis after the verdict tag is parser-tolerated.** `agent_output.extract_json_block` finds the rightmost `</verdict>` and parses the body backwards from there. Probe A's "tag plus trailing essay" passes. No prompt hardening (e.g. "do not write commentary after the verdict") is required.

## Considered alternatives

- **Keep `--system-prompt`, strengthen the classifier system prompt to override the persona.** Rejected — Probe B shows the model fully ignores the system-prompt instructions in favour of the Claude Code agent default behaviour. Behavioural overrides at the prompt layer are not a substitute for delivering the prompt where the model will actually obey it.
- **Re-enable `--bare` to get a true system-prompt replacement.** Rejected on the same grounds as the prior ADR-0039: silently breaks OAuth on the current CLI; would force a switch from subscription to per-token billing on a high-call-count workload.
- **Bypass `claude` CLI entirely and call the Anthropic Messages API directly.** Rejected for the same reasons ADR-0015, the prior ADR-0038, and the prior ADR-0039 rejected it — re-introduces auth plumbing and loses the `Max` subscription pricing path on ~200 calls per run (ADR-0036). The combined-prompt wire shape solves the immediate bug without that cost.
- **Two-prompt session (turn-1 generic context, turn-2 listing).** Investigated and rejected in the prior ADR-0038 for cache-prefix reasons. Still rejected here: extra round-trip, no remaining cache argument to justify it.
- **Wait for upstream fix to whatever makes `--system-prompt` suppress format compliance.** Rejected — pipeline is broken today; symptoms are reliably bound to a CLI flag we control. If upstream behaviour ever changes, a Probe-A/B comparison can re-evaluate.

## Consequences

- **`ClaudeCliInvoker.call()` signature shrinks.** The `system_prompt: str` parameter introduced by the prior ADR-0038 is removed; callers pass the full combined body as the single `prompt` argument. CLI args lose `--system-prompt`; the four retained harness-strip flags plus `--no-session-persistence` remain.
- **Prompt files collapse from split to single per call site.** `classify_relevance.system.md` + `classify_relevance.user.md` → `classify_relevance.md`; same merge for `judge_top_n`. Simple concatenation (former system content, blank line, former user content), no separator marker. `{USER_INFO}` interpolation and per-call slot interpolation all live in the one file.
- **Prompt loader collapses.** `SplitPromptTemplate.render_system` / `render_user` retired; each call site exposes a single `render(**slots) -> str`.
- **Transcript shape reverts.** The `system_prompt` field added by the prior ADR-0038 is removed from `llm_classify_relevance.transcripts.jsonl` and `llm_judge_match.transcripts.jsonl` writes. Only the `prompt` field carries the wire content. No migration of historical rows.
- **`_default_runner` keeps `cwd=tempfile.gettempdir()`.** Property of the runner, not the args; unchanged from the prior ADR-0040.
- **Project `CLAUDE.md` and project-local `.claude/` config still do not reach the classifier or judge.** Inherited from the prior ADR-0040. Tune the classifier's `classify_relevance.md` prompt file instead.
- **`usage.input_tokens` should remain materially lower than ADR-0039's regression baseline.** The harness-strip flags and neutral cwd contribute most of the drop the prior ADR-0040 documented (~85% from ~8200 to ~1200 tokens). The combined-prompt switch does not in itself change input-token cost meaningfully.
- **CLI version floor unchanged.** All flags exist on current `claude` 2.x. `--bare` is still avoided; no version floor is introduced or relaxed.
- **ADR-0015 / ADR-0023 / ADR-0036 references survive.** ADR-0015's "shell out to Claude Code CLI as a subprocess per call" decision is unchanged. ADR-0023's quota-error-sleep is unchanged. ADR-0036's solo-call worker model is unchanged; its cache-amortisation language (which the prior ADR-0038 had amended) reverts to the pre-0038 semantics — cache benefit is incidental, not engineered.
- **No `Config` schema change, no `.seen.json` migration.** Pure wire-shape change at the LLM boundary plus prompt-file consolidation.
- **Reproducer artefacts.** The Probe A/B comparison can be regenerated from any row in `llm_classify_relevance.transcripts.jsonl` by extracting the `system_prompt` and `prompt` fields, concatenating for Probe A or feeding separately via `--system-prompt` for Probe B. Both invocations use the same other flags listed in the wire shape above.
