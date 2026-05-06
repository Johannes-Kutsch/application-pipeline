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

    def ensure_initialized(self) -> None:
        if self._path.exists() and self._path.stat().st_size > 0:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self._file_header, encoding="utf-8")

    def next_position_number(self) -> int:
        if not self._path.exists() or self._path.stat().st_size == 0:
            raise ResultsFileError(f"results file missing or empty: {self._path}")
        text = self._path.read_text(encoding="utf-8")
        numbers = [int(m.group(1)) for m in _POSITION_HEADER.finditer(text)]
        return max(numbers) + 1 if numbers else 1

    def append(self, rendered_block: str) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(rendered_block)
            f.flush()
            os.fsync(f.fileno())


def load(path: Path, file_header: str) -> ResultsFileManager:
    return ResultsFileManager(path, file_header)
