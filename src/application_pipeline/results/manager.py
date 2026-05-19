from __future__ import annotations

import os
from pathlib import Path

from .errors import ResultsFileError


def ensure_initialized(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ResultsFileError(str(exc)) from exc


def append(path: Path, text: str) -> None:
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        raise ResultsFileError(
            f"append failed — results file may be corrupt, manual intervention may be required: {path}"
        ) from exc
