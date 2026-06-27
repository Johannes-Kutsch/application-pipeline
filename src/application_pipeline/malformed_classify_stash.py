from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from application_pipeline.llm.types import (
    ExtractorBatchMalformedError,
    ExtractorMalformedError,
    ExtractorMalformedJSONError,
)
from application_pipeline.parsers.types import PositionStub

__all__ = [
    "ListingDiagnosticFacts",
    "stash_malformed_classify_artifact",
    "stash_malformed_classify_exception",
    "stash_malformed_classify_verdict",
]


@dataclass(frozen=True)
class ListingDiagnosticFacts:
    source: str
    url: str
    title: str | None = None


MalformedClassifyError = (
    ExtractorBatchMalformedError | ExtractorMalformedError | ExtractorMalformedJSONError
)


def stash_malformed_classify_artifact(
    *,
    filesystem_root: Path,
    listing: ListingDiagnosticFacts,
    error_classification: str,
    error_message: str,
    agent_runtime_log_pointer: str | Path | None = None,
    raw_model_output: str | None = None,
) -> Path:
    stash_dir = filesystem_root / "malformed"
    stash_dir.mkdir(parents=True, exist_ok=True)

    slug = listing.url.replace("https://", "").replace("http://", "").replace("/", "-")
    markdown_path = stash_dir / f"{listing.source}-{slug}.md"

    lines: list[str] = [
        f"**Source:** {listing.source}",
        f"**URL:** {listing.url}",
    ]
    if listing.title is not None:
        lines.append(f"**Title:** {listing.title}")
    lines.extend(
        [
            f"**Error Classification:** {error_classification}",
            f"**Error:** {error_message}",
        ]
    )
    if agent_runtime_log_pointer is not None:
        lines += ["", "## Agent Runtime Log", "", str(agent_runtime_log_pointer)]
    if raw_model_output is not None and raw_model_output.strip():
        lines += ["", "## Raw Model Output", "", "```text", raw_model_output, "```"]
    markdown_path.write_text("\n".join(lines), encoding="utf-8")

    return markdown_path


def _stash_malformed_classify_listing(
    *,
    filesystem_root: Path,
    stub: PositionStub,
    error: MalformedClassifyError | None = None,
    agent_runtime_log_pointer: str | Path | None = None,
    raw_description: str | None = None,
) -> Path:
    if error is None:
        error_classification = "malformed_classifier_verdict"
        error_message = "malformed classifier verdict"
        raw_model_output = None
    else:
        error_classification = type(error).__name__
        error_message = str(error)
        raw_model_output = _apply_raw_output_policy(
            getattr(error, "raw_response", None),
            prompt=getattr(error, "prompt", None),
            raw_description=raw_description,
        )

    return stash_malformed_classify_artifact(
        filesystem_root=filesystem_root,
        listing=ListingDiagnosticFacts(
            source=stub.source,
            url=stub.url,
            title=stub.title,
        ),
        error_classification=error_classification,
        error_message=error_message,
        agent_runtime_log_pointer=agent_runtime_log_pointer,
        raw_model_output=raw_model_output,
    )


def _apply_raw_output_policy(
    raw_model_output: str | None,
    *,
    prompt: str | None,
    raw_description: str | None,
) -> str | None:
    if raw_model_output is None:
        return None

    sanitized = raw_model_output
    for blocked_text in (prompt, raw_description):
        if blocked_text and blocked_text.strip():
            sanitized = sanitized.replace(blocked_text, "")

    sanitized = sanitized.strip()
    return sanitized or None


def stash_malformed_classify_exception(
    *,
    filesystem_root: Path,
    stub: PositionStub,
    error: MalformedClassifyError,
    agent_runtime_log_pointer: str | Path | None = None,
    raw_description: str | None = None,
) -> Path:
    return _stash_malformed_classify_listing(
        filesystem_root=filesystem_root,
        stub=stub,
        error=error,
        agent_runtime_log_pointer=agent_runtime_log_pointer,
        raw_description=raw_description,
    )


def stash_malformed_classify_verdict(
    *,
    filesystem_root: Path,
    stub: PositionStub,
    agent_runtime_log_pointer: str | Path | None = None,
) -> Path:
    return _stash_malformed_classify_listing(
        filesystem_root=filesystem_root,
        stub=stub,
        agent_runtime_log_pointer=agent_runtime_log_pointer,
    )
