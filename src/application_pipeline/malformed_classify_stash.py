from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["ListingDiagnosticFacts", "stash_malformed_classify_artifact"]


@dataclass(frozen=True)
class ListingDiagnosticFacts:
    source: str
    url: str


def stash_malformed_classify_artifact(
    *,
    filesystem_root: Path,
    listing: ListingDiagnosticFacts,
    error_classification: str,
    error_message: str,
    agent_runtime_log_pointer: Path | None = None,
    raw_model_output: str | None = None,
) -> None:
    stash_dir = filesystem_root / "malformed"
    stash_dir.mkdir(parents=True, exist_ok=True)

    slug = listing.url.replace("https://", "").replace("http://", "").replace("/", "-")
    markdown_path = stash_dir / f"{listing.source}-{slug}.md"

    lines: list[str] = [
        f"**Source:** {listing.source}",
        f"**URL:** {listing.url}",
        f"**Error:** {error_message}",
    ]
    if agent_runtime_log_pointer is not None:
        lines += ["", "## Agent Runtime Log", "", str(agent_runtime_log_pointer)]
    markdown_path.write_text("\n".join(lines), encoding="utf-8")

    if raw_model_output is not None:
        raw_output_path = stash_dir / f"{listing.source}-{slug}.txt"
        raw_output_path.write_text(raw_model_output, encoding="utf-8")

    # This seam accepts structured error classification for future callers even
    # though the current stash format remains message-only in this slice.
    _ = error_classification
