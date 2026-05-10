"""Shared HTTP constants for all parser HTTP clients."""

HTTP_CONNECT_TIMEOUT: float = 5.0
HTTP_READ_TIMEOUT: float = 30.0
MAX_RETRIES: int = 3
BACKOFF_INITIAL: float = 1.0
BACKOFF_MULTIPLIER: float = 2.0
BACKOFF_MAX: float = 8.0
RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
REQUEST_PACING: float = 0.5
USER_AGENT: str = "application-pipeline/0.1 (job-discovery-bot)"
