# Strip Claude Code agent harness from classifier and judge invocations

Both LLM call sites — **Relevance Classifier** and **Match Judge** — invoke `claude -p` in *bare* mode with our own system prompt and no tools, instead of running under the default Claude Code agent harness. The static half of each prompt (system instructions + `{USER_INFO}` + `{skills}` for the judge) is passed via `--system-prompt`; only the per-call variable half (listing for the classifier, candidates block for the judge) is written to stdin.

Wire shape per call:

```
claude -p - \
  --output-format json \
  --model <alias> \
  [--effort <level>] \
  --bare \
  --tools "" \
  --no-session-persistence \
  --system-prompt <static-half>
# stdin: <variable-half>
```

The template files split accordingly. `classify_relevance.md` becomes `classify_relevance.system.md` (lines 1–26 of the previous single file, including `{USER_INFO}` interpolation) + `classify_relevance.user.md` (the `Titel: … / Beschreibung: …` block). `judge_top_n.md` splits the same way, with `{USER_INFO}` and `{skills}` in the system half and the candidates block in the user half.

This supersedes the wire shape sketched in ADR-0015 and amends the cache-amortisation language in ADR-0036 — the substance of both ADRs survives, only the placement of the static prefix changes.

## Why

- **Default harness pays per-call tax we never use.** Under default `claude -p`, every invocation re-reads `CLAUDE.md` files up the tree, loads built-in tool schemas (Read/Bash/Grep/Edit/…), loads slash-command and skill metadata, and prepends per-machine sections (cwd, env, git status) to the system prompt. Easily 5–15 KB of tokens our classifier and judge never reference. `--bare --tools ""` strips all of that.
- **Per-machine sections bust prompt-cache reuse.** Claude Code's own `--exclude-dynamic-system-prompt-sections` flag documents that the default system prompt embeds cwd/env/git-status that *vary across invocations*. With `--system-prompt` we choose exactly what the system prompt is — byte-stable across calls — and the dynamic-sections concern goes away.
- **Solo classify calls (ADR-0036) magnify the per-call tax.** Under batched ADR-0014 the harness cost was paid ~2 times per run. Under ADR-0036 it's paid ~200 times per run. The lever's payoff scales with call count, which is why this ADR lands now rather than alongside ADR-0036.
- **System-prompt slot is what Anthropic's prompt cache is shaped for.** The static prefix belongs in the request's system slot; putting it there (instead of as the leading bytes of a user message) is the canonical way to get the cache discount. We were getting it via prefix-match by accident; moving it to `--system-prompt` makes the caching intentional.
- **Judge benefit is smaller but consistent.** Judge runs once per pipeline run, so there's no second call to cache against — but the harness-stripping wins on raw input-token cost regardless of caching. Applying the same pattern uniformly keeps both call sites' wire shapes parallel and the construction logic shared.

## Considered alternatives

- **`--exclude-dynamic-system-prompt-sections` only.** Less aggressive: keep Claude Code's default system prompt minus the per-machine sections. Rejected — still pays the entire Claude Code system-prompt body (tool schemas, skill registry, default instructions) we don't need, and leaves us trusting the default's contents instead of owning them explicitly.
- **Bypass `claude` CLI entirely and call the Anthropic Messages API directly.** Rejected — ADR-0015 chose the CLI specifically for auth (subscription cap handling) and quota-error parsing (ADR-0023). Direct API would re-introduce auth plumbing and lose the `Max` subscription pricing path. The bare-mode CLI keeps those upsides while shedding the agent layer.
- **Two-prompt session (turn-1 generic context, turn-2 listing).** Investigated and rejected. Caching requires byte-identical conversation prefix, which means the turn-1 assistant response would have to be a hardcoded string rather than a real model response — mechanically equivalent to just lengthening the system prompt, with no token savings and an extra round-trip's worth of complexity. The single-prompt design already gets full prefix-cache benefit.
- **Keep the default harness.** Rejected for the four reasons above; the harness costs tokens on every call and buys us nothing on a pure text-in/JSON-out call site.

## Consequences

- **`ClaudeCliInvoker.call()` signature gains a `system_prompt: str` parameter.** Existing `prompt` parameter is repurposed as the stdin user-message body. CLI args extend to `--bare --tools "" --no-session-persistence --system-prompt <body>` on every call. `--effort` placement unchanged.
- **Prompt files split per call site.** `classify_relevance.md` → `classify_relevance.system.md` + `classify_relevance.user.md`; `judge_top_n.md` → `judge_top_n.system.md` + `judge_top_n.user.md`. The `Prompts` loader reads both halves; each call-site exposes `.render_system(...)` and `.render_user(...)` (or returns a `(system, user)` tuple — implementation choice).
- **`{USER_INFO}` and `{skills}` interpolate into the system half at construction time** (both are run-invariant). The user-message half carries only the per-call variables (`{TITLE}`, `{RAW_DESCRIPTION}` for classifier; `{candidates}` for judge).
- **Transcript shape gains a `system_prompt` field** alongside the existing `prompt` (now the stdin body) so per-call evals can reconstruct the full request. Events log unchanged.
- **Hard floor on `claude` CLI version.** `--bare` is recent; older installations error out. No feature-detection / fallback — `cron.sh` already runs `pip install --upgrade application-pipeline` each tick, and the package's own dependency floor is the canonical place to express this. Document the minimum CLI version in the package README.
- **ADR-0015 amended in spirit, not retracted.** The decision "shell out to Claude Code CLI as a subprocess per call" survives. The implicit assumption "let the default agent harness wrap our prompt" did not; this ADR makes the harness boundary explicit.
- **ADR-0036's cache-amortisation argument survives unchanged.** The static prefix still amortises across calls within the 5-min TTL; it just lives in `--system-prompt` now instead of as the leading bytes of stdin.
- **No `.seen.json` migration, no `Config` schema change.** Pure wire-shape change at the LLM boundary.
