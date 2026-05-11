# Local Ollama as the LLM backend

The **LLM Extractor** runs against a local **Ollama** instance on the Pi 5 8GB, using a **two-model split**: **Qwen 3 0.6B (Q4_K_M)** for **`classify_relevance`** and **Qwen 3 4B (Q4_K_M)** for **`judge_match`**. The model identifiers are exposed as `Config.ollama_classify_model` (default `qwen3:0.6b`) and `Config.ollama_judge_model` (default `qwen3:4b`) so they can be swapped without a code change. The Pi is the only machine that runs the LLM; the laptop is used only to smoke-test **Parsers** in isolation during development.

## Why

- **No Anthropic API budget.** The applicant has a Claude Code subscription only — the Anthropic API is off-limits for this project's runtime.
- **Pi cron has no Claude Code session.** A Claude-Code-subagent pattern only works inside an interactive session; cron can't trigger one. Any LLM call that must work unattended on the Pi has to be a normal HTTP call to a process the Pi controls.
- **Qwen 3 8B does not fit the Pi 5's *thermal* budget under sustained inference, only its RAM budget.** The earlier revision of this ADR picked Qwen 3 8B because it fits in ~5 GB resident with headroom. RAM was the wrong constraint. Empirically (2026-05-11), sustained 8B inference on this Pi pegs all four cores at ~380% and pushes the SoC past the 80 °C soft-throttle threshold (observed `temp=82.9°C`, `vcgencmd get_throttled=0x80008` — soft temperature limit currently active and previously occurred), and per-call wall time stretches to 3–6 minutes. A run with ~10 listings cannot complete inside a reasonable cron tick. Active cooling alone does not close the gap — 8B at ~2.5 tok/s decode is fundamentally slow on this hardware.
- **Two-model split lets each call site pay only what its quality floor demands.** `classify_relevance` is a binary `in_domain: bool` — a 0.6B model handles it at ~15–20 tok/s with negligible quality cost. `judge_match` produces structured JSON with open-vocabulary `matched`/`missing` arrays and a 2–3 sentence German or English summary — needs more capability, but a 4B model is the smallest Qwen 3 size where JSON-mode adherence and German prose stay robust enough for a personal pipeline. Combined resident size ~3 GB; both models co-reside in RAM under Ollama's `keep_alive`, so no swap/reload cost on classify→judge alternation.
- **Disable Qwen 3 thinking mode.** Qwen 3 models default to emitting `<think>...</think>` tokens before the answer; on small models on Pi this can double per-call latency without improving classify/judge output quality. Prompts must include the `/no_think` directive (Qwen 3 convention) or pass the equivalent option to Ollama.
- **Model identifier as Config, not constant.** Exposed via two fields (`ollama_classify_model`, `ollama_judge_model`) so the two LLM call sites can run different model bins without code changes. Defaults are `qwen3:0.6b` and `qwen3:4b` respectively; swapping (up to `qwen3:1.7b`/`qwen3:8b`, or out to a different family) is a config edit.

## Considered alternatives

- **Anthropic API direct** — rejected: no budget.
- **Haiku via Claude Code subagent** — rejected: doesn't run unattended on Pi cron.
- **Pi-runs-keyword-only, laptop-runs-LLM-after-grab** — rejected: splits the pipeline awkwardly, adds a manual step.
- **Qwen 3 8B for both call sites** (the previous revision of this ADR) — superseded: fits in RAM but not in thermal/throughput budget on Pi 5. Empirical data above; ~2.5 tok/s decode means per-call wall time of 2–3 minutes unthrottled and 3–6 minutes when soft-throttled, making a full run unbounded in wall-clock.
- **Qwen 3 1.7B for classify, 8B for judge** (the previous downgrade path) — rejected at the same grilling: speeds classify ~3× but leaves the judge call on the same throttle-inducing 8B, so wall-clock for a non-trivial run is still dominated by judge × throttle. Half-measure.
- **Qwen 3 1.7B for both call sites** — rejected: judge quality on German listings and JSON-mode adherence are marginal at 1.7B; the resulting `Match Verdict` is the artifact the applicant actually reads. False economy.
- **Qwen 3 4B for both call sites** — rejected: 4B is overkill for the binary classify call and costs roughly 4× more per call than 0.6B at no quality gain. The two-model split is cheaper without losing anything that matters.
- **Qwen 2.5 7B Instruct (the original choice)** — superseded earlier: same cost class as Qwen 3 8B with weaker JSON-mode adherence.

## Consequences

- Prompts must be tuned against the picked model at each call site — quality won't match Claude or 8B-Qwen. Acceptable for binary classify and structured-JSON judgment over short German/English listings; revisit if false-classification or false-tier rate becomes a problem in practice. The per-language prompt files (per ADR-0006) must include `/no_think` to suppress Qwen 3 thinking output.
- **Ollama** is a hard runtime dependency on the Pi. `ollama pull qwen3:0.6b` *and* `ollama pull qwen3:4b` are manual setup steps documented in the Pi deployment runbook.
- The `LLMExtractor` Protocol stays in the codebase so tests can mock it cheaply.
- The earlier "documented downgrade path to `qwen3:1.7b` for classify" is retired — the new defaults *are* the downgraded state. The escape hatch in the *other* direction (upgrade to `qwen3:8b` for judge, or to a larger Qwen 3 release if Pi 6 lands with substantially better sustained throughput) remains a config edit. There is no in-code fallback.
- Total RAM footprint ~3 GB (0.6B + 4B both resident with `keep_alive`), leaving ~5 GB headroom on the 8 GB Pi 5 for the rest of the pipeline and OS — substantially more than the ~280 MB free observed with 8B resident.
- Active cooling on the Pi 5 is still recommended but no longer load-bearing for correctness — the smaller models pin the CPU for shorter bursts, so thermal pressure drops naturally. Mid-run `vcgencmd get_throttled` should report `0x0` under the new defaults; if it doesn't, cooling needs revisiting independently.
