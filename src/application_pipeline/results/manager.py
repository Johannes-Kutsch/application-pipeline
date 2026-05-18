from __future__ import annotations

import os
from pathlib import Path

from .errors import ResultsFileError

FILE_HEADER = """\
<!-- schema-version: 1 -->
<!-- Delete this file and re-run the pipeline to reset -->

"""


def ensure_initialized(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > 0:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FILE_HEADER, encoding="utf-8")
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


class ResultsFileManager:
    def __init__(self, path: Path) -> None:
        self._path = path

    def ensure_initialized(self) -> None:
        ensure_initialized(self._path)

    def append(self, rendered_block: str) -> None:
        append(self._path, rendered_block)


def load(path: Path) -> ResultsFileManager:
    return ResultsFileManager(path)
