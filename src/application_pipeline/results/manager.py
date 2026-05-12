from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import ResultsFileError

_POSITION_HEADER = re.compile(r"^## (\d+)\.", re.MULTILINE)


class ResultsFileManager:
    def __init__(self, path: Path, file_header: str) -> None:
        self._path = path
        self._file_header = file_header
        self._next_position: int | None = None

    def ensure_initialized(self) -> None:
        try:
            if self._path.exists() and self._path.stat().st_size > 0:
                text = self._path.read_text(encoding="utf-8")
                numbers = [int(m.group(1)) for m in _POSITION_HEADER.finditer(text)]
                self._next_position = max(numbers) + 1 if numbers else 1
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(self._file_header, encoding="utf-8")
        except OSError as exc:
            raise ResultsFileError(str(exc)) from exc
        self._next_position = 1

    def next_position_number(self) -> int:
        if self._next_position is None:
            raise ResultsFileError(
                f"next_position_number called before ensure_initialized: {self._path}"
            )
        n = self._next_position
        self._next_position += 1
        return n

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


def load(path: Path, file_header: str) -> ResultsFileManager:
    return ResultsFileManager(path, file_header)
