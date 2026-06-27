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


def stash_malformed_classify_exception(
    *,
    filesystem_root: Path,
    stub: PositionStub,
    error: MalformedClassifyError,
    agent_runtime_log_pointer: str | Path | None = None,
) -> Path:
    return stash_malformed_classify_artifact(
        filesystem_root=filesystem_root,
        listing=ListingDiagnosticFacts(
            source=stub.source,
            url=stub.url,
            title=stub.title,
        ),
        error_classification=type(error).__name__,
        error_message=str(error),
        agent_runtime_log_pointer=agent_runtime_log_pointer,
        raw_model_output=getattr(error, "raw_response", None),
    )


def stash_malformed_classify_verdict(
    *,
    filesystem_root: Path,
    stub: PositionStub,
    agent_runtime_log_pointer: str | Path | None = None,
) -> Path:
    return stash_malformed_classify_artifact(
        filesystem_root=filesystem_root,
        listing=ListingDiagnosticFacts(
            source=stub.source,
            url=stub.url,
            title=stub.title,
        ),
        error_classification="malformed_classifier_verdict",
        error_message="malformed classifier verdict",
        agent_runtime_log_pointer=agent_runtime_log_pointer,
    )
