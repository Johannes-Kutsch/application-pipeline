# Local Ollama as the LLM backend

The **LLM Extractor** (requirements extraction + **Relevance Classifier**) runs against a local **Ollama** instance using **Qwen 2.5 7B Instruct (Q4_K_M)**. The same backend is used on the laptop (v1) and the Pi 5 8GB (v1.1).

## Why

- **No Anthropic API budget.** The applicant has a Claude Code subscription only — the Anthropic API is off-limits for this project's runtime.
- **Pi cron has no Claude Code session.** The Haiku-via-subagent pattern (used in the applicant's `pycastle` project) only works inside an interactive Claude Code session; cron can't trigger one. Any LLM call that must work unattended on the Pi has to be a normal HTTP call to a process the Pi controls.
- **Single code path.** Picking a backend that runs locally on both laptop and Pi avoids a v1→v1.1 rewrite of the LLM-call layer and lets v1 already validate the production extraction quality before deployment.
- **Pi 5 8GB comfortably runs a 7–8B Q4 model** (~5 GB resident), with headroom for the rest of the pipeline.

## Considered alternatives

- **Anthropic API direct** — rejected: no budget.
- **Haiku via Claude Code subagent** — rejected: doesn't run on Pi cron, would force a v1.1 rewrite.
- **Pi-runs-keyword-only, laptop-runs-LLM-after-grab** — rejected: splits the pipeline awkwardly, adds a manual step, and the Pi already has the hardware.

## Consequences

- Prompts must be tuned against Qwen 2.5 7B specifically — quality won't match Claude. Acceptable for structured extraction over short German listings; revisit if false-classification rate becomes a problem.
- **Ollama** is now a hard runtime dependency on every machine the pipeline runs on.
- The `LLMExtractor` Protocol stays in the codebase so tests can mock it cheaply, even though there is only one production implementation.
