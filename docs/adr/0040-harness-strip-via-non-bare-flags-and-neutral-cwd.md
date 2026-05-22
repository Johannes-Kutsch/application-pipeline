# Strip the harness via non-`--bare` flags and a neutral cwd

`ClaudeCliInvoker.call()` now passes four additional flags to `claude -p` and runs the subprocess from a tempdir cwd. Wire shape:

```
claude -p - \
  --output-format json \
  --model <alias> \
  [--effort <level>] \
  --no-session-persistence \
  --disable-slash-commands \
  --tools "" \
  --setting-sources user \
  [--system-prompt <static-half>]
# stdin: <variable-half>
# subprocess cwd: tempfile.gettempdir()
```

`--bare` stays out (ADR-0039 — silently breaks OAuth subscription auth). The four added flags strip the agent harness without touching the auth path. The neutral cwd severs the harness's cwd-keyed discovery channels (project `CLAUDE.md`, auto-memory, project `.claude/` settings).

This amends ADR-0039. The reason for ADR-0039 (drop `--bare` because it kills OAuth) survives unchanged; this ADR addresses the *consequence* ADR-0039 accepted ("default harness re-introduces 5–15 KB of system-prompt content per call") and the failure mode it did not anticipate (skill auto-trigger hijacking the classifier).

## Why

- **Skill auto-trigger broke the classifier in production.** First run after ADR-0039 landed: `classify_relevance` produced `protocol_error: tag_missing: <verdict> block missing or malformed`. Inspection of `logs/llm_classify_relevance.transcripts.jsonl` shows the model responded as `~/.claude/skills/analyse-listing` rather than as the classifier — full `"✅ Startup-Checks bestanden"` / `"## Listing-Bestätigung"` / `"Welches Unternehmen…"` preamble, no `<verdict>` block. `--system-prompt` does not suppress harness skill triggers; the harness still routes user messages through the skill matcher. `--disable-slash-commands` (CLI help: "Disable all skills") closes this hole without affecting auth.
- **`--tools ""` works again.** ADR-0039 reported `error: option '--tools <tools...>' argument missing`. On the current CLI, `--tools ""` is accepted and disables all built-in tools (verified manually on Windows 11 with `claude` 2.x). Eliminates Bash/Read/Edit/Grep/etc. schemas from the system prompt; our calls are pure text-in/JSON-out and never use tools.
- **`--setting-sources user` skips project and local settings.** Default loads `user,project,local`. Project `.claude/` config (skill paths, hook commands, MCP servers) is irrelevant to a pure classifier call and adds noise. Restricting to `user` keeps the subscription-auth path (lives in `~/.claude/`) while ignoring the project layer.
- **Neutral cwd severs cwd-keyed harness channels.** The harness discovers `CLAUDE.md` by walking up from cwd and keys auto-memory on cwd (`~/.claude/projects/<slug-of-cwd>/memory/`). Running the subprocess from `tempfile.gettempdir()` makes both lookups whiff. Reproduced empirically: from project cwd the model knows the project name; from `$TEMP` it does not.
- **Measurable token reduction.** Manual `claude -p` with this stack on a trivial prompt: `input_tokens` drops from ~8200 (cache-creation, project cwd, default settings) to ~1200 (system-prompt provided, neutral cwd, harness flags). Not zero — `--bare`'s skeleton stripping is still unreachable — but a ~85% drop on the per-call harness tax that ADR-0039 explicitly listed as a known cost.
- **`--exclude-dynamic-system-prompt-sections` is a no-op for us.** ADR-0039 listed it as a deferred follow-up. CLI help is explicit: *"Only applies with the default system prompt (ignored with --system-prompt)."* We always pass `--system-prompt`. Removing this from the follow-up list.

## Considered alternatives

- **Strengthen the classifier system prompt to override skill triggers.** Rejected — fragile, model-version dependent, and the offending response showed the model fully ignored the system prompt's instructions in favour of the skill. Behavioural overrides at the prompt layer are not a substitute for not loading the skill registry.
- **`--system-prompt` only, leave the harness otherwise intact.** This is what ADR-0039 shipped. Insufficient — the skill registry still loads and triggers, as the production failure shows.
- **Mount user-level skills only via project-scoped overrides instead of `--setting-sources user`.** Considered. More targeted but adds maintenance burden (skill list to keep in sync with reality). `--setting-sources user` is the coarse one-flag answer and the local settings layer is empty for this project anyway.
- **Move classifier off `claude` CLI to direct Anthropic Messages API.** Rejected for the same reasons ADR-0038 and ADR-0039 rejected it — re-introduces auth plumbing, loses `Max` subscription pricing on ~200 calls per run (ADR-0036), and the harness-strip wire shape achieves the same end without that cost.
- **Run from a tempdir but keep `--setting-sources` defaults.** Tempdir alone prevents project `CLAUDE.md` discovery and project auto-memory keying, but `.claude/` project files referenced by absolute path in user settings could still load. Belt-and-braces: do both.

## Consequences

- **`ClaudeCliInvoker.call()` argv grows by four flags.** `--disable-slash-commands`, `--tools ""`, `--setting-sources user` added unconditionally alongside the existing `--no-session-persistence`. `--bare` and `--tools` (the bare-mode form) remain absent. Wire-shape test updated to assert all four are present and `--bare` is still out.
- **`_default_runner` pins `cwd=tempfile.gettempdir()` on every `subprocess.run`.** This is a property of the runner, not the args — callers and the `SubprocessRunner` protocol are unchanged. Test runners that supply their own `_runner` callable continue to ignore cwd as before.
- **Project `CLAUDE.md` no longer reaches the classifier or judge.** Any future expectation that classifier behaviour can be tuned by editing the repo's `CLAUDE.md` no longer holds — the classifier never sees it. Tune the classifier's `*.system.md` prompt files instead.
- **Project-local `.claude/` skill, hook, and MCP config is ignored for these calls.** If we ever want a classifier-specific MCP server or hook, it must live under `~/.claude/` or be passed via `--mcp-config` / `--settings` explicitly.
- **`usage.input_tokens` should drop materially after deploy.** Watch `llm_classify_relevance.transcripts.jsonl` and `pipeline_prefilter.transcripts.jsonl` — per-call `input_tokens` was ~8200 cache_creation under ADR-0039, expected ~1200 input_tokens with no cache_creation under this ADR. A regression in that number is a signal one of these flags stopped doing its job (CLI behaviour change upstream).
- **CLI version floor unchanged.** All four flags exist on current `claude` 2.x. No new floor introduced. If `--tools ""` becomes rejected again upstream (it was in ADR-0039's snapshot), the wire-shape test will catch it via integration.
- **ADR-0038 and ADR-0039 amended in spirit, not retracted.** ADR-0038's prompt-split (system/user halves, `--system-prompt` for the static half) survives. ADR-0039's "do not use `--bare`" survives. This ADR fills in the harness-strip approach that ADR-0039 left as an open consequence.
- **No `Config` schema change, no `.seen.json` migration, no prompt-template change.** Pure wire-shape addition at the CLI boundary plus a subprocess-cwd change in `_default_runner`.
