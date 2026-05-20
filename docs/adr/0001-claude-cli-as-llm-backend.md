# Claude Code CLI as the LLM backend

**LLM Extractor** drives Claude via `claude -p --output-format json` headless subprocess from the Pi. Runs against the user's Claude Code subscription (auth via one-time `claude login`); Anthropic API off-limits as budget item. Ollama/Qwen retired.

## Why

- **Pi 5 can't sustain local inference cheaply.** Inference off-device makes the Pi a thin coordinator — HTTP out, markdown in. No thermal headroom, no model pulls, no `keep_alive` tuning.
- **Headless mode works unattended.** `claude -p` is a normal subprocess driveable from cron; subscription auth in `~/.claude/` inherits via the user's home.
- **Quality ceiling lifts.** Claude beats Qwen on German listings without prompt-engineering acrobatics.
- **Subprocess + envelope, not SDK.** `--output-format json` returns envelope with `usage` (input/output/cache-read tokens), `total_cost_usd`, `session_id`. Anthropic SDK and Claude Agent SDK both wrap the same CLI but default to API keys — rejected.
- **Subscription rate-limit handling is structural.** Usage-limit errors surface in the envelope. See ADR-0023 for the sleep-and-retry behaviour that replaced earlier abort/degrade designs.
- **`LLMExtractor` Protocol survives, only the implementation swaps.** Tests mock the Protocol; `OllamaExtractor` deleted, not toggled.

## Consequences

- `OllamaExtractor`, its tests, and `OLLAMA_*` Config fields removed. `pi-setup.md` drops `ollama pull`, gains `claude login`.
- New Config: `claude_classify_batch_size: int` (default 100, see ADR-0014) and optional `claude_cli_path`.
- Auth file `~/.claude/.credentials.json` lives outside `data/` deliberately — replicating OAuth through Syncthing is worse than a one-time re-auth.
- Run-time cost shifts from electricity to subscription quota. Per-call-site Claude token/cost fields land in `pipeline_orchestrator.events.jsonl` (see ADR-0018).
