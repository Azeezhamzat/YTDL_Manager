from __future__ import annotations

from ytdlmanager.retry import backoff_delay_seconds, should_retry


def test_backoff_delay_grows_exponentially():
    d1 = backoff_delay_seconds(1, base=2.0, cap=1000.0)
    d2 = backoff_delay_seconds(2, base=2.0, cap=1000.0)
    d3 = backoff_delay_seconds(3, base=2.0, cap=1000.0)
    assert d1 == 2.0
    assert d2 == 4.0
    assert d3 == 8.0


def test_backoff_delay_is_capped():
    delay = backoff_delay_seconds(10, base=2.0, cap=10.0)
    assert delay == 10.0


def test_backoff_delay_clamps_low_attempt_numbers():
    assert backoff_delay_seconds(0) == backoff_delay_seconds(1)
    assert backoff_delay_seconds(-5) == backoff_delay_seconds(1)


def test_should_retry_transient_failure_within_budget():
    assert should_retry(attempt=1, max_attempts=3, is_permanent=False) is True
    assert should_retry(attempt=2, max_attempts=3, is_permanent=False) is True


def test_should_retry_transient_failure_exhausted_budget():
    assert should_retry(attempt=3, max_attempts=3, is_permanent=False) is False


def test_should_retry_permanent_failure_never_retries():
    assert should_retry(attempt=1, max_attempts=5, is_permanent=True) is False
