from __future__ import annotations


class HttpRetryError(Exception):
    """All retries exhausted."""


class HttpNotRetryableError(Exception):
    """HTTP error that must not be retried (e.g. 404, auth failure)."""


class HttpStubNotRetryableError(HttpNotRetryableError):
    """This URL is unrecoverable — skip the stub, continue the parser."""


class HttpParserFatalError(HttpNotRetryableError):
    """This parser cannot continue (auth failure, unexpected server error)."""
