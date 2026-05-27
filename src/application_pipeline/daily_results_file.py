from __future__ import annotations

import os
from pathlib import Path

from application_pipeline.results.errors import ResultsFileError


def _render_card(rank: int, header: str, summary: str, url: str, body: str) -> str:
    lines = header.split("\n", 1)
    title = lines[0]
    metadata = lines[1] if len(lines) > 1 else ""
    return (
        f"# **{rank}:** {title}\n"
        f"\n"
        f"{metadata}\n"
        f"{url}\n"
        f"\n"
        f"{summary}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"{body}\n"
        f"\n"
        f"---\n"
    )


class DailyResultsFile:
    def __init__(self, path: Path) -> None:
        self._path = path

    def ensure_initialized(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ResultsFileError(str(exc)) from exc

    def commit(
        self, *, rank: int, header: str, summary: str, url: str, body: str
    ) -> None:
        text = _render_card(
            rank=rank, header=header, summary=summary, url=url, body=body
        )
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            raise ResultsFileError(
                f"append failed — results file may be corrupt, manual intervention may be required: {self._path}"
            ) from exc
