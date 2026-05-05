# Local Ollama as the LLM backend

The **LLM Extractor** (**Relevance Classifier** + **Match Judge**) runs against a local **Ollama** instance using **Qwen 2.5 7B Instruct (Q4_K_M)** on the Pi 5 8GB. The Pi is the only machine that runs the LLM; the laptop is used only to smoke-test **Parsers** in isolation during development.

## Why

- **No Anthropic API budget.** The applicant has a Claude Code subscription only — the Anthropic API is off-limits for this project's runtime.
- **Pi cron has no Claude Code session.** A Claude-Code-subagent pattern only works inside an interactive session; cron can't trigger one. Any LLM call that must work unattended on the Pi has to be a normal HTTP call to a process the Pi controls.
- **Pi 5 8GB comfortably runs a 7–8B Q4 model** (~5 GB resident), with headroom for the rest of the pipeline.
- **One runtime, one backend.** Since the laptop never runs the LLM, there is no laptop-vs-Pi backend split to worry about and no v1→v1.1 rewrite of the LLM-call layer.

## Considered alternatives

- **Anthropic API direct** — rejected: no budget.
- **Haiku via Claude Code subagent** — rejected: doesn't run unattended on Pi cron.
- **Pi-runs-keyword-only, laptop-runs-LLM-after-grab** — rejected: splits the pipeline awkwardly, adds a manual step, and the Pi already has the hardware.

## Consequences

- Prompts must be tuned against Qwen 2.5 7B specifically — quality won't match Claude. Acceptable for structured judgment over short German/English listings; revisit if false-classification or false-tier rate becomes a problem.
- **Ollama** is a hard runtime dependency on the Pi.
- The `LLMExtractor` Protocol stays in the codebase so tests can mock it cheaply, even though there is only one production implementation.
