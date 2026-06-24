# Agent Runtime native logs under LLM log subdirectories

Superseded by ADR-0057: the runtime no longer writes `.log` files; the pipeline now serializes returned `InvocationRecord` evidence into per-call directories. The classify/judge subdir locations and the "missing/empty logs are diagnostic gaps" stance survive; the "runtime owns the file/shape" premise and the single-file-per-invocation layout do not.

Provider-level logs for production **LLM Extractor** calls use Agent Runtime's native `.log` format instead of preserving the old Claude transcript/event JSONL shape. Each Agent Runtime invocation gets one log file under the existing `<settings-dir>/.runtime-data/logs/` tree, separated by call site: classifier logs under `llm/agent-runtime/classify/`, **Match Judge** logs under `llm/agent-runtime/judge/`. These `.log` files are the only production prompt/response evidence for classifier and judge calls.

This keeps the operator's log location stable while accepting that the runtime now owns provider-event shape. Pipeline-owned counters, failure reports, and run-level events remain pipeline-owned **Log Artifacts**; pipeline-owned LLM transcript JSONL is retired.

Because runtime logs are one file per invocation rather than append-forever files, maintenance deletes Agent Runtime `.log` files older than 30 days instead of tail-truncating them. Pipeline-owned log files keep their existing tail-retention behavior.
