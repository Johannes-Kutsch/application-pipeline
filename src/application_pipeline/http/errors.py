from __future__ import annotations


class HttpRetryError(Exception):
    """All retries exhausted."""


class HttpNotRetryableError(Exception):
    """HTTP error that must not be retried (e.g. 404, auth failure)."""
