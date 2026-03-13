"""Tests for global backend overload protection (sc3 + overload-limits.map).

The overload check uses st_overload (http_req_rate(1s)) and compares against
per-backend thresholds from overload-limits.map.  Since the check sits AFTER
the per-IP rate-limit deny, rate-limited requests don't inflate the counter.
"""

import time

import requests

from conftest import (
    set_map,
    RATE_LIMITS_MAP,
    OVERLOAD_LIMITS_MAP,
    restore_rate_limits,
    restore_overload_limits,
)

API_HOST = "agt-1.agt-app.de"


def test_overload_503_response(base_url):
    """Exceeding the global RPS limit triggers 503 with overload JSON body."""
    try:
        # Raise per-IP limit so it doesn't interfere
        set_map(RATE_LIMITS_MAP, "api_report", "9999")
        # Lower overload limit to something easily exceeded
        set_map(OVERLOAD_LIMITS_MAP, "api_report", "3")
        time.sleep(0.2)

        statuses = []
        for _ in range(8):
            r = requests.get(
                f"{base_url}/v3/report/test",
                headers={"Host": API_HOST},
                verify=False, timeout=5,
            )
            statuses.append(r.status_code)

        assert 503 in statuses, f"Expected at least one 503: {statuses}"

        # Verify the overload response format
        idx_503 = statuses.index(503)
        r = requests.get(
            f"{base_url}/v3/report/test",
            headers={"Host": API_HOST},
            verify=False, timeout=5,
        )
        if r.status_code == 503:
            assert "application/json" in r.headers.get("Content-Type", "")
            body = r.json()
            assert body["error"] == "backend_overload"
            assert r.headers.get("Retry-After") == "5"
    finally:
        restore_rate_limits()
        restore_overload_limits()


def test_overload_different_backends_independent(base_url):
    """Overloading api_report does not block api_sync."""
    try:
        set_map(RATE_LIMITS_MAP, "api_report", "9999")
        set_map(RATE_LIMITS_MAP, "api_sync", "9999")
        set_map(OVERLOAD_LIMITS_MAP, "api_report", "2")
        time.sleep(0.2)

        # Overload api_report
        for _ in range(6):
            requests.get(
                f"{base_url}/v3/report/test",
                headers={"Host": API_HOST},
                verify=False, timeout=5,
            )

        # api_sync should still work
        r = requests.get(
            f"{base_url}/v3/sync-api/test",
            headers={"Host": API_HOST},
            verify=False, timeout=5,
        )
        assert r.status_code == 200
    finally:
        restore_rate_limits()
        restore_overload_limits()


def test_overload_recovery(base_url):
    """After the 1 s rate window expires, the overload counter resets."""
    try:
        set_map(RATE_LIMITS_MAP, "api_report", "9999")
        set_map(OVERLOAD_LIMITS_MAP, "api_report", "2")
        time.sleep(0.2)

        # Trigger overload
        for _ in range(5):
            requests.get(
                f"{base_url}/v3/report/test",
                headers={"Host": API_HOST},
                verify=False, timeout=5,
            )

        # Wait well beyond the 1 s window for the sliding counter to drain.
        # HAProxy's rate counter uses internal period granularity, so give
        # extra headroom.
        time.sleep(3)

        r = requests.get(
            f"{base_url}/v3/report/test",
            headers={"Host": API_HOST},
            verify=False, timeout=5,
        )
        assert r.status_code == 200
    finally:
        restore_rate_limits()
        restore_overload_limits()
