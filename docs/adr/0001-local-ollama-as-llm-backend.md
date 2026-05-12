# Claude Code CLI as the LLM backend

The **LLM Extractor** drives **Claude** via the **Claude Code CLI** in headless mode (`claude -p --output-format json`) invoked as a Python subprocess from the **Pipeline Orchestrator** on the Pi. The CLI runs against the user's Claude Code subscription (authed once via `claude login` on the Pi); the Anthropic API is still off-limits as a budget item. **Ollama** and the **Qwen 3** models are retired.

## Why

- **Pi 5 cannot sustain local inference cheaply.** The earlier revisions of this ADR landed on Qwen 3 0.6B + 4B under Ollama after Qwen 3 8B blew past the Pi's thermal budget. Even the smaller split keeps the SoC warm and the deploy story (manual `ollama pull` of two models, ~3 GB resident, `keep_alive` tuning) is fiddly. Moving inference off the device entirely makes the Pi a thin coordinator — HTTP out, markdown in.
- **Claude Code CLI runs unattended.** The previous revision of this ADR rejected Anthropic-the-vendor on two grounds: "no API budget" and "Claude Code subagents only work in an interactive session." The first still holds — we don't pay per-token via the SDK. The second was wrong for *headless* mode: `claude -p "<prompt>" --output-format json` is a normal subprocess that exits with a result, drivable from cron. The subscription auth lives in `~/.claude/`; cron inherits it from the user's home.
- **Quality ceiling lifts.** The Qwen 3 split was chosen at the bottom of the quality curve we could tolerate. Claude's classify and judge outputs are materially better on German job listings without prompt-engineering acrobatics, and `judge_match`'s open-vocabulary `matched`/`missing` arrays + 2–3 sentence summary stop being marginal.
- **Subprocess + envelope, not SDK.** The CLI's `--output-format json` returns a JSON envelope around the model response carrying `usage` (input/output/cache-read tokens), `total_cost_usd`, `session_id`, and error signals. The orchestrator double-parses (envelope, then `result` body as JSON array) and feeds the token/cost fields into the **Run Divider** telemetry. The **Anthropic SDK** and **Claude Agent SDK** were considered; both add a dependency layer over the same CLI and re-raise the "what auth does it use" question — the CLI already rides on the subscription, the SDKs default to API keys.
- **Subscription rate-limit handling is explicit.** Claude Code surfaces usage-limit errors structurally in the JSON envelope. When the orchestrator sees one mid-run it aborts, writes a `synched/failures/<ts>.md` (per ADR-0012), and exits non-zero. The next cron tick retries; the first batch will hit the limit again and abort again until the window resets. We accept those few wasted pings rather than introduce a `limit-until.txt` cooldown marker — revisit if the wasted pings become observable.
- **The `LLMExtractor` Protocol survives, only the implementation swaps.** Tests continue to mock against the Protocol. The Ollama implementation is *deleted*, not toggled — see "Consequences." Call-site topology (per-language prompts, batched classify, scalar judge) is covered in [ADR-0016](0016-claude-classify-batching.md).

## Considered alternatives

- **Anthropic SDK with API key** — rejected: same "no API budget" constraint as before. The subscription is the only payment surface available for this project.
- **Claude Agent SDK** — rejected: wraps the same CLI under the hood, adds a dependency without giving us anything the subprocess + envelope-JSON path doesn't. Reconsider if we ever want streaming progress.
- **Keep Ollama as a config-toggle fallback** — rejected: dead weight. The Protocol stays so the backend is replaceable in code; resurrection is `git revert` away if Claude subscription policy changes.
- **Move the whole pipeline to the laptop** — rejected: loses the "runs while I sleep, results land via Syncthing in the morning" property that motivated the Pi.
- **Split: parsers on Pi, LLM on laptop** — rejected: two writers, two cron loops, much more plumbing for no real win once inference is HTTP.
- **Qwen 3 0.6B + 4B under Ollama** (the previous revision of this ADR) — superseded: still works, but the Pi-friction (model pulls, thermal headroom, `keep_alive` tuning) is gone for free once inference moves off the device.

## Consequences

- **`OllamaExtractor` is deleted.** `src/application_pipeline/llm/ollama.py`, its tests, and the `OLLAMA_*` cluster of `Config` fields (`base_url`, `classify_model`, `judge_model`, `read_timeout_seconds`, `json_retries`, `http_retries`, `keep_alive`) are removed. `pi-setup.md` drops `ollama pull qwen3:*` steps and gains a `claude login` step.
- **New `Config` fields**: `claude_classify_batch_size: int` (default 100, per [ADR-0016](0016-claude-classify-batching.md)) and any small surface needed to locate the CLI (e.g. `claude_cli_path: str | None`, default looks for `claude` on `PATH`).
- **Prompt files lose the Qwen `/no_think` directive** (irrelevant for Claude). The four-language fanout (`de | en | other | unknown`) collapses to two — only `de` and `en` prompt files exist; `other` / `unknown` Positions are classified against the English prompt (Claude is cross-lingually strong enough for the binary `in_domain` decision).
- **The Pi is a thin coordinator.** Local RAM/thermal headroom is no longer load-bearing. Active cooling is no longer needed for correctness.
- **Auth lives on the Pi.** `claude login` is a one-time manual step in the deployment runbook. The subscription credential file in `~/.claude/` is required for cron to call the CLI; back this up alongside `config.py` and `layout.py`.
- **Run-time cost shifts from electricity to subscription quota.** The **Run Divider** records `claude_input_tokens`, `claude_output_tokens`, `claude_cache_read_tokens`, and `claude_cost_usd` so the user can monitor burn against the subscription cap.
