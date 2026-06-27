from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["ListingDiagnosticFacts", "stash_malformed_classify_artifact"]


@dataclass(frozen=True)
class ListingDiagnosticFacts:
    source: str
    url: str
    title: str | None = None


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
