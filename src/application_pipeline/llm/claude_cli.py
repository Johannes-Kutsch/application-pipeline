import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Protocol

_USAGE_LIMIT_PHRASES = ("usage limit", "rate limit")


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
    pass


class _ClaudeClassifiedError(_ClaudeCliForensicsError):
    def __init__(
        self,
        message: str,
        *,
        returncode: int,
        stdout: str,
        stderr: str,
        envelope: dict[str, Any] | None,
        envelope_error_class: str,
    ) -> None:
        super().__init__(
            message,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            envelope=envelope,
        )
        self.envelope_error_class = envelope_error_class


class ClaudeCliError(_ClaudeClassifiedError):
    pass


class ClaudeMalformedEnvelopeError(_ClaudeClassifiedError):
    pass


@dataclass(frozen=True)
class ClaudeUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


@dataclass(frozen=True)
class ClaudeResponse:
    parsed_result: Any
    raw_response: str
    usage: ClaudeUsage
    cost_usd: float
    duration_s: float
    session_id: str


class SubprocessRunner(Protocol):
    def __call__(self, args: list[str], stdin: str) -> tuple[int, str, str]: ...


def _default_runner(args: list[str], stdin: str) -> tuple[int, str, str]:
    proc = subprocess.run(args, input=stdin, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _signals_usage_limit_envelope(envelope: dict[str, Any]) -> bool:
    if not envelope.get("is_error"):
        return False
    result_lower = str(envelope.get("result", "")).lower()
    return any(phrase in result_lower for phrase in _USAGE_LIMIT_PHRASES)


def _signals_usage_limit_stderr(stderr: str) -> bool:
    stderr_lower = stderr.lower()
    return any(phrase in stderr_lower for phrase in _USAGE_LIMIT_PHRASES)


class ClaudeCliInvoker:
    # Prompts are delivered via stdin: the CLI is invoked as
    #   claude -p - --output-format json
    # with the prompt written to the process's stdin.  This avoids ARG_MAX
    # limits for multi-KB prompts and keeps user content off the command line.

    def __init__(
        self,
        *,
        cli_path: str | None = None,
        _runner: SubprocessRunner | None = None,
    ) -> None:
        self._cli_path = cli_path
        self._runner: SubprocessRunner = _runner or _default_runner

    def call(self, prompt: str, language: str) -> ClaudeResponse:
        cli = self._cli_path or shutil.which("claude") or "claude"
        args = [cli, "-p", "-", "--output-format", "json"]

        t0 = time.monotonic()
        returncode, stdout, stderr = self._runner(args, prompt)
        duration_s = time.monotonic() - t0

        try:
            envelope = json.loads(stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ClaudeMalformedEnvelopeError(
                f"envelope JSON unparseable: {exc}",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                envelope=None,
                envelope_error_class="envelope_not_json",
            ) from exc

        if not isinstance(envelope, dict):
            raise ClaudeMalformedEnvelopeError(
                "envelope is not a JSON object",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                envelope=None,
                envelope_error_class="envelope_not_object",
            )

        if _signals_usage_limit_envelope(envelope):
            raise ClaudeUsageLimitError(
                f"Claude subscription cap reached: {envelope.get('result', '')}",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                envelope=envelope,
            )

        if returncode != 0:
            if _signals_usage_limit_stderr(stderr):
                raise ClaudeUsageLimitError(
                    f"Claude usage limit signalled in stderr: {stderr.strip()[:200]}",
                    returncode=returncode,
                    stdout=stdout,
                    stderr=stderr,
                    envelope=envelope,
                )
            raise ClaudeCliError(
                f"claude CLI exited {returncode}: "
                f"{stderr.strip() or str(envelope.get('result', ''))}",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                envelope=envelope,
                envelope_error_class="cli_nonzero_exit",
            )

        raw_response = str(envelope.get("result", ""))
        if not raw_response:
            raise ClaudeCliError(
                "claude CLI returned empty result field",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                envelope=envelope,
                envelope_error_class="empty_result",
            )

        try:
            parsed_result = json.loads(raw_response)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ClaudeCliError(
                f"result field is not valid JSON: {exc}",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                envelope=envelope,
                envelope_error_class="result_not_json",
            ) from exc

        usage_raw = envelope.get("usage", {})
        usage = ClaudeUsage(
            input_tokens=int(usage_raw.get("input_tokens", 0)),
            output_tokens=int(usage_raw.get("output_tokens", 0)),
            cache_read_tokens=int(usage_raw.get("cache_read_input_tokens", 0)),
        )

        return ClaudeResponse(
            parsed_result=parsed_result,
            raw_response=raw_response,
            usage=usage,
            cost_usd=float(envelope.get("total_cost_usd", 0.0)),
            duration_s=duration_s,
            session_id=str(envelope.get("session_id", "")),
        )
