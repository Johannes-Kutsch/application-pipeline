from __future__ import annotations

import contextvars

current_stage: contextvars.ContextVar[str] = contextvars.ContextVar(
    "application_pipeline.current_stage", default="pipeline_orchestrator"
)
