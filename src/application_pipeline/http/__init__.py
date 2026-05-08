from typing import Any, Callable

HttpPost = Callable[[str, dict[str, Any], float], dict[str, Any]]


class HttpRetryError(Exception):
    pass


def post_with_retries(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    retries: int,
    http_post: HttpPost,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            return http_post(url, payload, timeout)
        except Exception as exc:
            last_exc = exc
    raise HttpRetryError(
        f"HTTP request failed after {retries} retries: {last_exc}"
    ) from last_exc
