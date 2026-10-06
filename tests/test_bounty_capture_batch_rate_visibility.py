from types import SimpleNamespace

from concierge.bounty_capture_batch import _BatchSession


def test_batch_session_records_healthy_rate_limit_headers():
    session = _BatchSession(SimpleNamespace(), max_requests=10)
    response = SimpleNamespace(
        status_code=200,
        headers={
            "X-RateLimit-Remaining": "4871",
            "X-RateLimit-Reset": "1791249999",
        },
    )

    session._record_response(response)

    assert session.rate_limit_remaining == 4871
    assert session.rate_limit_reset_at == 1791249999
    assert session.request_headroom_reserved is False
    assert session.rate_limited is False
