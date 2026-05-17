from __future__ import annotations

import os
from pathlib import Path

from .errors import ResultsFileError

FILE_HEADER = """\
<!-- schema-version: 1 -->
<!-- Delete this file and re-run the pipeline to reset -->

"""


class ResultsFileManager:
    def __init__(self, path: Path) -> None:
        self._path = path

    def ensure_initialized(self) -> None:
        try:
            if self._path.exists() and self._path.stat().st_size > 0:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(FILE_HEADER, encoding="utf-8")
        except OSError as exc:
            raise ResultsFileError(str(exc)) from exc

    def append(self, rendered_block: str) -> None:
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(rendered_block)
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            raise ResultsFileError(
                f"append failed — results file may be corrupt, manual intervention may be required: {self._path}"
            ) from exc


def load(path: Path) -> ResultsFileManager:
    return ResultsFileManager(path)
