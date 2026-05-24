# Claude Code CLI as the LLM backend

**LLM Extractor** drives Claude via `claude -p --output-format json` headless subprocess. Runs against the user's Claude Code subscription; Anthropic API off-limits as budget item.

## Why

- Pi 5 can't sustain local inference. Headless `claude -p` works unattended from cron.
- `--output-format json` returns envelope with `usage`, `total_cost_usd`, `session_id`. SDK rejected (defaults to API keys).
- Usage-limit errors surface in the envelope — see ADR-0016 for sleep-and-retry.

## Consequences

- Auth file `~/.claude/.credentials.json` lives outside the settings dir — replicating OAuth through Syncthing is worse than a one-time re-auth.
- Run-time cost shifts from electricity to subscription quota.
