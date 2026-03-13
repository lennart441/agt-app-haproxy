"""Tests for per-IP and per-mail rate limiting.

Per-IP rate-limit values (stick-table sc2 + rate-limits.map):
  api_get       20 req / 300 s
  api_report    10 req / 60 s
  api_primaer   120 req / 300 s
  api_primaer_reqcode  30 req / 300 s
  api_primaer_verify   20 req / 600 s

Per-mail rate-limit values (stick-table sc4 + rate-limits.map):
  api_primaer_reqcode_mail  1 req / 120 s
  api_primaer_verify_mail   5 req / 600 s
"""

import requests

from conftest import set_map, RATE_LIMITS_MAP, restore_rate_limits

API_HOST = "agt-1.agt-app.de"


def _send(base_url, path, n, host=API_HOST):
    """Send *n* requests and return list of status codes."""
    codes = []
    for _ in range(n):
        r = requests.get(
            f"{base_url}{path}",
            headers={"Host": host},
            verify=False, timeout=5,
        )
        codes.append(r.status_code)
    return codes


def test_api_get_rate_limit(base_url):
    """20 requests pass, 21st gets 429 (api_get limit = 20 / 300 s)."""
    codes = _send(base_url, "/v3/agt-get-api/test", 21)
    assert all(c == 200 for c in codes[:20]), f"Expected 200s: {codes[:20]}"
    assert codes[20] == 429


def test_api_report_rate_limit(base_url):
    """10 requests pass, 11th gets 429 (api_report limit = 10 / 60 s)."""
    codes = _send(base_url, "/v3/report/test", 11)
    assert all(c == 200 for c in codes[:10]), f"Expected 200s: {codes[:10]}"
    assert codes[10] == 429


def test_api_primaer_verify_rate_limit(base_url):
    """20 requests pass, 21st gets 429 (api_primaer_verify limit = 20 / 600 s)."""
    codes = _send(base_url, "/v3/pri-api/verify-code/test", 21)
    assert all(c == 200 for c in codes[:20]), f"Expected 200s: {codes[:20]}"
    assert codes[20] == 429


def test_rate_limit_response_format(base_url):
    """429 response has correct JSON body and Content-Type."""
    _send(base_url, "/v3/report/test", 10)  # exhaust limit
    r = requests.get(
        f"{base_url}/v3/report/test",
        headers={"Host": API_HOST},
        verify=False, timeout=5,
    )
    assert r.status_code == 429
    assert "application/json" in r.headers.get("Content-Type", "")
    body = r.json()
    assert body["error"] == "rate_limit_exceeded"
    assert body["http_status"] == 429


def test_different_endpoints_independent(base_url):
    """Exhausting api_report does not affect api_get."""
    _send(base_url, "/v3/report/test", 11)  # exhaust report
    r = requests.get(
        f"{base_url}/v3/agt-get-api/test",
        headers={"Host": API_HOST},
        verify=False, timeout=5,
    )
    assert r.status_code == 200


def test_rate_limit_with_lowered_value(base_url):
    """Runtime API 'set map' can lower a rate limit; requests are blocked earlier."""
    try:
        set_map(RATE_LIMITS_MAP, "api_get", "3")
        codes = _send(base_url, "/v3/agt-get-api/test", 5)
        assert codes[:3] == [200, 200, 200]
        assert 429 in codes[3:]
    finally:
        restore_rate_limits()


def test_website_rate_limit_high_threshold(base_url):
    """Website limit (2000 req/s) is not hit by a small burst."""
    codes = _send(base_url, "/", 20, host="agt-app.de")
    assert all(c == 200 for c in codes)


# ---------------------------------------------------------------------------
# Per-mail rate limiting (sc4 + X-Rate-Email header)
# ---------------------------------------------------------------------------

def _send_with_email(base_url, path, n, email, host=API_HOST):
    """Send *n* requests with X-Rate-Email header, return list of status codes."""
    codes = []
    for _ in range(n):
        r = requests.get(
            f"{base_url}{path}",
            headers={"Host": host, "X-Rate-Email": email},
            verify=False, timeout=5,
        )
        codes.append(r.status_code)
    return codes


def test_primaer_reqcode_per_mail_limit(base_url):
    """1 request-code request per mail passes, 2nd gets 429."""
    codes = _send_with_email(
        base_url, "/v3/pri-api/request-code/test", 2, "test@example.de",
    )
    assert codes[0] == 200
    assert codes[1] == 429


def test_primaer_verify_per_mail_limit(base_url):
    """5 verify-code requests per mail pass, 6th gets 429."""
    codes = _send_with_email(
        base_url, "/v3/pri-api/verify-code/test", 6, "test@example.de",
    )
    assert all(c == 200 for c in codes[:5]), f"Expected 200s: {codes[:5]}"
    assert codes[5] == 429


def test_per_mail_different_mails_independent(base_url):
    """Exhausting limit for one mail does not block a different mail."""
    _send_with_email(
        base_url, "/v3/pri-api/request-code/test", 2, "a@example.de",
    )
    codes = _send_with_email(
        base_url, "/v3/pri-api/request-code/test", 1, "b@example.de",
    )
    assert codes[0] == 200


def test_per_mail_and_per_ip_independent(base_url):
    """Per-mail and per-IP limits are tracked independently.

    Exhaust per-mail limit (1 req) for request-code, then send without
    X-Rate-Email: per-IP limit (30 req) must still allow the request.
    """
    _send_with_email(
        base_url, "/v3/pri-api/request-code/test", 2, "test@example.de",
    )
    r = requests.get(
        f"{base_url}/v3/pri-api/request-code/test",
        headers={"Host": API_HOST},
        verify=False, timeout=5,
    )
    assert r.status_code == 200


def test_no_email_header_skips_mail_limit(base_url):
    """Without X-Rate-Email, only the per-IP limit applies (30/300s)."""
    codes = _send(base_url, "/v3/pri-api/request-code/test", 5)
    assert all(c == 200 for c in codes)
