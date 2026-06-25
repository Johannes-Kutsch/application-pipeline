from dataclasses import dataclass
from datetime import datetime
from typing import Any


class _ProviderForensicsError(Exception):
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


class UsageLimitError(_ProviderForensicsError):
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
class AgentRuntimeResponse:
    raw_response: str
