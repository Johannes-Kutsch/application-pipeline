# Drop `--bare`, `--tools ""` — keep subscription auth working

`ClaudeCliInvoker.call()` no longer passes `--bare` or `--tools ""` to `claude -p`. `--no-session-persistence` is retained — it works without `--bare` and we want to keep classifier and judge invocations off the session log. The invocation reverts to the default Claude Code agent harness otherwise:

```
claude -p - \
  --output-format json \
  --model <alias> \
  [--effort <level>] \
  --no-session-persistence \
  [--system-prompt <static-half>]
# stdin: <variable-half>
```

`--system-prompt` and the system/user template split introduced by ADR-0038 survive — only the two harness-stripping flags are removed.

This amends ADR-0038. The substance of ADR-0038 (split prompts into system/user halves, push the run-invariant prefix into `--system-prompt`) is retained; the bare-mode wire shape is retracted.

## Why

- **`--bare` silently breaks OAuth subscription auth.** Empirically, with `--bare` set and no `ANTHROPIC_API_KEY` in the environment, the CLI exits 1 with `result: "Not logged in · Please run /login"` even when `~/.claude/.credentials.json` holds valid OAuth tokens and a non-bare `claude -p` call from the same shell succeeds. ADR-0038 assumed `--bare` only stripped the agent harness while preserving the subscription auth path — that assumption was wrong. Reproduced on Windows 11 with `claude` 2.x and a `Max` subscription on 2026-05-22.
- **`--tools ""` is rejected by the current CLI.** The flag now requires a non-empty argument list (`error: option '--tools <tools...>' argument missing`). The flag was tied to bare mode and is no longer applicable once `--bare` is dropped.
- **Subscription pricing is load-bearing for this pipeline.** ADR-0015 chose the CLI specifically for the `Max` subscription path and ADR-0023's quota-error parsing. An "API-key-only" workaround for `--bare` would re-introduce per-token billing on a high-call-count workload (ADR-0036: ~200 classify calls per run) — exactly the cost ADR-0015 was avoiding.
- **The token-tax we re-accept is bounded and observable.** Default harness adds CLAUDE.md content, tool schemas, slash-command/skill metadata, and per-machine sections to the system prompt — measurable in `usage.input_tokens` per call. Token cost reappears, but it's paid in subscription quota, not USD; the immediate failure mode (pipeline produces zero classifications) is far worse than the slower-burn cost.

## Considered alternatives

- **Keep `--bare`, require users to set `ANTHROPIC_API_KEY`.** Rejected — switches every install from subscription pricing to per-token billing without consent, and contradicts ADR-0015's auth choice. Would also need a config-validation step that errors if the env var is missing, plus documentation of the trade-off in the README.
- **Feature-detect `--bare` auth and fall back.** Rejected — adds a probe call (more latency, more quota burn) and a branching auth model. Easier to just not use the broken flag.
- **Wait for upstream fix in the Claude Code CLI.** Rejected as the load-bearing path — pipeline is broken today. If/when upstream fixes `--bare` to honour OAuth credentials, a future ADR can re-enable it; the system/user prompt split from ADR-0038 means flipping the flag back is a one-line change.
- **`--exclude-dynamic-system-prompt-sections`.** Considered as a middle ground (strip per-machine sections but keep auth). Defer — worth investigating in a follow-up if harness token cost proves painful; not required to unblock the pipeline.

## Consequences

- **`ClaudeCliInvoker.call()` no longer emits `--bare` or `--tools`.** `--no-session-persistence` is retained (it works without `--bare` and is verified by manual `claude -p` test). Argument construction in `src/application_pipeline/llm/claude_cli.py` shrinks by two flags. The bare-mode wire-shape test asserts `--bare` and `--tools` are absent while `--no-session-persistence` is still present.
- **The system/user prompt split from ADR-0038 stays.** Templates remain `*.system.md` + `*.user.md`; `--system-prompt` continues to carry the static half; stdin carries the variable half. Cache amortisation argument (ADR-0036) survives — the static prefix still occupies the system slot.
- **Per-call input token count increases.** The default harness re-introduces ~5–15 KB of system-prompt content per call (CLAUDE.md, tool schemas, skill registry, machine state). Visible in `usage.input_tokens` on every classifier and judge call.
- **No `Config` schema change, no `.seen.json` migration, no prompt-template change.** Pure wire-shape revert at the CLI boundary.
- **ADR-0038 is amended, not retracted.** Its prompt-split decision is still in force; only its harness-stripping wire shape is undone.
- **Hard floor on `claude` CLI version from ADR-0038 (`--bare` availability) becomes irrelevant.** `--system-prompt` is older and broadly available; no new version floor is introduced by this ADR.
