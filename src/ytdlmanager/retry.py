"""Bounded exponential backoff for transient download failures."""

from __future__ import annotations

from .config import DEFAULT_RETRY_BASE_DELAY_SECONDS, DEFAULT_RETRY_MAX_DELAY_SECONDS


def backoff_delay_seconds(
    attempt: int,
    *,
    base: float = DEFAULT_RETRY_BASE_DELAY_SECONDS,
    cap: float = DEFAULT_RETRY_MAX_DELAY_SECONDS,
) -> float:
    """Delay before retry attempt `attempt` (1-indexed: the delay before the
    *first* retry, i.e. after attempt 1 has failed, is `attempt == 1`).
    """
    if attempt < 1:
        attempt = 1
    delay = base * (2 ** (attempt - 1))
    return min(delay, cap)


def should_retry(attempt: int, max_attempts: int, is_permanent: bool) -> bool:
    if is_permanent:
        return False
    return attempt < max_attempts
