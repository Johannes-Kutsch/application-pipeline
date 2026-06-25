import json
import shutil
import subprocess
import tempfile
from typing import Any, Protocol

from application_pipeline.llm import quota as _quota
from .agent_runtime_types import (
    AgentRuntimeResponse,
    UsageLimitError,
    _ProviderForensicsError,
)

_USAGE_LIMIT_PHRASES = ("usage limit", "rate limit")


class _ClaudeClassifiedError(_ProviderForensicsError):
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


class SubprocessRunner(Protocol):
    def __call__(self, args: list[str], stdin: str) -> tuple[int, str, str]: ...


def _default_runner(args: list[str], stdin: str) -> tuple[int, str, str]:
    # cwd is forced to a neutral tempdir so the harness does not discover the
    # project CLAUDE.md or auto-memory keyed off the calling cwd. See ADR-0038.
    proc = subprocess.run(
        args,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tempfile.gettempdir(),
    )
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

    def call(
        self,
        prompt: str,
        *,
        model: str,
        effort: str = "",
    ) -> AgentRuntimeResponse:
        cli = self._cli_path or shutil.which("claude") or "claude"
        args = [
            cli,
            "-p",
            "-",
            "--output-format",
            "json",
            "--model",
            model,
            "--no-session-persistence",
            "--disable-slash-commands",
            "--tools",
            "",
            "--setting-sources",
            "user",
        ]
        if effort:
            args += ["--effort", effort]

        returncode, stdout, stderr = self._runner(args, prompt)

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
            result_text = str(envelope.get("result", ""))
            raise UsageLimitError(
                f"Claude subscription cap reached: {result_text}",
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                envelope=envelope,
                reset_time=_quota.parse_reset_time(result_text),
            )

        if returncode != 0:
            if _signals_usage_limit_stderr(stderr):
                raise UsageLimitError(
                    f"Claude usage limit signalled in stderr: {stderr.strip()[:200]}",
                    returncode=returncode,
                    stdout=stdout,
                    stderr=stderr,
                    envelope=envelope,
                    reset_time=_quota.parse_reset_time(stderr),
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

        return AgentRuntimeResponse(
            raw_response=raw_response,
        )
