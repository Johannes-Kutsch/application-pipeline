from dataclasses import dataclass
from datetime import datetime
from typing import Any


class _ClaudeCliForensicsError(Exception):
    def __init__(
        self,
        message: str,
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        envelope: dict[str, Any] | None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.envelope = envelope


class ClaudeUsageLimitError(_ClaudeCliForensicsError):
    def __init__(
        self,
        message: str,
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        envelope: dict[str, Any] | None,
        reset_time: datetime | None = None,
    ) -> None:
        super().__init__(
            message,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            envelope=envelope,
        )
        self.reset_time = reset_time


@dataclass(frozen=True)
class ClaudeUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


@dataclass(frozen=True)
class ClaudeResponse:
    raw_response: str
    usage: ClaudeUsage
    cost_usd: float
    duration_s: float
    session_id: str
