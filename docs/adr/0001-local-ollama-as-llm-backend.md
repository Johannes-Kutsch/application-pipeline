# Local Ollama as the LLM backend

The **LLM Extractor** runs against a local **Ollama** instance using **Qwen 3 8B Instruct (Q4_K_M)** on the Pi 5 8GB. The model identifier is exposed as `Config.ollama_classify_model` and `Config.ollama_judge_model` (both default to `qwen3:8b`) so it can be swapped without a code change. The Pi is the only machine that runs the LLM; the laptop is used only to smoke-test **Parsers** in isolation during development.

## Why

- **No Anthropic API budget.** The applicant has a Claude Code subscription only — the Anthropic API is off-limits for this project's runtime.
- **Pi cron has no Claude Code session.** A Claude-Code-subagent pattern only works inside an interactive session; cron can't trigger one. Any LLM call that must work unattended on the Pi has to be a normal HTTP call to a process the Pi controls.
- **Pi 5 8GB comfortably runs a 7–8B Q4 model** (~5 GB resident), with headroom for the rest of the pipeline.
- **One runtime, one backend.** Since the laptop never runs the LLM, there is no laptop-vs-Pi backend split to worry about and no v1→v1.1 rewrite of the LLM-call layer.
- **Qwen 3 over Qwen 2.5.** Qwen 3 8B Q4_K_M has the same on-disk size and same Pi 5 throughput class as Qwen 2.5 7B (~2.5 tok/s decode), with measurably better JSON-mode adherence and improved German handling for **Triage Profile** prose. Strict quality upgrade at no cost class change.
- **Model identifier as Config, not constant.** Exposed via two fields (`ollama_classify_model`, `ollama_judge_model`) so the two LLM call sites can run different model bins without code changes. Both default to `qwen3:8b` in v1; swapping is a config edit.

## Considered alternatives

- **Anthropic API direct** — rejected: no budget.
- **Haiku via Claude Code subagent** — rejected: doesn't run unattended on Pi cron.
- **Pi-runs-keyword-only, laptop-runs-LLM-after-grab** — rejected: splits the pipeline awkwardly, adds a manual step, and the Pi already has the hardware.
- **Qwen 2.5 7B Instruct (the original choice)** — superseded: same cost class as Qwen 3 8B with weaker JSON-mode adherence. Held by this ADR's earlier revision; bumped at PRD #20's grilling.
- **Qwen 3 4B as the cheap-classify model** — rejected: 2.5 GB + 8B's 5 GB exceeds the Pi 5 RAM headroom that leaves room for scrapers and OS, forcing an Ollama unload/reload on every classify→judge alternation. The reload cost (~10–15 s per swap) exceeds any per-call savings. The documented downgrade path for classify is `qwen3:1.7b` (~1 GB), which co-resides with 8B in RAM.

## Consequences

- Prompts must be tuned against Qwen 3 8B specifically — quality won't match Claude. Acceptable for structured judgment over short German/English listings; revisit if false-classification or false-tier rate becomes a problem.
- **Ollama** is a hard runtime dependency on the Pi. `ollama pull qwen3:8b` is a manual setup step documented in the Pi deployment runbook.
- The `LLMExtractor` Protocol stays in the codebase so tests can mock it cheaply, even though there is only one production implementation.
- The documented downgrade path, if Pi cron throughput becomes the bottleneck: set `Config.ollama_classify_model = "qwen3:1.7b"` (kept as a Config comment for reference). The 1.7B and 8B models co-reside in RAM, so per-listing model swaps are free; cron throughput rises ~3× for the classify portion.
