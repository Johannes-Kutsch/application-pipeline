# Agent Runtime native logs under LLM log subdirectories

Provider-level logs for production **LLM Extractor** calls use Agent Runtime's native `.log` format instead of preserving the old Claude transcript/event JSONL shape. Each Agent Runtime invocation gets one log file under the existing `<settings-dir>/.runtime-data/logs/` tree, separated by call site: classifier logs under `llm/agent-runtime/classify/`, **Match Judge** logs under `llm/agent-runtime/judge/`.

This keeps the operator's log location stable while accepting that the runtime now owns provider-event shape. Pipeline-owned summaries, counters, failure reports, and run-level events remain pipeline-owned **Log Artifacts**.

Because runtime logs are one file per invocation rather than append-forever files, maintenance deletes Agent Runtime `.log` files older than 30 days instead of tail-truncating them. Pipeline-owned log files keep their existing tail-retention behavior.
