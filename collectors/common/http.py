import time
from collections.abc import Callable
from typing import TypeVar

import httpx

T = TypeVar("T")

USER_AGENT = "us-econ-collector/0.1 (+https://github.com/)"


def client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


def with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 2.0,
    retry_on_status: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> T:
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in retry_on_status or i == attempts - 1:
                raise
            last_exc = exc
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            if i == attempts - 1:
                raise
            last_exc = exc
        time.sleep(base_delay * (2**i))
    raise RuntimeError("with_retries exhausted") from last_exc
